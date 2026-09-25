"""Persistent, manually controlled processing queue.

Every processing job (transcription, Claude analysis, memory generation) is a
row in the ``jobs`` table of the Listener SQLite DB, so queue state survives
page refreshes and app restarts. Nothing in this module starts work on its
own: jobs are created by explicit user actions and executed only when the
user starts a run (transcription lane) or triggers a Claude action.

Lanes
-----
* **Transcription lane** — jobs of kind ``transcribe``. ``JobRunner.start_run``
  snapshots the selected queued jobs and executes them one at a time in a
  single background thread. Jobs queued while a run is active are *not* picked
  up; they wait for the next explicit run. Only one run can be active.
* **Claude lane** — jobs of kind ``analyze`` / ``memory``. They are light
  (network calls) so ``JobRunner.start_claude_job`` executes them right away in
  a separate single worker thread, one at a time, in request order. After a
  restart, queued Claude jobs stay queued until the user runs them again.

Statuses
--------
``queued`` → ``running`` → ``done`` | ``failed`` | ``interrupted``.
``interrupted`` is set when the app restarts while a job was running or when
the user stops a run; it never restarts automatically.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections import deque
from datetime import datetime
from typing import Callable, Protocol

from listener.db import DB_LOCK, get_db

logger = logging.getLogger(__name__)

KINDS = ("transcribe", "analyze", "memory")
TRANSCRIPTION_KINDS = ("transcribe",)
CLAUDE_KINDS = ("analyze", "memory")
ACTIVE_STATUSES = ("queued", "running")
RETRYABLE_STATUSES = ("failed", "interrupted")
FINISHED_STATUSES = ("done", "failed", "interrupted")

INTERRUPTED_BY_RESTART = (
    "Interrupted: the app was restarted while this job was running. "
    "Retry to resume from the last checkpoint."
)
INTERRUPTED_BY_USER = "Stopped by user. Retry to resume from the last checkpoint."


class JobError(Exception):
    """Base error for queue operations (maps to HTTP 4xx in the web app)."""


class DuplicateJob(JobError):
    """An active job of the same kind already exists for this meeting."""


class MeetingBusy(JobError):
    """Another job is currently running for this meeting."""


class RunActive(JobError):
    """A transcription run is already in progress."""


class JobInterrupted(Exception):
    """Raised by an executor when it noticed a stop request and left a checkpoint."""


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_schema_conn = None  # connection the schema was last ensured on


def ensure_schema() -> None:
    """Create the jobs table once per connection.

    ``executescript`` implicitly COMMITs, so this must not run on every call:
    it would commit statements other threads have pending on the shared
    connection. Tests swap the DB, so the check is keyed on the connection.
    """
    global _schema_conn
    conn = get_db()
    if conn is _schema_conn:
        return
    with DB_LOCK:
        if conn is _schema_conn:
            return
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                kind        TEXT NOT NULL,
                status      TEXT NOT NULL,
                stage       TEXT NOT NULL DEFAULT '',
                options     TEXT NOT NULL DEFAULT '{}',
                error       TEXT NOT NULL DEFAULT '',
                result      TEXT NOT NULL DEFAULT '{}',
                progress    TEXT NOT NULL DEFAULT '{}',
                run_id      TEXT NOT NULL DEFAULT '',
                attempts    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                started_at  TEXT,
                finished_at TEXT
            );
            CREATE INDEX IF NOT EXISTS jobs_session_idx ON jobs(session_id);
            CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
        """)
        conn.commit()
        _schema_conn = conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_job(row) -> dict:
    job = dict(row)
    job.pop("seq", None)  # insertion order helper, not part of the API
    for key in ("options", "result", "progress"):
        try:
            job[key] = json.loads(job.get(key) or "{}")
        except Exception:
            job[key] = {}
    return job


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def enqueue(session_id: str, kind: str, options: dict | None = None) -> dict:
    """Create a queued job. Refuses duplicates and busy meetings."""
    if kind not in KINDS:
        raise JobError(f"Unknown job kind: {kind}")
    ensure_schema()
    conn = get_db()
    with DB_LOCK:
        dup = conn.execute(
            "SELECT id, status FROM jobs WHERE session_id = ? AND kind = ? "
            "AND status IN ('queued', 'running') LIMIT 1",
            (session_id, kind),
        ).fetchone()
        if dup:
            raise DuplicateJob(
                f"A {kind} job for this meeting is already {dup['status']}."
            )
        busy = conn.execute(
            "SELECT kind FROM jobs WHERE session_id = ? AND status = 'running' LIMIT 1",
            (session_id,),
        ).fetchone()
        if busy:
            raise MeetingBusy(f"A {busy['kind']} job is currently running for this meeting.")
        job_id = uuid.uuid4().hex[:12]
        now = _now()
        conn.execute(
            "INSERT INTO jobs (id, session_id, kind, status, options, created_at, updated_at) "
            "VALUES (?, ?, ?, 'queued', ?, ?, ?)",
            (job_id, session_id, kind, json.dumps(options or {}, ensure_ascii=False), now, now),
        )
        conn.commit()
    return get_job(job_id)


