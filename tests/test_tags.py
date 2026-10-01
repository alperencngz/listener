"""User-defined meeting tags: vocabulary, per-meeting tags, the context Claude
receives, and the web endpoints. Config and meetings live in temp dirs; Claude
is never called.
"""

import asyncio
import io
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from listener import settings
from listener import tags as tagsmod


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    return path


# ---------------------------------------------------------------------------
# Vocabulary + per-meeting tags (no web app)
# ---------------------------------------------------------------------------

def test_vocabulary_crud_and_normalisation(cfg):
    assert tagsmod.list_tags() == []
    t = tagsmod.upsert_tag("  #Client  meeting ", "a paying customer")
    assert t == {"name": "Client meeting", "note": "a paying customer"}
    tagsmod.upsert_tag("client MEETING", "updated note")           # same tag, casefold
    assert tagsmod.list_tags() == [{"name": "Client meeting", "note": "updated note"}]
    tagsmod.upsert_tag("1:1")
    assert [t["name"] for t in tagsmod.list_tags()] == ["Client meeting", "1:1"]
    renamed = tagsmod.upsert_tag("client meeting", rename_to="Customer")
    assert renamed["name"] == "Customer" and renamed["note"] == "updated note"
    with pytest.raises(tagsmod.TagError):
        tagsmod.upsert_tag("1:1", rename_to="customer")            # name taken
    assert tagsmod.delete_tag("CUSTOMER") is True
    assert tagsmod.delete_tag("customer") is False
    assert [t["name"] for t in tagsmod.list_tags()] == ["1:1"]
    for bad in ("", "   ", "a,b", "x" * 41, 5):
        with pytest.raises(tagsmod.TagError):
            tagsmod.normalize_name(bad)
    assert tagsmod.find_tag("1:1") == {"name": "1:1", "note": ""}
    # the config file keeps other keys
    settings.update_config(default_model="small")
    assert settings.load_config()["tags"] == [{"name": "1:1", "note": ""}]


def test_parse_tag_list_and_meeting_tags():
    assert tagsmod.parse_tag_list("client, Weekly ,client") == ["client", "Weekly"]
    assert tagsmod.parse_tag_list(["a", " b ", "", "A"]) == ["a", "b"]
    assert tagsmod.parse_tag_list(None) == [] and tagsmod.parse_tag_list("") == []
    with pytest.raises(tagsmod.TagError):
        tagsmod.parse_tag_list({"a": 1})
    with pytest.raises(tagsmod.TagError):
        tagsmod.parse_tag_list([f"t{i}" for i in range(21)])
    assert tagsmod.meeting_tags({"tags": ["x", 3, "", "X", " y "]}) == ["x", "y"]
    assert tagsmod.meeting_tags({"tags": "nope"}) == [] and tagsmod.meeting_tags(None) == []


def test_set_meeting_tags_creates_unknown_names(cfg, tmp_path):
    out = tmp_path / "t"; out.mkdir()
    (out / "2026-01-01_10-00-00_meta.json").write_text(json.dumps({"title": "A"}), encoding="utf-8")
    tagsmod.upsert_tag("Client", "paying customer")
    names = tagsmod.set_meeting_tags(out, "2026-01-01_10-00-00", ["client", "Board prep"])
    assert names == ["Client", "Board prep"]                       # canonical spelling wins
    meta = json.loads((out / "2026-01-01_10-00-00_meta.json").read_text(encoding="utf-8"))
    assert meta["title"] == "A" and meta["tags"] == ["Client", "Board prep"]
    assert [t["name"] for t in tagsmod.list_tags()] == ["Client", "Board prep"]
    assert tagsmod.strip_tag(out, "board PREP") == 1
    assert tagsmod.meeting_tags(json.loads((out / "2026-01-01_10-00-00_meta.json").read_text())) == ["Client"]


def test_prompt_block_uses_notes(cfg):
    assert tagsmod.prompt_block([]) == ""
    tagsmod.upsert_tag("client", "external customer; extract commitments we made")
    block = tagsmod.prompt_block(["client", "weekly"])
    assert "- client — external customer; extract commitments we made" in block
    assert "- weekly" in block and "not part of the transcript" in block


# ---------------------------------------------------------------------------
# What Claude receives
# ---------------------------------------------------------------------------

