"""Web-app level tests for the meeting memory features (Claude mocked)."""

import json
import time

import pytest

from listener import jobs as jobsdb


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def client(isolated_env, monkeypatch):
    import listener.web.app as webapp
    webapp._reset_for_tests()
    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as c:
        yield c


def _seed_transcribed(env, sid, title, transcript):
    d = env["transcripts_dir"]
    (d / f"{sid}_transcript.md").write_text(transcript, encoding="utf-8")
    (d / f"{sid}_meta.json").write_text(json.dumps({"title": title, "language": "en", "duration": 700}))
    return sid


FAKE_MEMORY = {
    "summary": "Roadmap review; launch moved to October.",
    "key_points": [{"text": "Q3 roadmap review", "ts": "00:05"}],
    "decisions": [{"text": "Move the launch to October", "rationale": "the vendor slipped", "ts": "00:32"}],
    "tasks": [
        {"text": "Send the updated budget", "owner": "Ayşe", "deadline": "by Friday", "ts": "01:10"},
        {"text": "Own the load test", "owner": "Mehmet", "deadline": "end of next week", "ts": "04:10"},
        {"text": "Invented task", "owner": "John", "deadline": "31 December", "ts": "09:99"},
    ],
    "open_questions": [{"text": "Will the API rate limits hold?", "ts": "02:45"}],
}


@pytest.fixture
def fake_claude(monkeypatch):
    calls = {"schema": [], "session": []}

    async def fake_schema(prompt, schema, *, system_prompt="", model="", node_name="", auth_mode="max", max_retries=3):
        calls["schema"].append({"prompt": prompt, "system": system_prompt, "node": node_name})
        return json.loads(json.dumps(FAKE_MEMORY))

    async def fake_session(prompt, *, system_prompt="", model="", allowed_tools=None, max_turns=1,
                           auth_mode="max", node_name=""):
        calls["session"].append({"prompt": prompt, "system": system_prompt, "node": node_name})
        return "Launch moved to October [2026-05-12 11:52 — Q3 roadmap]. Budget is still open."

    monkeypatch.setattr("listener.claude.runner.run_with_schema_validation", fake_schema)
    monkeypatch.setattr("listener.claude.runner.run_claude_session", fake_session)
    return calls


def _job(client, job_id):
    return next(j for j in client.get("/api/status").get_json()["jobs"] if j["id"] == job_id)


def test_generate_memory_is_explicit_grounded_and_persistent(client, isolated_env, sample_transcript, fake_claude):
    env = isolated_env
    sid = _seed_transcribed(env, "2026-05-12_11-52-12", "Q3 roadmap", sample_transcript)
    assert client.get(f"/api/memory/{sid}").status_code == 404
    assert fake_claude["schema"] == []  # nothing happened by itself

    r = client.post(f"/api/memory/{sid}/generate")
    assert r.status_code == 202, r.get_json()
    jid = r.get_json()["job"]["id"]
    assert _wait(lambda: _job(client, jid)["status"] == "done"), _job(client, jid)
    assert len(fake_claude["schema"]) == 1
    assert "<transcript>" in fake_claude["schema"][0]["prompt"]
    assert "not instructions" in fake_claude["schema"][0]["system"]

    rec = client.get(f"/api/memory/{sid}").get_json()
    assert rec["title"] == "Q3 roadmap"
    assert rec["decisions"][0]["ts"] == "00:32" and rec["decisions"][0]["verified"] is True
    assert "vendor slipped" in rec["decisions"][0]["evidence"]
    tasks = {t["text"]: t for t in rec["tasks"]}
    assert tasks["Send the updated budget"]["owner"] == "Ayşe"
    assert tasks["Send the updated budget"]["deadline"] == "by Friday"
    invented = tasks["Invented task"]
    assert invented["owner"] is None and invented["deadline"] is None and invented["ts"] is None
    assert invented["verified"] is False
    assert any("John" in n for n in rec["grounding_notes"])
    assert rec["last_generation"]["status"] == "ok"
    # files on disk, session list knows about memory
    assert (env["transcripts_dir"] / f"{sid}_memory.md").exists()
    assert (env["transcripts_dir"] / f"{sid}_memory.json").exists()
    sess = client.get("/api/sessions").get_json()["sessions"][0]
    assert sess["has_memory"] is True and sess["files"]["memory"] == f"{sid}_memory.md"
    assert sess["files"]["transcript"] == f"{sid}_transcript.md"