def get_job(job_id: str) -> dict | None:
    ensure_schema()
    row = get_db().execute("SELECT rowid AS seq, * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(
    *,
    status: str | tuple[str, ...] | None = None,
    session_id: str | None = None,
    kind: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Newest first."""
    ensure_schema()
    clauses, params = [], []
    if status:
        statuses = (status,) if isinstance(status, str) else tuple(status)
        clauses.append(f"status IN ({','.join('?' * len(statuses))})")
        params.extend(statuses)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = get_db().execute(
        f"SELECT rowid AS seq, * FROM jobs {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [_row_to_job(r) for r in rows]


def _queued_transcription_jobs() -> list[dict]:
    """Queued transcription jobs, oldest first (insertion order)."""
    rows = get_db().execute(
        "SELECT rowid AS seq, * FROM jobs WHERE status = 'queued' AND kind = 'transcribe' "
        "ORDER BY created_at ASC, rowid ASC"
    ).fetchall()
    return [_row_to_job(r) for r in rows]


def jobs_by_session() -> dict[str, dict]:
    """Map session_id -> most relevant job (running > queued > failed/interrupted > done)."""
    rank = {"running": 0, "queued": 1, "failed": 2, "interrupted": 2, "done": 3}
    out: dict[str, dict] = {}
    for job in list_jobs(limit=1000):
        cur = out.get(job["session_id"])
        if cur is None or rank.get(job["status"], 9) < rank.get(cur["status"], 9):
            out[job["session_id"]] = job
    return out


def remove_job(job_id: str) -> bool:
    """Delete a job that is not running. Returns False if missing; raises if running."""
    ensure_schema()
    conn = get_db()
    with DB_LOCK:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return False
        if row["status"] == "running":
            raise JobError("Cannot remove a running job. Stop the run first.")
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
    return True


def retry_job(job_id: str) -> dict:
    """Put a failed/interrupted job back in the queue (options unchanged)."""
    ensure_schema()
    conn = get_db()
    with DB_LOCK:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise JobError("Job not found.")
        if row["status"] not in RETRYABLE_STATUSES:
            raise JobError(f"Only failed or interrupted jobs can be retried (status: {row['status']}).")
        conn.execute(
            "UPDATE jobs SET status = 'queued', stage = '', error = '', run_id = '', progress = '{}', "
            "started_at = NULL, finished_at = NULL, updated_at = ? WHERE id = ?",
            (_now(), job_id),
        )
        conn.commit()
    return get_job(job_id)


def clear_finished(statuses: tuple[str, ...] = ("done",)) -> int:
    ensure_schema()
    conn = get_db()
    with DB_LOCK:
        cur = conn.execute(
            f"DELETE FROM jobs WHERE status IN ({','.join('?' * len(statuses))})", statuses
        )
        conn.commit()
        return cur.rowcount


def _update(job_id: str, **fields) -> None:
    conn = get_db()
    fields["updated_at"] = _now()
    for key in ("result", "progress", "options"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with DB_LOCK:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))
        conn.commit()


def claim_job(job_id: str, run_id: str = "") -> str | None:
    """Atomically move a queued job to ``running``.

    Returns ``None`` on success, or a reason string when the job cannot start:
    it is no longer queued, or another job for the same meeting is running
    (two jobs must never write one meeting's files at the same time).
    """
    conn = get_db()
    with DB_LOCK:
        row = conn.execute("SELECT session_id, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return "Job no longer exists."
        if row["status"] != "queued":
            return f"Job is {row['status']}, not queued."
        busy = conn.execute(
            "SELECT kind FROM jobs WHERE session_id = ? AND status = 'running' AND id != ? LIMIT 1",
            (row["session_id"], job_id),
        ).fetchone()
        if busy:
            return (
                f"Skipped: this meeting's {busy['kind']} job was still running. "
                "Retry once it has finished."
            )
        conn.execute(
            "UPDATE jobs SET status = 'running', stage = 'starting', error = '', run_id = ?, progress = '{}', "
            "attempts = attempts + 1, started_at = ?, finished_at = NULL, updated_at = ? WHERE id = ?",
            (run_id, _now(), _now(), job_id),
        )
        conn.commit()
    return None


def mark_running(job_id: str, run_id: str = "") -> None:
    """Unconditional variant of :func:`claim_job` (kept for callers that already checked)."""
    reason = claim_job(job_id, run_id)
    if reason:
        raise JobError(reason)


def set_stage(job_id: str, stage: str) -> None:
    _update(job_id, stage=stage)


def set_progress(job_id: str, progress: dict) -> None:
    _update(job_id, progress=progress)


def mark_done(job_id: str, result: dict | None = None) -> None:
    _update(job_id, status="done", stage="", result=result or {}, finished_at=_now())


def mark_failed(job_id: str, error: str) -> None:
    _update(job_id, status="failed", stage="", error=(error or "Unknown error")[:4000], finished_at=_now())


def mark_interrupted(job_id: str, reason: str = INTERRUPTED_BY_USER) -> None:
    _update(job_id, status="interrupted", stage="", error=reason, finished_at=_now())


def recover_on_startup() -> int:
    """Mark jobs left 'running' by a previous process as interrupted. Never restarts them."""
    ensure_schema()
    conn = get_db()
    with DB_LOCK:
        cur = conn.execute(
            "UPDATE jobs SET status = 'interrupted', stage = '', error = ?, finished_at = ?, updated_at = ? "
            "WHERE status = 'running'",
            (INTERRUPTED_BY_RESTART, _now(), _now()),
        )
        conn.commit()
        n = cur.rowcount
    if n:
        logger.warning("Marked %d job(s) as interrupted after restart", n)
    return n


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class JobContext(Protocol):
    def set_stage(self, stage: str) -> None: ...
    def set_progress(self, progress: dict) -> None: ...
    def should_stop(self) -> bool: ...


class _Context:
    def __init__(self, job_id: str, cancel: threading.Event, on_stage: Callable[[str], None]):
        self.job_id = job_id
        self._cancel = cancel
        self._on_stage = on_stage

    def set_stage(self, stage: str) -> None:
        set_stage(self.job_id, stage)
        self._on_stage(stage)

    def set_progress(self, progress: dict) -> None:
        set_progress(self.job_id, progress)

    def should_stop(self) -> bool:
        return self._cancel.is_set()


Executor = Callable[[dict, JobContext], dict]


class JobRunner:
    """Executes jobs. One transcription run at a time; one Claude job at a time."""

    def __init__(self, executors: dict[str, Executor]):
        self._executors = executors
        self._lock = threading.Lock()
        # transcription lane
        self._run: dict | None = None
        self._run_thread: threading.Thread | None = None
        self._cancel = threading.Event()
        # claude lane
        self._claude_pending: deque[str] = deque()
        self._claude_thread: threading.Thread | None = None
        self._claude_current: dict | None = None

    # -- status ----------------------------------------------------------

    def run_status(self) -> dict:
        with self._lock:
            if not self._run:
                return {"active": False}
            return dict(self._run, active=True, stopping=self._cancel.is_set())

    def claude_status(self) -> dict:
        with self._lock:
            pending = list(self._claude_pending)
            if not self._claude_current:
                return {"active": False, "pending_job_ids": pending}
            return dict(self._claude_current, active=True, pending_job_ids=pending)

    def transcription_active(self) -> bool:
        with self._lock:
            return self._run is not None

    # -- transcription lane -----------------------------------------------

    def start_run(self, job_ids: list[str] | None = None) -> dict:
        """Snapshot queued transcription jobs and execute them sequentially.

        ``job_ids`` limits the run to those jobs (Run selected); ``None`` runs
        every queued transcription job at this moment (Run queued).
        """
        ensure_schema()
        queued = _queued_transcription_jobs()
        if job_ids is not None:
            if not all(isinstance(jid, str) and jid for jid in job_ids):
                raise JobError("job_ids must be a list of job id strings.")
            wanted = list(dict.fromkeys(job_ids))  # de-duplicate, keep order
            by_id = {j["id"]: j for j in queued}
            missing = [jid for jid in wanted if jid not in by_id]
            if missing:
                raise JobError(f"Job(s) not queued for transcription: {', '.join(missing)}")
            selected = [by_id[jid] for jid in wanted]
        else:
            selected = queued
        if not selected:
            raise JobError("Nothing to run: the transcription queue is empty.")

        with self._lock:
            if self._run is not None:
                raise RunActive("A transcription run is already in progress.")
            run_id = uuid.uuid4().hex[:8]
            self._cancel.clear()
            self._run = {
                "run_id": run_id,
                "job_ids": [j["id"] for j in selected],
                "current_job_id": None,
                "current_session_id": None,
                "stage": None,
                "started_at": _now(),
                "completed": 0,
                "total": len(selected),
            }
            thread = threading.Thread(
                target=self._run_loop, args=(run_id, [j["id"] for j in selected]),
                name=f"listener-run-{run_id}", daemon=True,
            )
            self._run_thread = thread
            snapshot = dict(self._run)
        thread.start()
        return snapshot

    def stop_run(self) -> bool:
        with self._lock:
            if self._run is None:
                return False
            self._cancel.set()
        return True

    def _run_loop(self, run_id: str, job_ids: list[str]) -> None:
        try:
            for job_id in job_ids:
                if self._cancel.is_set():
                    break
                job = get_job(job_id)
                if not job or job["status"] != "queued":
                    continue  # removed or changed meanwhile
                self._execute(job, run_id, lane="run")
                with self._lock:
                    if self._run:
                        self._run["completed"] += 1
        finally:
            with self._lock:
                self._run = None
                self._run_thread = None
                self._cancel.clear()

    # -- claude lane --------------------------------------------------------

    def start_claude_job(self, job_id: str) -> dict:
        job = get_job(job_id)
        if not job:
            raise JobError("Job not found.")
        if job["kind"] not in CLAUDE_KINDS:
            raise JobError(f"{job['kind']} jobs run through the transcription queue.")
        if job["status"] != "queued":
            raise JobError(f"Job is {job['status']}, not queued.")
        with self._lock:
            if job_id in self._claude_pending:
                return job
            self._claude_pending.append(job_id)
            if self._claude_thread is None or not self._claude_thread.is_alive():
                self._claude_thread = threading.Thread(
                    target=self._claude_loop, name="listener-claude", daemon=True
                )
                self._claude_thread.start()
        return job

    def _claude_loop(self) -> None:
        while True:
            with self._lock:
                if not self._claude_pending:
                    self._claude_thread = None
                    return
                job_id = self._claude_pending.popleft()
            job = get_job(job_id)
            if not job or job["status"] != "queued":
                continue
            self._execute(job, run_id="", lane="claude")

    # -- shared -------------------------------------------------------------

    def _execute(self, job: dict, run_id: str, lane: str) -> None:
        job_id = job["id"]
        executor = self._executors.get(job["kind"])

        def on_stage(stage: str) -> None:
            with self._lock:
                target = self._run if lane == "run" else self._claude_current
                if target is not None:
                    target["stage"] = stage

        # Claim first: refuses if another job for the same meeting is running.
        reason = claim_job(job_id, run_id)
        if reason:
            if reason.startswith("Skipped"):
                logger.warning("Job %s (%s) not started: %s", job_id, job["kind"], reason)
                mark_failed(job_id, reason)
            return

        with self._lock:
            info = {
                "current_job_id": job_id, "current_session_id": job["session_id"],
                "kind": job["kind"], "stage": "starting",
            }
            if lane == "run" and self._run is not None:
                self._run.update(info)
            elif lane == "claude":
                self._claude_current = info

        cancel = self._cancel if lane == "run" else threading.Event()
        ctx = _Context(job_id, cancel, on_stage)
        try:
            if executor is None:
                raise JobError(f"No executor registered for kind '{job['kind']}'")
            result = executor(job, ctx)
            # A stop request that arrived after the work finished does not undo it:
            # the executor only raises JobInterrupted when it actually stopped early.
            mark_done(job_id, result or {})
        except JobInterrupted as exc:
            mark_interrupted(job_id, str(exc) or INTERRUPTED_BY_USER)
        except Exception as exc:  # noqa: BLE001 - job failures are reported, not raised
            logger.exception("Job %s (%s) failed", job_id, job["kind"])
            mark_failed(job_id, f"{type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                if lane == "run" and self._run is not None:
                    self._run.update({"current_job_id": None, "current_session_id": None, "stage": None})
                elif lane == "claude":
                    self._claude_current = None
