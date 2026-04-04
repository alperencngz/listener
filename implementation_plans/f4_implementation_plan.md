# Implementation Plan: F4 — Full-Text Search Across Meetings

## Pre-Implementation Checklist

Files the implementor **must read fresh** before starting (these are shared files modified by F1, F2, F3, F5, F8, F9, F10):

- `/Users/alperencngzz/Desktop/listener/listener/web/app.py` — Flask backend (609 lines as of F5)
- `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html` — Single-file frontend (~1510 lines)
- `/Users/alperencngzz/Desktop/listener/pyproject.toml` — Dependencies (no new deps needed for F4)
- `/Users/alperencngzz/Desktop/listener/listener/cli.py` — CLI commands

Things to verify:
- `sqlite3` is part of Python stdlib — no pip install needed
- `~/.listener/` directory pattern already used by webhooks (F10) for `config.yaml`
- The `transcripts/` directory (relative, `./transcripts/`) is the OUTPUT_DIR in app.py

## Dependencies

**No new dependencies required.** `sqlite3` is in Python's standard library. FTS5 is compiled into the default SQLite that ships with Python 3.11+.

Verify with:
```bash
python3 -c "import sqlite3; conn = sqlite3.connect(':memory:'); conn.execute('CREATE VIRTUAL TABLE t USING fts5(a)'); print('FTS5 OK')"
```

## Implementation Tasks

---

### Task 1: Create `listener/db.py` — SQLite FTS5 Database Module

- **File:** `/Users/alperencngzz/Desktop/listener/listener/db.py`
- **Action:** CREATE
- **Details:** This is the core module. It manages the SQLite database at `~/.listener/listener.db`, creates the schema, and provides CRUD + search functions.

```python
"""SQLite FTS5 search index for meetings.

DB location: ~/.listener/listener.db
"""

import json
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".listener" / "listener.db"

_conn: sqlite3.Connection | None = None


def get_db() -> sqlite3.Connection:
    """Return a module-level SQLite connection, creating the DB and schema if needed."""
    global _conn
    if _conn is not None:
        return _conn

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
    _conn = conn
    return conn


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
        transcript = tf.read_text()

        # Read analysis if available
        analysis = ""
        analysis_file = transcripts_dir / f"{session_id}_analysis.md"
        if analysis_file.exists():
            analysis = analysis_file.read_text()

        # Read meta if available
        title = ""
        duration = 0.0
        language = ""
        lang_confidence = 0.0
        meta_file = transcripts_dir / f"{session_id}_meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text())
                title = meta.get("title", "")
                duration = meta.get("duration", 0.0)
                language = meta.get("language", "")
                lang_confidence = meta.get("language_probability", 0.0)
            except Exception:
                pass

        # Derive date from session_id (format: YYYY-MM-DD_HH-MM-SS)
        date = session_id.replace("_", "T").replace("-", ":", 2)
        # Fix: session_id is like 2026-04-04_20-33-25
        # We want ISO date: 2026-04-04T20:33:25
        parts = session_id.split("_")
        date = parts[0]  # just the date part is fine
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
```

- **Verification:**
```bash
cd /Users/alperencngzz/Desktop/listener && python3 -c "from listener.db import get_db, search_meetings; get_db(); print('db.py OK')"
```

---

### Task 2: Add Search API Endpoint to Flask App

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/app.py`
- **Action:** MODIFY
- **Details:** Add a `/api/search` GET endpoint and integrate DB indexing into `_process_recording`. Also add backfill call on module load.

#### 2a: Add search endpoint

Add this section **after the Webhooks API section** (after line ~373, before the Live Transcription SSE section):

```python
# ---------------------------------------------------------------------------
# Full-Text Search (F4)
# ---------------------------------------------------------------------------

@app.route("/api/search")
def api_search():
    """Search across all meeting transcripts, analyses, and titles."""
    from listener.db import search_meetings
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    limit = request.args.get("limit", 20, type=int)
    results = search_meetings(q, limit=min(limit, 50))
    return jsonify(results)