def test_update_keeps_manual_edits_and_completion(client, isolated_env, sample_transcript, fake_claude):
    env = isolated_env
    sid = _seed_transcribed(env, "2026-05-12_11-52-12", "Q3 roadmap", sample_transcript)
    jid = client.post(f"/api/memory/{sid}/generate").get_json()["job"]["id"]
    assert _wait(lambda: _job(client, jid)["status"] == "done")
    rec = client.get(f"/api/memory/{sid}").get_json()
    budget = next(t for t in rec["tasks"] if t["text"] == "Send the updated budget")
    load = next(t for t in rec["tasks"] if t["text"] == "Own the load test")

    assert client.patch(f"/api/memory/tasks/{budget['id']}", json={"status": "done"}).status_code == 200
    assert client.patch(f"/api/memory/tasks/{load['id']}", json={"text": "Run the load test (staging)"}).status_code == 200
    assert client.patch(f"/api/memory/tasks/{load['id']}", json={"status": "bogus"}).status_code == 400
    assert client.patch("/api/memory/tasks/nope", json={"status": "done"}).status_code == 404
    manual = client.post("/api/memory/tasks", json={"session_id": sid, "text": "Book the room"}).get_json()["task"]

    # Re-generate with slightly reworded tasks and one task gone
    FAKE_MEMORY["tasks"][0]["text"] = "Send the updated budget to finance"
    del FAKE_MEMORY["tasks"][2]
    try:
        jid2 = client.post(f"/api/memory/{sid}/generate").get_json()["job"]["id"]
        assert _wait(lambda: _job(client, jid2)["status"] == "done")
    finally:
        FAKE_MEMORY["tasks"][0]["text"] = "Send the updated budget"
        FAKE_MEMORY["tasks"].append({"text": "Invented task", "owner": "John", "deadline": "31 December", "ts": "09:99"})

    rec2 = client.get(f"/api/memory/{sid}").get_json()
    assert rec2["generation_count"] == 2
    by_id = {t["id"]: t for t in rec2["tasks"]}
    assert by_id[budget["id"]]["status"] == "done"
    assert by_id[budget["id"]]["text"] == "Send the updated budget to finance"  # not manually edited → AI text
    assert by_id[load["id"]]["text"] == "Run the load test (staging)"  # manual edit kept
    assert by_id[load["id"]]["ai_text"] == "Own the load test"
    assert by_id[manual["id"]]["stale"] is False
    texts = [t["text"] for t in rec2["tasks"]]
    assert texts.count("Send the updated budget to finance") == 1  # no duplicate
    stale = [t for t in rec2["tasks"] if t["stale"]]
    assert [t["text"] for t in stale] == ["Invented task"]  # kept, marked stale, not deleted
    # open tasks endpoint
    open_tasks = client.get(f"/api/memory/tasks?status=open&session_id={sid}").get_json()["tasks"]
    assert budget["id"] not in {t["id"] for t in open_tasks}


def test_failed_generation_is_recorded_and_recoverable(client, isolated_env, sample_transcript, monkeypatch):
    env = isolated_env
    sid = _seed_transcribed(env, "2026-05-12_11-52-12", "Q3 roadmap", sample_transcript)

    async def boom(*a, **k):
        raise RuntimeError("claude down")

    monkeypatch.setattr("listener.claude.runner.run_with_schema_validation", boom)
    jid = client.post(f"/api/memory/{sid}/generate").get_json()["job"]["id"]
    assert _wait(lambda: _job(client, jid)["status"] == "failed")
    assert "claude down" in _job(client, jid)["error"]
    r = client.get(f"/api/memory/{sid}")
    assert r.status_code == 404
    assert r.get_json()["generations"][0]["status"] == "failed"
    assert "claude down" in r.get_json()["generations"][0]["error"]
    # explicit retry only
    time.sleep(0.2)
    assert _job(client, jid)["status"] == "failed"
    assert client.post(f"/api/memory/nope/generate").status_code == 404


