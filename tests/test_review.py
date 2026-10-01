"""Review line: after transcription a meeting waits for the user's tags and
notes; nothing goes to Claude until the user starts it. Notes travel with the
tags as user-provided context. Claude is mocked throughout.
"""

import json
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from listener import settings
from listener import tags as tagsmod


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def client(isolated_env, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "config.yaml")
    import listener.web.app as webapp
    webapp._reset_for_tests()
    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as c:
        yield c


def _seed(env, sid, *, transcript=True, analysis=False, meta=None):
    d = env["transcripts_dir"]
    sf.write(d / f"{sid}.wav", np.zeros(8000, dtype="float32"), 16000)
    if transcript:
        (d / f"{sid}_transcript.md").write_text("# T\n\n---\n\n**[00:05] Speaker 1:** Ayşe sends the budget by Friday.\n", encoding="utf-8")
    if analysis:
        (d / f"{sid}_analysis.md").write_text("# A\n", encoding="utf-8")
    (d / f"{sid}_meta.json").write_text(json.dumps({"title": sid, **(meta or {})}), encoding="utf-8")


def _meta(env, sid):
    return json.loads((env["transcripts_dir"] / f"{sid}_meta.json").read_text(encoding="utf-8"))


def test_context_block_and_notes_cleaning():
    assert tagsmod.context_block([], "") == ""
    block = tagsmod.context_block([], "  Ali is the client.\r\nExtract pricing promises.  ")
    assert block.startswith("Notes the user wrote") and "Ali is the client.\nExtract pricing promises." in block
    assert tagsmod.clean_notes(None) == ""
    with pytest.raises(tagsmod.TagError):
        tagsmod.clean_notes(5)
    with pytest.raises(tagsmod.TagError):
        tagsmod.clean_notes("x" * 4001)
    assert tagsmod.meeting_notes({"notes": " hi "}) == "hi" and tagsmod.meeting_notes({"notes": 3}) == ""


def test_notes_reach_memory_prompt_and_ask_context(client, isolated_env):
    from listener import memory
    out = isolated_env["transcripts_dir"]
    sid = "2026-05-12_11-52-00"
    _seed(isolated_env, sid, meta={"tags": ["client"], "notes": "Ali is the client. Extract pricing promises."})
    _, prompt = memory.build_generation_prompt("**[00:05] Speaker 1:** hi", "T", "en", tags=["client"],
                                               notes="Ali is the client. Extract pricing promises.")
    assert "Notes the user wrote" in prompt and "pricing promises" in prompt
    assert prompt.index("pricing promises") < prompt.index("<transcript>")
    transcript = (out / f"{sid}_transcript.md").read_text(encoding="utf-8")
    memory.generate_memory(sid, transcript, title="T", tags=["client"], notes="Ali is the client.",
                           transcripts_dir=out, llm=lambda s, u, schema: {"summary": "s", "key_points": [], "decisions": [], "tasks": [], "open_questions": []})
    ctx = memory.retrieve_context([sid], transcripts_dir=out)["meetings"][0]["text"]
    assert "Tags (set by the user): client" in ctx and "Notes (written by the user): Ali is the client. Extract pricing promises." in ctx


def test_review_line_lists_only_transcribed_unreviewed_meetings(client, isolated_env):
    a, b, c, d = "2026-01-01_10-00-00", "2026-01-02_10-00-00", "2026-01-03_10-00-00", "2026-01-04_10-00-00"
    _seed(isolated_env, a)                                             # transcribed, waiting
    _seed(isolated_env, b, transcript=False)                           # audio only
    _seed(isolated_env, c, analysis=True)                              # already analysed
    _seed(isolated_env, d, meta={"reviewed_at": "2026-01-04T11:00:00"})  # waved through
    pending = client.get("/api/review").get_json()["meetings"]
    assert [m["id"] for m in pending] == [a]
    sessions = {s["id"]: s for s in client.get("/api/sessions").get_json()["sessions"]}
    assert [sessions[x]["needs_review"] for x in (a, b, c, d)] == [True, False, False, False]
    assert sessions[d]["reviewed_at"] == "2026-01-04T11:00:00"

    # notes: saved on the meeting, returned by the list, validated
    r = client.post(f"/api/sessions/{a}/notes", json={"notes": "  Ali is the client.\r\nPricing matters. "})
    assert r.status_code == 200 and r.get_json()["notes"] == "Ali is the client.\nPricing matters."
    assert _meta(isolated_env, a)["notes"] == "Ali is the client.\nPricing matters."
    assert client.get(f"/api/sessions/{a}/notes").get_json()["notes"].startswith("Ali")
    assert client.get("/api/review").get_json()["meetings"][0]["notes"].startswith("Ali")
    assert client.post(f"/api/sessions/{a}/notes", json={"notes": 7}).status_code == 400
    assert client.post("/api/sessions/nope/notes", json={"notes": "x"}).status_code == 404
    assert client.post(f"/api/sessions/{a}/notes", json={"notes": ""}).status_code == 200
    assert _meta(isolated_env, a)["notes"] == ""

    # wave through without Claude, and back again
    r = client.post(f"/api/sessions/{a}/reviewed")
    assert r.status_code == 200 and r.get_json()["reviewed_at"]
    assert client.get("/api/review").get_json()["meetings"] == []
    assert client.delete(f"/api/sessions/{a}/reviewed").status_code == 200
    assert [m["id"] for m in client.get("/api/review").get_json()["meetings"]] == [a]


def test_starting_analysis_marks_reviewed_and_passes_notes(client, isolated_env, monkeypatch):
    sid = "2026-01-01_10-00-00"
    _seed(isolated_env, sid, meta={"tags": ["client"], "notes": "Ali is the client."})
    tagsmod.upsert_tag("client", "external customer")
    seen = {}

    def fake_analyze(text, model="m", recipe_id=None, context=None):
        seen["context"] = context
        return "## Summary\nfine\n"

    monkeypatch.setattr("listener.analyzer.analyze_transcript_sync", fake_analyze)
    monkeypatch.setenv("HF_TOKEN", "")
    r = client.post(f"/api/analyze/{sid}", json={})
    assert r.status_code == 202, r.get_json()
    assert _meta(isolated_env, sid)["reviewed_at"]                     # left the review line at once
    assert client.get("/api/review").get_json()["meetings"] == []
    jid = r.get_json()["job"]["id"]
    assert _wait(lambda: client.get("/api/status").get_json() and any(
        j["id"] == jid and j["status"] == "done" for j in client.get("/api/status").get_json()["jobs"]))
    assert "- client — external customer" in seen["context"] and "Ali is the client." in seen["context"]
    assert client.post(f"/api/analyze/{sid}", json={}).status_code == 202   # re-analysis still fine