```

#### 2b: Index new meetings after processing

In the `_process_recording` function, **after** the meta.json is saved (after the line `(OUTPUT_DIR / f"{session_id}_meta.json").write_text(...)` which is around line 514) and **after** the analysis block completes (after the webhook section, just before the final `_state["status"] = "done"` block), add the DB indexing call:

Find this code block (near the end of `_process_recording`, right before the final state update):

```python
        # F10: Fire webhooks asynchronously
        try:
            from listener.webhooks import fire_webhooks
```

**Before** the webhook block, add:

```python
        # F4: Index in search database
        try:
            from listener.db import insert_meeting
            # Derive ISO date from session_id
            parts = session_id.split("_")
            iso_date = parts[0]
            if len(parts) > 1:
                iso_date = f"{parts[0]}T{parts[1].replace('-', ':')}"

            # Read analysis text if available
            analysis_text = ""
            if "analysis" in files:
                try:
                    analysis_text = (OUTPUT_DIR / files["analysis"]).read_text()
                except Exception:
                    pass

            insert_meeting(
                session_id=session_id,
                title=title,
                date=iso_date,
                duration=result.duration,
                language=result.language,
                lang_confidence=result.language_probability,
                transcript=transcript_text,
                analysis=analysis_text,
                audio_path=audio_path,
            )
            logger.info("Indexed session %s in search DB", session_id)
        except Exception as e:
            logger.warning("Search indexing failed: %s", e)
```

#### 2c: Backfill on startup

In the `run()` function at the bottom of app.py, add the backfill call. Find:

```python
def run(port=8642, debug=False):
    """Start the web server."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=port, debug=debug, threaded=True)
```

Replace with:

```python
def run(port=8642, debug=False):
    """Start the web server."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # F4: Backfill any existing sessions into search index
    try:
        from listener.db import backfill_from_transcripts
        count = backfill_from_transcripts(OUTPUT_DIR)
        if count:
            logger.info("Backfilled %d sessions into search index", count)
    except Exception as e:
        logger.warning("Search index backfill failed: %s", e)

    app.run(host="127.0.0.1", port=port, debug=debug, threaded=True)
