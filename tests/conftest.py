"""Shared pytest fixtures.

Every test runs against an isolated SQLite DB and an isolated transcripts
directory under tmp_path. Nothing touches ~/.listener or ./transcripts.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Point listener.db at a temp DB and the web app at a temp transcripts dir.

    Yields a dict with ``db_path`` and ``transcripts_dir``.
    """
    import listener.db as db

    db_path = tmp_path / "listener-test.db"
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()

    db.reset_db()
    monkeypatch.setattr(db, "DB_PATH", db_path)

    # The web app keeps OUTPUT_DIR as a module global; patch it if importable.
    try:
        import listener.web.app as webapp
        monkeypatch.setattr(webapp, "OUTPUT_DIR", transcripts_dir)
    except Exception:  # pragma: no cover - app import failure surfaces elsewhere
        webapp = None

    yield {"db_path": db_path, "transcripts_dir": transcripts_dir, "webapp": webapp}

    db.reset_db()


SAMPLE_TRANSCRIPT = """# Meeting Transcript -- 2026-05-12 11:52

**Duration:** 12m 4s  \\
**Language:** en (97% confidence)

---

**[00:05] Speaker 1:** Welcome everyone, let's go over the Q3 roadmap.

**[00:32] Speaker 2:** We decided last week to move the launch to October because the vendor slipped.

**[01:10] Speaker 1:** Ayşe will send the updated budget by Friday.

**[02:45] Speaker 3:** I'm not sure the API rate limits will hold. We should test that.

**[03:20] Speaker 2:** Agreed. Let's park the pricing question until we hear from legal.

**[04:02] Speaker 1:** Mehmet, can you own the load test?

**[04:10] Speaker 3:** Yes, Mehmet takes it. Target is end of next week.
"""


@pytest.fixture
def sample_transcript():
    return SAMPLE_TRANSCRIPT
