"""SQLite FTS5 search index for meetings.

DB location: ~/.listener/listener.db
"""

import json
import os
import sqlite3
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# LISTENER_DB_PATH lets a smoke run or a second install use its own database.
DB_PATH = Path(os.environ["LISTENER_DB_PATH"]).expanduser() if os.environ.get("LISTENER_DB_PATH") else Path.home() / ".listener" / "listener.db"

_conn: sqlite3.Connection | None = None

# One shared connection is used from Flask request threads and background
# worker threads. ``get_db()`` returns a ``LockedConnection`` that runs every
# single call under this lock; hold DB_LOCK yourself around any multi-statement
# read-modify-write sequence so it cannot interleave with another thread.
DB_LOCK = threading.RLock()


class _Rows:
    """The fully fetched result of one statement.

    Rows are read while ``DB_LOCK`` is held, so consuming them later (outside
    the lock, possibly from another thread) never touches the connection.
    """

    __slots__ = ("_rows", "_pos", "lastrowid", "rowcount")

    def __init__(self, cursor: sqlite3.Cursor):
        self._rows = cursor.fetchall() if cursor.description else []
        self._pos = 0
        self.lastrowid = cursor.lastrowid
        self.rowcount = cursor.rowcount

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self) -> list:
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class LockedConnection:
    """A ``sqlite3.Connection`` whose every call runs under ``DB_LOCK``.

    One connection is shared by Flask request threads, the transcription run
    thread and the Claude worker. Python's sqlite3 module must not be driven
    from two threads at once (it fails with "bad parameter or other API
    misuse"), so each statement is executed *and fully fetched* while the lock
    is held. Multi-statement read-modify-write sequences still wrap themselves
    in ``with DB_LOCK:`` (it is re-entrant) to stay atomic.
    """

    def __init__(self, conn: sqlite3.Connection):
        object.__setattr__(self, "_conn", conn)

    def execute(self, sql: str, params=()) -> _Rows:
        with DB_LOCK:
            return _Rows(self._conn.execute(sql, params))

    def executemany(self, sql: str, seq) -> _Rows:
        with DB_LOCK:
            return _Rows(self._conn.executemany(sql, seq))

    def executescript(self, script: str) -> None:
        with DB_LOCK:
            self._conn.executescript(script)

    def commit(self) -> None:
        with DB_LOCK:
            self._conn.commit()

    def rollback(self) -> None:
        with DB_LOCK:
            self._conn.rollback()

    def close(self) -> None:
        with DB_LOCK:
            self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)


def get_db() -> sqlite3.Connection:
    """Return the shared (lock-guarded) SQLite connection, creating the DB and schema if needed."""
    global _conn
    if _conn is not None:
        return _conn
    with DB_LOCK:
        if _conn is not None:
            return _conn
        _conn = LockedConnection(_open_db())
        return _conn


def _open_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # better concurrent read perf

    # Create schema
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meetings (
            session_id      TEXT PRIMARY KEY,
            title           TEXT NOT NULL DEFAULT '',
            date            TEXT NOT NULL,
            duration        REAL DEFAULT 0,
            language        TEXT DEFAULT '',
            lang_confidence REAL DEFAULT 0,
            transcript      TEXT NOT NULL DEFAULT '',
            analysis        TEXT DEFAULT '',
            audio_path      TEXT DEFAULT '',
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS meetings_fts USING fts5(
            title,
            transcript,
            analysis,
            content=meetings,
            content_rowid=rowid,
            tokenize='unicode61 remove_diacritics 2'
        );

        -- Triggers to keep FTS in sync
        CREATE TRIGGER IF NOT EXISTS meetings_ai AFTER INSERT ON meetings BEGIN
            INSERT INTO meetings_fts(rowid, title, transcript, analysis)
            VALUES (new.rowid, new.title, new.transcript, new.analysis);
        END;

        CREATE TRIGGER IF NOT EXISTS meetings_ad AFTER DELETE ON meetings BEGIN
            INSERT INTO meetings_fts(meetings_fts, rowid, title, transcript, analysis)
            VALUES ('delete', old.rowid, old.title, old.transcript, old.analysis);
        END;

        CREATE TRIGGER IF NOT EXISTS meetings_au AFTER UPDATE ON meetings BEGIN
            INSERT INTO meetings_fts(meetings_fts, rowid, title, transcript, analysis)
            VALUES ('delete', old.rowid, old.title, old.transcript, old.analysis);
            INSERT INTO meetings_fts(rowid, title, transcript, analysis)
            VALUES (new.rowid, new.title, new.transcript, new.analysis);
        END;
    """)
    conn.commit()
    return conn


def reset_db() -> None:
    """Close the module-level connection (tests swap DB_PATH and call this)."""
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    _conn = None


def insert_meeting(
    session_id: str,
    title: str = "",
    date: str = "",
    duration: float = 0,
    language: str = "",
    lang_confidence: float = 0,
    transcript: str = "",
    analysis: str = "",
    audio_path: str = "",
) -> None:
    """Insert or replace a meeting in the DB (and FTS index via trigger)."""
    conn = get_db()
    with DB_LOCK:
        conn.execute(
            """INSERT OR REPLACE INTO meetings
               (session_id, title, date, duration, language, lang_confidence,
                transcript, analysis, audio_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, title, date, duration, language, lang_confidence,
             transcript, analysis, audio_path),
        )
        conn.commit()