```

- **Verification:**
```bash
cd /Users/alperencngzz/Desktop/listener && python3 -c "from listener.web.app import app; print('app.py imports OK')"
```

---

### Task 3: Add Search UI to Frontend

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** MODIFY
- **Details:** Add a search bar above the sessions list and JS to call the search API. Three changes: CSS, HTML, and JavaScript.

#### 3a: CSS — Add search styles

Add these styles inside the `<style>` tag. A good location is **after the `.badge.au` rule** (around line 229), before the viewer styles:

```css
/* ---- Search (F4) ---- */
.search-bar{
  display:flex;gap:8px;margin-bottom:12px;
}
.search-bar input{
  flex:1;padding:8px 12px;border:1px solid #e2e8f0;border-radius:8px;
  font-size:13px;outline:none;transition:border .2s;
}
.search-bar input:focus{border-color:#6366f1}
.search-bar button{
  padding:8px 14px;border:none;border-radius:8px;
  background:#6366f1;color:#fff;font-size:13px;font-weight:600;
  cursor:pointer;white-space:nowrap;transition:background .2s;
}
.search-bar button:hover{background:#4f46e5}
.search-bar .clear-btn{
  background:#e2e8f0;color:#64748b;
}
.search-bar .clear-btn:hover{background:#cbd5e1}

.search-results .sr-item{
  padding:10px 12px;border-bottom:1px solid #f1f5f9;cursor:pointer;
  transition:background .15s;
}
.search-results .sr-item:hover{background:#f8fafc}
.search-results .sr-title{font-size:14px;font-weight:600;color:#1e293b}
.search-results .sr-meta{font-size:12px;color:#94a3b8;margin-top:2px}
.search-results .sr-snippet{
  font-size:12px;color:#475569;margin-top:4px;
  line-height:1.5;
}
.search-results .sr-snippet mark{
  background:#fef08a;color:#1e293b;border-radius:2px;
  padding:0 1px;
}
.search-results .sr-empty{
  text-align:center;color:#94a3b8;font-size:13px;padding:20px 0;
}
.search-results .sr-hint{
  text-align:center;color:#b0b8c4;font-size:12px;padding:12px 0;
}
```

#### 3b: HTML — Add search bar in the sessions card

Find this block in the HTML (around line 525-531):

```html
  <!-- Sessions -->
  <div class="card">
    <div class="card-label">Past Sessions</div>
    <ul class="slist" id="slist">
      <li class="empty">No sessions yet</li>
    </ul>
  </div>
```

Replace it with:

```html
  <!-- Sessions -->
  <div class="card">
    <div class="card-label">Past Sessions</div>
    <div class="search-bar">
      <input type="text" id="search-input" placeholder="Search all meetings..." onkeydown="if(event.key==='Enter')doSearch()">
      <button onclick="doSearch()">Search</button>
      <button class="clear-btn" id="search-clear" onclick="clearSearch()" style="display:none">Clear</button>
    </div>
    <div class="search-results" id="search-results" style="display:none"></div>
    <ul class="slist" id="slist">
      <li class="empty">No sessions yet</li>
    </ul>
  </div>
```

#### 3c: JavaScript — Add search functions

Add the following JS block **after the Sessions list section** (after the `loadSessions` function which ends around line 1437, before the Click-to-Seek section):

```javascript
// ===========================================================================
// Full-Text Search (F4)
// ===========================================================================
async function doSearch(){
  const input = Q('#search-input');
  const query = input.value.trim();
  if(!query){clearSearch();return}

  const res = Q('#search-results');
  const slist = Q('#slist');
  const clearBtn = Q('#search-clear');

  try{
    const r = await f('/api/search?q='+encodeURIComponent(query));
    const results = await r.json();

    clearBtn.style.display='inline-block';
    res.style.display='block';
    slist.style.display='none';

    if(!results.length){
      res.innerHTML='<div class="sr-empty">No results found for "'+esc(query)+'"</div>'+
        '<div class="sr-hint">Try: phrases in quotes, OR for alternatives, prefix* for wildcards</div>';
      return;
    }

    res.innerHTML = results.map(r=>{
      const title = r.title || r.session_id;
      const date = r.date || r.session_id.split('_')[0];
      const dur = r.duration ? ' &middot; '+fmtDur(r.duration) : '';
      const lang = r.language ? ' &middot; '+r.language.toUpperCase() : '';
      return `<div class="sr-item" onclick="searchClickSession('${esc(r.session_id)}')">
        <div class="sr-title">${esc(title)}</div>
        <div class="sr-meta">${esc(date)}${dur}${lang}</div>
        ${r.snippet ? '<div class="sr-snippet">...'+r.snippet+'...</div>' : ''}
      </div>`;
    }).join('');
  }catch(e){
    console.error('Search failed:',e);
    res.innerHTML='<div class="sr-empty">Search failed</div>';
    res.style.display='block';
    slist.style.display='none';
    clearBtn.style.display='inline-block';
  }
}

function clearSearch(){
  Q('#search-input').value='';
  Q('#search-results').style.display='none';
  Q('#search-results').innerHTML='';
  Q('#slist').style.display='';
  Q('#search-clear').style.display='none';
}

async function searchClickSession(sessionId){
  // Load the full session data from /api/sessions and open it
  try{
    const r = await f('/api/sessions');
    const sessions = await r.json();
    const s = sessions.find(s=>s.id===sessionId);
    if(s){
      clearSearch();
      viewSess(s);
    }else{
      // Session found in search but not in listing — open with minimal info
      clearSearch();
      viewSess({id:sessionId,files:{transcript:sessionId+'_transcript.md'},title:sessionId});
    }
  }catch(e){
    console.error('Could not load session:',e);
  }
}
```

- **Verification:** Start the web server and visit `http://127.0.0.1:8642`. The search bar should appear above the session list. Typing a query and pressing Enter (or clicking Search) should show results or "No results found".

---

### Task 4: Add CLI search command (optional enhancement)

- **File:** `/Users/alperencngzz/Desktop/listener/listener/cli.py`
- **Action:** MODIFY
- **Details:** Add a `search` command to the CLI for searching from the terminal.

Read cli.py first to find the Click group structure. Add the following command in the CLI commands section:

```python
@cli.command()
@click.argument("query")
@click.option("--limit", "-n", default=10, help="Max results to return")
def search(query, limit):
    """Search across all meeting transcripts."""
    from listener.db import search_meetings, backfill_from_transcripts
    from pathlib import Path

    # Backfill first to ensure index is current
    transcripts_dir = Path("./transcripts")
    backfill_from_transcripts(transcripts_dir)

    results = search_meetings(query, limit=limit)
    if not results:
        click.echo("No results found.")
        return

    for i, r in enumerate(results, 1):
        title = r["title"] or r["session_id"]
        click.echo(f"\n{i}. {title}")
        click.echo(f"   Date: {r['date']}  Duration: {r['duration']:.0f}s  Lang: {r['language']}")
        if r.get("snippet"):
            # Strip HTML tags for terminal display
            import re
            snippet = re.sub(r'<[^>]+>', '', r["snippet"])
            click.echo(f"   ...{snippet}...")
```

- **Verification:**
```bash
cd /Users/alperencngzz/Desktop/listener && python3 -m listener search "test" 2>/dev/null; echo "exit code: $?"
```

---

## Summary of All File Changes

| File | Action | What |
|---|---|---|
| `listener/db.py` | **CREATE** | SQLite FTS5 module (get_db, insert_meeting, search_meetings, backfill) |
| `listener/web/app.py` | **MODIFY** | Add `/api/search` endpoint, index in `_process_recording`, backfill in `run()` |
| `listener/web/templates/index.html` | **MODIFY** | Search bar CSS, HTML, and JS |
| `listener/cli.py` | **MODIFY** | Add `search` CLI command |

## Final Verification

Run these commands in order:

```bash
# 1. Verify db.py imports and FTS5 works
cd /Users/alperencngzz/Desktop/listener
python3 -c "from listener.db import get_db, search_meetings, backfill_from_transcripts; get_db(); print('DB OK')"

# 2. Verify app.py imports cleanly
python3 -c "from listener.web.app import app; print('App OK')"

# 3. Verify CLI search command exists
python3 -m listener search --help

# 4. Test backfill (indexes existing transcripts)
python3 -c "
from pathlib import Path
from listener.db import backfill_from_transcripts
count = backfill_from_transcripts(Path('./transcripts'))
print(f'Backfilled {count} sessions')
"

# 5. Test search (may return empty if no transcripts exist)
python3 -c "
from listener.db import search_meetings
results = search_meetings('meeting')
print(f'Found {len(results)} results')
for r in results[:3]:
    print(f'  - {r[\"session_id\"]}: {r[\"title\"][:50]}')
"

# 6. Start web server and verify search endpoint
# In terminal: python3 -m listener web
# In browser: http://127.0.0.1:8642
# - Search bar should be visible above "Past Sessions"
# - Type a query and press Enter
# - Results should show with highlighted snippets
# - Click a result to open the session viewer
# - Click "Clear" to return to normal session list
```

## Edge Cases & Notes

1. **FTS5 query syntax errors:** The `search_meetings` function catches `sqlite3.OperationalError` and returns `[]` for malformed queries (e.g., unmatched quotes). The user just sees "No results found."

2. **Concurrent access:** WAL mode (`PRAGMA journal_mode=WAL`) allows concurrent reads from the Flask thread and background processing thread. The `check_same_thread=False` flag is necessary since Flask is threaded.

3. **INSERT OR REPLACE:** Using `INSERT OR REPLACE` means re-processing a session updates the index automatically. The FTS triggers handle the delete+re-insert.

4. **Turkish/multilingual support:** The `unicode61 remove_diacritics 2` tokenizer strips diacritics, so searching "gorusme" matches "görüşme". This is critical for the project's multilingual use case.

5. **Backfill idempotency:** `backfill_from_transcripts` checks `meeting_exists()` before inserting, so it's safe to call multiple times (on every server start).

6. **No dependency on previous features:** F4 is self-contained. It reads whatever files exist in `transcripts/` (transcript, analysis, meta.json, audio). If diarization (F1) or analytics (F8) weren't run, those fields are simply empty.