def test_ask_cites_selected_meetings_only(client, isolated_env, sample_transcript, fake_claude):
    env = isolated_env
    a = _seed_transcribed(env, "2026-05-12_11-52-12", "Q3 roadmap", sample_transcript)
    b = _seed_transcribed(env, "2026-05-13_09-00-00", "Hiring sync", sample_transcript.replace("Q3 roadmap", "hiring plan"))
    c = _seed_transcribed(env, "2026-05-14_09-00-00", "Unrelated", sample_transcript)
    for sid in (a, b, c):
        jid = client.post(f"/api/memory/{sid}/generate").get_json()["job"]["id"]
        assert _wait(lambda: _job(client, jid)["status"] == "done")
    # rename after generation must propagate to memory/search
    client.post(f"/api/sessions/{c}/rename", json={"title": "Vendor call"})
    assert client.get(f"/api/memory/{c}").get_json()["title"] == "Vendor call"

    assert client.post("/api/memory/ask", json={"question": "x"}).status_code == 400
    r = client.post("/api/memory/ask", json={"question": "What was decided?", "session_ids": [a, b]})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert {s["session_id"] for s in d["sources"]} == {a, b}
    assert all(s["source"] == "memory" for s in d["sources"])
    prompt = fake_claude["session"][-1]["prompt"]
    assert "Q3 roadmap" in prompt and "Hiring sync" in prompt and "Vendor call" not in prompt
    assert "<meeting_memory>" in prompt
    assert "Welcome everyone" not in prompt  # never raw transcript
    assert "Q3 roadmap" in d["answer"]

    # project grouping
    p = client.post("/api/memory/projects", json={"name": "Launch", "session_ids": [a, c]}).get_json()["project"]
    assert client.post("/api/memory/projects", json={"name": "Launch"}).status_code == 409
    r = client.post("/api/memory/ask", json={"question": "Open work?", "project_id": p["id"]})
    assert {s["session_id"] for s in r.get_json()["sources"]} == {a, c}
    assert client.put(f"/api/memory/projects/{p['id']}", json={"session_ids": [a]}).get_json()["project"]["session_ids"] == [a]
    assert client.get("/api/memory/search?q=budget").get_json()
    assert client.delete(f"/api/memory/projects/{p['id']}").status_code == 200

    # bounded context: tiny limit omits meetings
    r = client.post("/api/memory/ask", json={"question": "q", "session_ids": [a, b], "max_chars": 300})
    assert r.get_json()["omitted"] == [b] or r.get_json()["omitted"] == [a]


def test_delete_session_removes_memory_and_jobs(client, isolated_env, sample_transcript, fake_claude):
    env = isolated_env
    sid = _seed_transcribed(env, "2026-05-12_11-52-12", "Q3 roadmap", sample_transcript)
    jid = client.post(f"/api/memory/{sid}/generate").get_json()["job"]["id"]
    assert _wait(lambda: _job(client, jid)["status"] == "done")
    r = client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 200
    assert f"{sid}_memory.md" in r.get_json()["deleted"]
    assert client.get(f"/api/memory/{sid}").status_code == 404
    assert client.get("/api/status").get_json()["jobs"] == []
    from listener.memory import list_memories
    assert list_memories([sid]) == []


def test_analysis_fallback_when_no_memory(client, isolated_env, sample_transcript, fake_claude):
    env = isolated_env
    sid = _seed_transcribed(env, "2026-05-12_11-52-12", "Q3 roadmap", sample_transcript)
    (env["transcripts_dir"] / f"{sid}_analysis.md").write_text(
        "# Meeting Analysis\n\n## Summary\nOld analysis text.\n\n---\n\n# Full Transcript\n\n[00:05] should not leak\n")
    r = client.post("/api/memory/ask", json={"question": "Summarize", "session_ids": [sid]})
    d = r.get_json()
    assert d["sources"][0]["source"] == "analysis"
    prompt = fake_claude["session"][-1]["prompt"]
    assert "Old analysis text" in prompt and "should not leak" not in prompt