def test_memory_generation_prompt_includes_tags(cfg):
    from listener import memory
    tagsmod.upsert_tag("client", "external customer")
    _, plain = memory.build_generation_prompt("**[00:05] Speaker 1:** hi", "T", "en")
    _, tagged = memory.build_generation_prompt("**[00:05] Speaker 1:** hi", "T", "en", tags=["client", "q3"])
    assert "Tags the user attached" not in plain
    assert "- client — external customer" in tagged and "- q3" in tagged
    assert tagged.index("Tags the user attached") < tagged.index("<transcript>")


def test_analyzer_appends_context_to_system_prompt(monkeypatch):
    from listener import analyzer
    seen = []

    async def fake_session(prompt, *, system_prompt="", model="", node_name="", **kw):
        seen.append(system_prompt)
        return "ok"

    monkeypatch.setattr(analyzer, "run_claude_session", fake_session)
    analyzer.analyze_transcript_sync("hello", context="- client — external customer")
    analyzer.analyze_transcript_sync("hello", context="")
    assert seen[0].endswith("- client — external customer") and seen[0].startswith(analyzer.SYSTEM_PROMPT)
    assert seen[1] == analyzer.SYSTEM_PROMPT


def test_memory_files_and_ask_context_carry_tags(cfg, isolated_env):
    from listener import memory
    out = isolated_env["transcripts_dir"]
    sid = "2026-05-12_11-52-00"
    transcript = "# Meeting Transcript\n\n---\n\n**[00:05] Speaker 1:** Ayşe will send the budget by Friday.\n"
    (out / f"{sid}_transcript.md").write_text(transcript, encoding="utf-8")
    (out / f"{sid}_meta.json").write_text(json.dumps({"title": "Budget", "tags": ["client"]}), encoding="utf-8")
    tagsmod.upsert_tag("client", "external customer")
    prompts = []

    def fake_llm(system_prompt, user_prompt, schema):
        prompts.append(user_prompt)
        return {"summary": "Budget follow-up.", "key_points": [], "decisions": [],
                "tasks": [{"text": "Send the budget", "owner": "Ayşe", "deadline": "by Friday", "ts": "00:05"}],
                "open_questions": []}

    record = memory.generate_memory(sid, transcript, title="Budget", tags=["client"], transcripts_dir=out, llm=fake_llm)
    assert "- client — external customer" in prompts[0]
    assert record["tags"] == ["client"]
    assert "- Tags: client" in (out / f"{sid}_memory.md").read_text(encoding="utf-8")
    assert json.loads((out / f"{sid}_memory.json").read_text(encoding="utf-8"))["tags"] == ["client"]
    ctx = memory.retrieve_context([sid], transcripts_dir=out)
    assert ctx["meetings"][0]["text"].startswith("Tags (set by the user): client — external customer")