def update_meeting_analysis(session_id: str, analysis: str) -> None:
    """Update just the analysis column for an existing meeting."""
    conn = get_db()
    with DB_LOCK:
        conn.execute(
            "UPDATE meetings SET analysis = ? WHERE session_id = ?",
            (analysis, session_id),
        )
        conn.commit()


def search_meetings(query: str, limit: int = 20) -> list[dict]:
    """Full-text search across all indexed meetings.

    Returns a list of dicts with: session_id, title, date, duration,
    language, rank (BM25 score), snippet (highlighted match from transcript).

    BM25 weights: title=10.0, transcript=1.0, analysis=5.0
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT m.session_id, m.title, m.date, m.duration, m.language,
                bm25(meetings_fts, 10.0, 1.0, 5.0) AS rank,
                snippet(meetings_fts, 1, '<mark>', '</mark>', '...', 40) AS snippet
            FROM meetings_fts
            JOIN meetings m ON m.rowid = meetings_fts.rowid
            WHERE meetings_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, limit)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError as e:
        # Bad FTS query syntax — return empty rather than crash
        logger.warning("FTS5 query error for %r: %s", query, e)
        return []


def meeting_exists(session_id: str) -> bool:
    """Check if a session is already indexed."""
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM meetings WHERE session_id = ?", (session_id,)
    ).fetchone()
    return row is not None


def backfill_from_transcripts(transcripts_dir: Path) -> int:
    """Scan transcripts/ directory and index any sessions not yet in the DB.

    Returns the number of newly indexed sessions.
    """
    if not transcripts_dir.exists():
        return 0

    count = 0
    # Find all transcript files
    for tf in sorted(transcripts_dir.glob("*_transcript.md")):
        session_id = tf.stem.replace("_transcript", "")
        if meeting_exists(session_id):
            continue

        # Read transcript
        transcript = tf.read_text(encoding="utf-8")

        # Read analysis if available
        analysis = ""
        analysis_file = transcripts_dir / f"{session_id}_analysis.md"
        if analysis_file.exists():
            analysis = analysis_file.read_text(encoding="utf-8")

        # Read meta if available
        title = ""
        duration = 0.0
        language = ""
        lang_confidence = 0.0
        meta_file = transcripts_dir / f"{session_id}_meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                title = meta.get("title", "")
                duration = meta.get("duration", 0.0)
                language = meta.get("language", "")
                lang_confidence = meta.get("language_probability", 0.0)
            except Exception:
                pass

        # Derive date from session_id (format: YYYY-MM-DD_HH-MM-SS)
        parts = session_id.split("_")
        date = parts[0]  # just the date part
        if len(parts) > 1:
            time_part = parts[1].replace("-", ":")
            date = f"{parts[0]}T{time_part}"

        audio_path = ""
        audio_file = transcripts_dir / f"{session_id}.wav"
        if audio_file.exists():
            audio_path = str(audio_file)

        insert_meeting(
            session_id=session_id,
            title=title,
            date=date,
            duration=duration,
            language=language,
            lang_confidence=lang_confidence,
            transcript=transcript,
            analysis=analysis,
            audio_path=audio_path,
        )
        count += 1
        logger.info("Backfilled session %s into search index", session_id)

    return count