def test_analyze_job_passes_tag_context(cfg, isolated_env, monkeypatch):
    from listener import pipeline
    out = isolated_env["transcripts_dir"]
    sid = "2026-05-12_11-52-00"
    (out / f"{sid}_transcript.md").write_text("# T\n\n---\n\n**[00:05] Speaker 1:** hi\n", encoding="utf-8")
    (out / f"{sid}_meta.json").write_text(json.dumps({"title": "T", "tags": ["client"], "duration": 60}), encoding="utf-8")
    tagsmod.upsert_tag("client", "external customer")
    seen = {}

    def fake_analyze(text, model="m", recipe_id=None, context=None):
        seen["context"] = context
        return "## Summary\nfine\n"

    import listener.analyzer as analyzer
    monkeypatch.setattr(analyzer, "analyze_transcript_sync", fake_analyze)

    class Ctx:
        def set_stage(self, *a, **k): pass
        def set_progress(self, *a, **k): pass
        def should_stop(self): return False

    pipeline.run_analyze_job({"session_id": sid, "options": {}}, Ctx(), out)
    assert "- client — external customer" in seen["context"]
    assert "**Tags:** client" in (out / f"{sid}_analysis.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Web endpoints
# ---------------------------------------------------------------------------

@pytest.fixture
def client(cfg, isolated_env):
    import listener.web.app as webapp
    webapp._reset_for_tests()
    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as c:
        yield c


def _seed(env, sid, title="Meeting"):
    d = env["transcripts_dir"]
    sf.write(d / f"{sid}.wav", np.zeros(8000, dtype="float32"), 16000)
    (d / f"{sid}_meta.json").write_text(json.dumps({"title": title}), encoding="utf-8")


def test_session_tags_endpoints_and_filter(client, isolated_env):
    a, b = "2026-01-01_10-00-00", "2026-01-02_10-00-00"
    _seed(isolated_env, a, "A"); _seed(isolated_env, b, "B")
    r = client.post(f"/api/sessions/{a}/tags", json={"tags": ["Client", "weekly"]})
    assert r.status_code == 200
    assert r.get_json()["tags"] == ["Client", "weekly"]
    assert [v["name"] for v in r.get_json()["vocabulary"]] == ["Client", "weekly"]
    assert client.post(f"/api/sessions/{b}/tags", json={"tags": "client"}).get_json()["tags"] == ["Client"]
    assert client.post(f"/api/sessions/{a}/tags", json={"tags": [3]}).status_code == 400
    assert client.post("/api/sessions/nope/tags", json={"tags": ["x"]}).status_code == 404
    assert client.get(f"/api/sessions/{a}/tags").get_json()["tags"] == ["Client", "weekly"]

    vocab = client.get("/api/tags").get_json()["tags"]
    assert [(v["name"], v["count"]) for v in vocab] == [("Client", 2), ("weekly", 1)]

    sessions = {s["id"]: s for s in client.get("/api/sessions").get_json()["sessions"]}
    assert sessions[a]["tags"] == ["Client", "weekly"] and sessions[b]["tags"] == ["Client"]
    assert [s["id"] for s in client.get("/api/sessions?tag=weekly").get_json()["sessions"]] == [a]
    assert client.get("/api/sessions?tag=CLIENT").get_json()["total"] == 2
    assert client.get("/api/sessions?tag=none").get_json()["sessions"] == []


def test_vocabulary_endpoints_rename_and_delete(client, isolated_env):
    a = "2026-01-01_10-00-00"; _seed(isolated_env, a)
    client.post(f"/api/sessions/{a}/tags", json={"tags": ["client"]})
    r = client.post("/api/tags", json={"name": "client", "note": "external customer"})
    assert r.status_code == 200 and r.get_json()["tag"] == {"name": "client", "note": "external customer"}
    assert client.post("/api/tags", json={"name": ""}).status_code == 400
    r = client.post("/api/tags", json={"name": "client", "rename_to": "Customer"})
    assert r.get_json()["tag"]["name"] == "Customer"
    assert client.get(f"/api/sessions/{a}/tags").get_json()["tags"] == ["Customer"]   # meetings follow the rename
    assert client.delete("/api/tags/missing").status_code == 404
    r = client.delete("/api/tags/customer?strip=1")
    assert r.status_code == 200 and r.get_json()["stripped"] == 1 and r.get_json()["vocabulary"] == []
    assert client.get(f"/api/sessions/{a}/tags").get_json()["tags"] == []


def test_import_and_start_accept_tags(client, isolated_env, monkeypatch):
    buf = io.BytesIO()
    sf.write(buf, np.zeros(8000, dtype="float32"), 16000, format="WAV")
    buf.seek(0)
    r = client.post("/api/import", data={"audio": (buf, "memo.wav"), "tags": "voice memo, Ideas"},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["tags"] == ["voice memo", "Ideas"]
    meta = json.loads((isolated_env["transcripts_dir"] / f"{body['session_id']}_meta.json").read_text(encoding="utf-8"))
    assert meta["tags"] == ["voice memo", "Ideas"] and meta["source"] == "import"

    bad = io.BytesIO(); sf.write(bad, np.zeros(800, dtype="float32"), 16000, format="WAV"); bad.seek(0)
    r = client.post("/api/import", data={"audio": (bad, "x.wav"), "tags": "[1, 2]"}, content_type="multipart/form-data")
    assert r.status_code == 400

    # Recording start: tags land on the placeholder meta before the mic opens.
    import listener.recorder as recorder
    from tests.test_queue import FakeRecorder
    monkeypatch.setattr(recorder, "Recorder", FakeRecorder)
    monkeypatch.setattr(recorder, "resolve_device", lambda device=None, name=None, refresh=True: (0, "Fake Mic", None))
    r = client.post("/api/start", json={"tags": ["Ideas", "standup"]})
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()["session_id"]
    assert json.loads((isolated_env["transcripts_dir"] / f"{sid}_meta.json").read_text(encoding="utf-8"))["tags"] == ["Ideas", "standup"]
    assert client.post("/api/stop").status_code == 200
    assert [v["name"] for v in client.get("/api/tags").get_json()["tags"]] == ["voice memo", "Ideas", "standup"]


# ---------------------------------------------------------------------------
# Timeline: to-dos and notes per meeting, newest first, scoped by tag/status
# ---------------------------------------------------------------------------

def _seed_memory(out, sid, title, tags, tasks, decisions=(), questions=()):
    from listener import memory
    transcript = "# T\n\n---\n\n**[00:05] Speaker 1:** " + " ".join(t["text"] for t in tasks) + " Ayşe Mehmet Friday.\n"
    (out / f"{sid}_transcript.md").write_text(transcript, encoding="utf-8")
    (out / f"{sid}_meta.json").write_text(json.dumps({"title": title, "tags": tags}), encoding="utf-8")
    body = {"summary": f"{title} summary", "key_points": [], "tasks": list(tasks),
            "decisions": [{"text": d, "rationale": None, "ts": None} for d in decisions],
            "open_questions": [{"text": q, "ts": None} for q in questions]}
    memory.generate_memory(sid, transcript, title=title, tags=tags, transcripts_dir=out,
                           llm=lambda s, u, schema: dict(body))


def test_timeline_groups_by_meeting_and_filters(client, isolated_env):
    out = isolated_env["transcripts_dir"]
    a, b, c = "2026-05-10_09-00-00", "2026-05-12_11-52-00", "2026-05-14_15-00-00"
    _seed_memory(out, a, "Old client call", ["client"],
                 [{"text": "Send the proposal", "owner": None, "deadline": None, "ts": "00:05"}],
                 decisions=["Go with plan A"])
    _seed_memory(out, b, "Internal sync", ["internal"],
                 [{"text": "Fix the build", "owner": None, "deadline": None, "ts": None},
                  {"text": "Write the changelog", "owner": None, "deadline": None, "ts": None}],
                 questions=["Who owns QA?"])
    _seed_memory(out, c, "Client follow-up", ["client", "weekly"], [], decisions=["Ship on Friday"])

    r = client.get("/api/memory/timeline")
    assert r.status_code == 200
    body = r.get_json()
    assert [m["session_id"] for m in body["meetings"]] == [c, b, a]           # newest first
    assert body["totals"] == {"open": 3, "done": 0}
    by_id = {m["session_id"]: m for m in body["meetings"]}
    assert by_id[b]["tags"] == ["internal"] and len(by_id[b]["tasks"]) == 2
    assert by_id[b]["open_questions"][0]["text"] == "Who owns QA?"
    assert by_id[c]["tasks"] == [] and by_id[c]["decisions"][0]["text"] == "Ship on Friday"
    assert {(t["name"], t["count"]) for t in body["tags"]} == {("client", 2), ("internal", 1), ("weekly", 1)}

    # tag filter (case-insensitive), scope by selected meetings, notes off
    assert [m["session_id"] for m in client.get("/api/memory/timeline?tag=CLIENT").get_json()["meetings"]] == [c, a]
    assert [m["session_id"] for m in client.get(f"/api/memory/timeline?session_id={a}&session_id={b}").get_json()["meetings"]] == [b, a]
    assert [m["session_id"] for m in client.get("/api/memory/timeline?notes=0").get_json()["meetings"]] == [b, a]

    # mark one done: it leaves the open view and appears in the done view
    task_id = by_id[b]["tasks"][0]["id"]
    assert client.patch(f"/api/memory/tasks/{task_id}", json={"status": "done"}).status_code == 200
    done = client.get("/api/memory/timeline?status=done&notes=0").get_json()
    assert [m["session_id"] for m in done["meetings"]] == [b]
    assert done["meetings"][0]["tasks"][0]["text"] == "Fix the build"
    assert done["meetings"][0]["tasks_open"] == 1 and done["meetings"][0]["tasks_done"] == 1
    opened = client.get("/api/memory/timeline?status=open&notes=0").get_json()
    assert [len(m["tasks"]) for m in opened["meetings"]] == [1, 1]
    assert client.get("/api/memory/timeline?status=bogus").status_code == 400
    assert client.get("/api/memory/timeline?status=all").get_json()["totals"] == {"open": 2, "done": 1}
