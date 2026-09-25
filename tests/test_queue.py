"""Recording / queue lifecycle tests for the web app.

Everything heavy is mocked: the recorder never opens a microphone, the
transcriber never loads Whisper, and Claude is never called. Each test uses an
isolated DB and transcripts directory (see conftest.py).
"""

import json
import threading
import time

import pytest

from listener import jobs as jobsdb
from listener import pipeline


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class FakeRecorder:
    """Stands in for listener.recorder.Recorder; writes a tiny WAV on stop."""

    instances: list["FakeRecorder"] = []

    def __init__(self, device=None, channels=1, on_audio=None):
        self.device = device
        self.on_audio = on_audio
        self.path = None
        self.stopped = False
        FakeRecorder.instances.append(self)

    def start(self, output_path):
        self.path = output_path
        _write_wav(output_path)

    def stop(self):
        self.stopped = True
        return self.path


def _write_wav(path, seconds: float = 0.5):
    import numpy as np
    import soundfile as sf
    sr = 16000
    sf.write(path, np.zeros(int(sr * seconds), dtype="float32"), sr)


class FakeSegment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text, self.speaker, self.words = start, end, text, "", []


class FakeResult:
    def __init__(self, segments, language="en"):
        self.segments = segments
        self.language = language
        self.language_probability = 0.9
        self.duration = 12.0

    @property
    def has_speakers(self):
        return any(s.speaker for s in self.segments)

    def to_timestamped_text(self):
        return "\n\n".join(f"[00:{int(s.start):02d}] {s.text}" for s in self.segments)


@pytest.fixture
def client(isolated_env, monkeypatch):
    import listener.recorder
    import listener.web.app as webapp

    webapp._reset_for_tests()
    FakeRecorder.instances.clear()
    monkeypatch.setattr(listener.recorder, "Recorder", FakeRecorder)
    # Keep the suite off the real audio stack: resolve whatever was asked for.
    monkeypatch.setattr(
        listener.recorder, "resolve_device",
        lambda device=None, name=None, refresh=True: (device if device is not None else 0,
                                                       name or "Fake Microphone", None),
    )
    # No HF token → no diarization; no webhooks configured
    monkeypatch.setenv("HF_TOKEN", "")
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "")
    monkeypatch.setattr("listener.diarizer.get_hf_token", lambda cli_token=None: None)
    monkeypatch.setattr("listener.webhooks.list_webhooks", lambda: [])
    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as c:
        yield c
    # make sure no run thread is left behind
    webapp._runner.stop_run()
    for _ in range(50):
        if not webapp._runner.transcription_active():
            break
        time.sleep(0.05)


@pytest.fixture
def transcribe_mock(monkeypatch):
    """Patch the Whisper call with a controllable fake.

    ``gate`` lets a test hold a transcription in flight; ``calls`` records
    concurrent invocations so we can assert only one runs at a time.
    """
    import listener.transcriber as tr

    state = {"gate": threading.Event(), "calls": [], "active": 0, "max_active": 0,
             "fail_for": set(), "claude_calls": 0}
    state["gate"].set()

    def fake_transcribe(audio_path, model_size="large-v3", language=None, device="cpu",
                        compute_type="auto", multilingual=False, hotwords=None,
                        should_stop=None, on_progress=None):
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        state["calls"].append({"audio_path": audio_path, "language": language,
                               "multilingual": multilingual, "thread": threading.current_thread().name})
        try:
            state["gate"].wait(timeout=10)
            if should_stop and should_stop():
                raise tr.TranscriptionInterrupted("stopped in test")
            sid = audio_path.split("/")[-1]
            for marker in state["fail_for"]:
                if marker in sid:
                    raise RuntimeError(f"boom for {marker}")
            if on_progress:
                on_progress(2, 8.0, 12.0)
            return FakeResult([FakeSegment(1, 4, f"hello from {sid}"), FakeSegment(5, 8, "second line")])
        finally:
            state["active"] -= 1

    monkeypatch.setattr(tr, "transcribe", fake_transcribe)

    def no_claude(*a, **k):
        state["claude_calls"] += 1
        raise AssertionError("Claude must not be called during transcription")

    monkeypatch.setattr("listener.analyzer.generate_title_sync", no_claude)
    monkeypatch.setattr("listener.analyzer.analyze_transcript_sync", no_claude)
    monkeypatch.setattr("listener.claude.runner.run_claude_session", no_claude)
    return state


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _status(client):
    return client.get("/api/status").get_json()


def _job(client, job_id):
    return next(j for j in _status(client)["jobs"] if j["id"] == job_id)


def _make_audio_session(env, sid="2026-01-01_10-00-00", title=None):
    path = env["transcripts_dir"] / f"{sid}.wav"
    _write_wav(path)
    if title:
        (env["transcripts_dir"] / f"{sid}_meta.json").write_text(json.dumps({"title": title}))
    return sid


# ---------------------------------------------------------------------------
# Recording is independent of processing
# ---------------------------------------------------------------------------

def test_stop_recording_saves_without_queueing(client, isolated_env):
    r = client.post("/api/start", json={"device": None})
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()["session_id"]
    assert _status(client)["recording"]["active"] is True

    r = client.post("/api/stop")
    assert r.status_code == 200
    assert r.get_json()["session_id"] == sid
    assert (isolated_env["transcripts_dir"] / f"{sid}.wav").exists()
    st = _status(client)
    assert st["recording"]["active"] is False
    assert st["jobs"] == []  # nothing queued, nothing processed
    sessions = client.get("/api/sessions").get_json()["sessions"]
    assert sessions[0]["id"] == sid and sessions[0]["job"] is None


def test_second_recording_rejected_while_recording(client):
    assert client.post("/api/start", json={}).status_code == 200
    r = client.post("/api/start", json={})
    assert r.status_code == 409
    client.post("/api/stop")


def test_record_b_while_a_transcribes(client, isolated_env, transcribe_mock):
    env = isolated_env
    sid_a = _make_audio_session(env, "2026-01-01_10-00-00", title="Meeting A")
    transcribe_mock["gate"].clear()  # hold A's transcription in flight

    r = client.post("/api/queue", json={"session_id": sid_a, "language": "en"})
    assert r.status_code == 201, r.get_json()
    job_a = r.get_json()["job"]["id"]
    r = client.post("/api/queue/run", json={})
    assert r.status_code == 200, r.get_json()
    assert _wait(lambda: _job(client, job_a)["status"] == "running")
    assert _wait(lambda: _job(client, job_a)["stage"] == "transcribing")

    # Start recording B while A is transcribing
    r = client.post("/api/start", json={"device": None})
    assert r.status_code == 200, r.get_json()
    sid_b = r.get_json()["session_id"]
    assert sid_b != sid_a
    st = _status(client)
    assert st["recording"]["active"] and st["recording"]["session_id"] == sid_b
    assert st["run"]["active"] and st["run"]["current_session_id"] == sid_a

    # Finish A; B keeps recording untouched
    transcribe_mock["gate"].set()
    assert _wait(lambda: _job(client, job_a)["status"] == "done")
    st = _status(client)
    assert st["recording"]["active"] and st["recording"]["session_id"] == sid_b
    assert (env["transcripts_dir"] / f"{sid_a}_transcript.md").exists()
    assert not (env["transcripts_dir"] / f"{sid_b}_transcript.md").exists()
    # A's meta kept its title and B has its own meta
    assert json.loads((env["transcripts_dir"] / f"{sid_a}_meta.json").read_text())["title"] == "Meeting A"

    r = client.post("/api/stop")
    assert r.get_json()["session_id"] == sid_b
    assert (env["transcripts_dir"] / f"{sid_b}.wav").exists()
    assert transcribe_mock["claude_calls"] == 0


def test_failure_of_a_does_not_touch_b(client, isolated_env, transcribe_mock):
    env = isolated_env
    sid_a = _make_audio_session(env, "2026-01-01_10-00-00")
    sid_b = _make_audio_session(env, "2026-01-01_11-00-00")
    transcribe_mock["fail_for"].add(sid_a)
    ja = client.post("/api/queue", json={"session_id": sid_a}).get_json()["job"]["id"]
    jb = client.post("/api/queue", json={"session_id": sid_b}).get_json()["job"]["id"]
    assert client.post("/api/queue/run", json={}).status_code == 200
    assert _wait(lambda: _job(client, ja)["status"] == "failed")
    assert _wait(lambda: _job(client, jb)["status"] == "done")
    assert "boom" in _job(client, ja)["error"]
    assert not (env["transcripts_dir"] / f"{sid_a}_transcript.md").exists()
    assert (env["transcripts_dir"] / f"{sid_b}_transcript.md").exists()
    # sessions endpoint reports per-meeting job state independently
    sessions = {s["id"]: s for s in client.get("/api/sessions").get_json()["sessions"]}
    assert sessions[sid_a]["job"]["status"] == "failed"
    assert sessions[sid_b]["job"]["status"] == "done"


# ---------------------------------------------------------------------------
# Queue semantics
# ---------------------------------------------------------------------------

def test_only_one_transcription_at_a_time_and_snapshot_semantics(client, isolated_env, transcribe_mock):
    env = isolated_env
    sids = [_make_audio_session(env, f"2026-01-01_1{i}-00-00") for i in range(3)]
    ids = [client.post("/api/queue", json={"session_id": s}).get_json()["job"]["id"] for s in sids[:2]]
    transcribe_mock["gate"].clear()
    r = client.post("/api/queue/run", json={})
    assert r.status_code == 200
    assert r.get_json()["run"]["total"] == 2
    assert _wait(lambda: _job(client, ids[0])["status"] == "running")
    # Second run refused while one is active
    assert client.post("/api/queue/run", json={}).status_code == 409
    # A job added during the run is NOT picked up by it
    late = client.post("/api/queue", json={"session_id": sids[2]}).get_json()["job"]["id"]
    transcribe_mock["gate"].set()
    assert _wait(lambda: all(_job(client, j)["status"] == "done" for j in ids))
    assert _wait(lambda: not _status(client)["run"]["active"])
    assert _job(client, late)["status"] == "queued"
    assert transcribe_mock["max_active"] == 1
    # Run selected: only the late job
    r = client.post("/api/queue/run", json={"job_ids": [late]})
    assert r.status_code == 200
    assert _wait(lambda: _job(client, late)["status"] == "done")
    assert transcribe_mock["max_active"] == 1


def test_duplicate_and_overwrite_guards(client, isolated_env, transcribe_mock):
    env = isolated_env
    sid = _make_audio_session(env)
    assert client.post("/api/queue", json={"session_id": sid}).status_code == 201
    assert client.post("/api/queue", json={"session_id": sid}).status_code == 409  # duplicate
    client.post("/api/queue/run", json={})
    assert _wait(lambda: _status(client)["jobs"][0]["status"] == "done")
    # transcript exists now → refuse unless overwrite
    r = client.post("/api/queue", json={"session_id": sid})
    assert r.status_code == 400 and "already exists" in r.get_json()["error"]
    r = client.post("/api/queue", json={"session_id": sid, "overwrite": True, "fresh": True})
    assert r.status_code == 201
    assert r.get_json()["job"]["options"]["overwrite"] is True


def test_remove_retry_and_clear(client, isolated_env, transcribe_mock):
    env = isolated_env
    sid = _make_audio_session(env)
    jid = client.post("/api/queue", json={"session_id": sid}).get_json()["job"]["id"]
    assert client.delete(f"/api/queue/{jid}").status_code == 200
    assert _status(client)["jobs"] == []

    transcribe_mock["fail_for"].add(sid)
    jid = client.post("/api/queue", json={"session_id": sid}).get_json()["job"]["id"]
    client.post("/api/queue/run", json={})
    assert _wait(lambda: _job(client, jid)["status"] == "failed")
    transcribe_mock["fail_for"].clear()
    r = client.post(f"/api/queue/{jid}/retry")
    assert r.status_code == 200 and r.get_json()["job"]["status"] == "queued"
    # retry does not run by itself
    time.sleep(0.2)
    assert _job(client, jid)["status"] == "queued"
    client.post("/api/queue/run", json={})
    assert _wait(lambda: _job(client, jid)["status"] == "done")
    assert _job(client, jid)["attempts"] == 2
    assert client.post("/api/queue/clear-finished", json={}).get_json()["removed"] == 1


def test_stop_run_marks_interrupted_and_keeps_checkpoint_semantics(client, isolated_env, transcribe_mock):
    env = isolated_env
    sid = _make_audio_session(env)
    jid = client.post("/api/queue", json={"session_id": sid}).get_json()["job"]["id"]
    transcribe_mock["gate"].clear()
    client.post("/api/queue/run", json={})
    assert _wait(lambda: _job(client, jid)["status"] == "running")
    assert client.post("/api/queue/stop").status_code == 200
    transcribe_mock["gate"].set()
    assert _wait(lambda: _job(client, jid)["status"] == "interrupted")
    assert "Retry" in _job(client, jid)["error"] or "stopped" in _job(client, jid)["error"].lower()
    assert not (env["transcripts_dir"] / f"{sid}_transcript.md").exists()
    # explicit retry puts it back in the queue
    assert client.post(f"/api/queue/{jid}/retry").get_json()["job"]["status"] == "queued"


def test_queue_survives_restart_and_running_jobs_become_interrupted(client, isolated_env, transcribe_mock):
    import listener.web.app as webapp
    env = isolated_env
    sid = _make_audio_session(env)
    jid = client.post("/api/queue", json={"session_id": sid}).get_json()["job"]["id"]
    # simulate a crash while running: mark running directly, then "restart" the process
    jobsdb.mark_running(jid, "deadrun")
    webapp._reset_for_tests()
    st = _status(client)  # triggers recover_on_startup
    job = next(j for j in st["jobs"] if j["id"] == jid)
    assert job["status"] == "interrupted"
    assert "restarted" in job["error"]
    assert not st["run"]["active"]
    # nothing runs until the user retries + runs
    time.sleep(0.2)
    assert _job(client, jid)["status"] == "interrupted"


def test_import_saves_without_processing(client, isolated_env, transcribe_mock):
    import io
    env = isolated_env
    buf = io.BytesIO()
    import numpy as np
    import soundfile as sf
    sf.write(buf, np.zeros(8000, dtype="float32"), 16000, format="WAV")
    buf.seek(0)
    r = client.post("/api/import", data={"audio": (buf, "Standup notes.wav")},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert (env["transcripts_dir"] / f"{d['session_id']}.wav").exists()
    assert d["title"] == "Standup notes"
    assert _status(client)["jobs"] == []
    assert transcribe_mock["calls"] == []


def test_transcription_never_calls_claude_and_preserves_manual_title(client, isolated_env, transcribe_mock):
    env = isolated_env
    sid = _make_audio_session(env, title="Budget sync")
    jid = client.post("/api/queue", json={"session_id": sid, "language": "tr", "multilingual": True}).get_json()["job"]["id"]
    client.post("/api/queue/run", json={})
    assert _wait(lambda: _job(client, jid)["status"] == "done")
    assert transcribe_mock["claude_calls"] == 0
    assert transcribe_mock["calls"][0]["language"] == "tr"
    assert transcribe_mock["calls"][0]["multilingual"] is True
    meta = json.loads((env["transcripts_dir"] / f"{sid}_meta.json").read_text())
    assert meta["title"] == "Budget sync"
    assert meta["language"] == "en"  # from the transcriber result
    assert meta.get("transcribed_at")
    job = _job(client, jid)
    assert job["result"]["files"]["transcript"] == f"{sid}_transcript.md"
    assert job["title"] == "Budget sync"
    # indexed for search
    from listener.db import search_meetings
    assert any(r["session_id"] == sid for r in search_meetings("hello"))


# ---------------------------------------------------------------------------
# Live transcription contention policy
# ---------------------------------------------------------------------------

def test_live_transcription_skipped_while_run_active(client, isolated_env, transcribe_mock, monkeypatch):
    import listener.streaming as streaming
    started = []

    class FakeStreamer:
        def __init__(self, model_size="large-v3", language=None):
            started.append(self)

        def start(self):
            pass

        def stop(self):
            pass

        def feed_audio(self, *a):
            pass

    monkeypatch.setattr(streaming, "StreamingTranscriber", FakeStreamer)
    env = isolated_env
    sid = _make_audio_session(env)
    client.post("/api/queue", json={"session_id": sid})
    transcribe_mock["gate"].clear()
    client.post("/api/queue/run", json={})
    assert _wait(lambda: _status(client)["run"]["active"])

    r = client.post("/api/start", json={"live_transcription": True})
    assert r.status_code == 200
    assert r.get_json()["live"] is False
    assert "Whisper" in r.get_json()["live_note"]
    assert started == []  # no second Whisper instance
    client.post("/api/stop")
    transcribe_mock["gate"].set()
    assert _wait(lambda: not _status(client)["run"]["active"])

    # And the other direction: a run is refused while live transcription is active
    r = client.post("/api/start", json={"live_transcription": True})
    assert r.get_json()["live"] is True and len(started) == 1
    sid2 = _make_audio_session(env, "2026-01-02_10-00-00")
    client.post("/api/queue", json={"session_id": sid2})
    r = client.post("/api/queue/run", json={})
    assert r.status_code == 409 and "Live transcription" in r.get_json()["error"]
    client.post("/api/stop")
    assert client.post("/api/queue/run", json={}).status_code == 200


# ---------------------------------------------------------------------------
# Explicit Claude actions go through the claude lane
# ---------------------------------------------------------------------------

def test_analyze_is_explicit_job_and_reindexes(client, isolated_env, transcribe_mock, monkeypatch):
    env = isolated_env
    sid = _make_audio_session(env, title="Retro")
    jid = client.post("/api/queue", json={"session_id": sid}).get_json()["job"]["id"]
    client.post("/api/queue/run", json={})
    assert _wait(lambda: _job(client, jid)["status"] == "done")

    calls = []

    def fake_analyze(text, model="claude-sonnet-4-5", recipe_id=None):
        calls.append(recipe_id)
        return "## Summary\nAll good.\n\n## Topics Discussed\n- Budget\n"

    monkeypatch.setattr("listener.analyzer.analyze_transcript_sync", fake_analyze)
    r = client.post(f"/api/analyze/{sid}", json={"recipe_id": "sprint_retro"})
    assert r.status_code == 202, r.get_json()
    ajob = r.get_json()["job"]["id"]
    assert _wait(lambda: _job(client, ajob)["status"] == "done")
    assert calls == ["sprint_retro"]
    assert (env["transcripts_dir"] / f"{sid}_analysis.md").read_text().startswith("# Meeting Analysis")
    meta = json.loads((env["transcripts_dir"] / f"{sid}_meta.json").read_text())
    assert meta["recipe_id"] == "sprint_retro" and meta["title"] == "Retro"
    # duplicate while queued/running is refused; a second one after completion is fine
    r = client.post(f"/api/analyze/{sid}", json={"recipe_id": "sprint_retro"})
    assert r.status_code == 202
    assert _wait(lambda: _job(client, r.get_json()["job"]["id"])["status"] == "done")
    assert client.post("/api/analyze/nope", json={}).status_code == 404


def test_pipeline_atomic_writes_and_helpers(tmp_path):
    sid = "2026-03-04_05-06-07"
    assert pipeline.date_display(sid) == "2026-03-04 05:06"
    assert pipeline.iso_date(sid) == "2026-03-04T05:06:07"
    assert pipeline.fmt_duration(3725) == "1h 2m 5s"
    pipeline.update_meta(tmp_path, sid, title="x")
    pipeline.update_meta(tmp_path, sid, language="tr")
    assert pipeline.read_meta(tmp_path, sid) == {"title": "x", "language": "tr"}
    assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# Review regressions: atomic slot reservation, per-meeting exclusivity, stop
# after completion, meta write safety, import id collisions, denoise flag
# ---------------------------------------------------------------------------

def test_concurrent_starts_open_exactly_one_recorder(client, isolated_env, monkeypatch):
    """Two /api/start requests in flight: one 200, one 409, one microphone opened."""
    import listener.web.app as webapp

    def slow_start(self, output_path):
        time.sleep(0.3)  # widen the window between the check and the recorder opening
        self.path = output_path
        _write_wav(output_path)

    monkeypatch.setattr(FakeRecorder, "start", slow_start)
    results = []

    def go():
        c = webapp.app.test_client()
        r = c.post("/api/start", json={"device": None})
        results.append((r.status_code, r.get_json()))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert sorted(code for code, _ in results) == [200, 409], results
    assert len(FakeRecorder.instances) == 1
    assert client.post("/api/stop").status_code == 200
    assert FakeRecorder.instances[0].stopped


def test_run_refused_while_live_model_is_still_loading(client, isolated_env, transcribe_mock, monkeypatch):
    """The live intent is visible before the Whisper model finishes loading (no TOCTOU)."""
    import listener.streaming as streaming
    import listener.web.app as webapp

    entered, release = threading.Event(), threading.Event()

    class SlowStreamer:
        def __init__(self, model_size="large-v3", language=None):
            pass

        def start(self):
            entered.set()
            release.wait(timeout=5)

        def stop(self):
            pass

        def feed_audio(self, *a):
            pass

    monkeypatch.setattr(streaming, "StreamingTranscriber", SlowStreamer)
    sid = _make_audio_session(isolated_env)
    client.post("/api/queue", json={"session_id": sid})

    out = {}

    def go():
        out["r"] = webapp.app.test_client().post("/api/start", json={"live_transcription": True})

    t = threading.Thread(target=go)
    t.start()
    assert entered.wait(timeout=5)
    # While the live model loads: the run is refused, a second start is refused, stop says "starting"
    r = client.post("/api/queue/run", json={})
    assert r.status_code == 409 and "Live transcription" in r.get_json()["error"]
    assert client.post("/api/start", json={}).status_code == 409
    assert client.post("/api/stop").status_code == 409
    assert _status(client)["recording"]["active"] is True
    assert transcribe_mock["calls"] == []  # no Whisper job started underneath the live model
    release.set()
    t.join(timeout=5)
    assert out["r"].status_code == 200 and out["r"].get_json()["live"] is True
    assert client.post("/api/stop").status_code == 200
    assert client.post("/api/queue/run", json={}).status_code == 200


def test_transcribe_is_skipped_while_analyze_runs_for_same_meeting(client, isolated_env, transcribe_mock,
                                                                   monkeypatch):
    """Two jobs never write one meeting's files at the same time; the loser fails visibly."""
    env = isolated_env
    sid = _make_audio_session(env)
    jid = client.post("/api/queue", json={"session_id": sid}).get_json()["job"]["id"]
    client.post("/api/queue/run", json={})
    assert _wait(lambda: _job(client, jid)["status"] == "done")

    gate = threading.Event()

    def slow_analyze(text, model="claude-sonnet-4-5", recipe_id=None):
        gate.wait(timeout=5)
        return "## Summary\nok\n"

    monkeypatch.setattr("listener.analyzer.analyze_transcript_sync", slow_analyze)
    # re-transcribe queued first, then an analysis starts immediately in the Claude lane
    rj = client.post("/api/queue", json={"session_id": sid, "overwrite": True}).get_json()["job"]["id"]
    aj = client.post(f"/api/analyze/{sid}", json={}).get_json()["job"]["id"]
    assert _wait(lambda: _job(client, aj)["status"] == "running")
    assert client.post("/api/queue/run", json={}).status_code == 200
    assert _wait(lambda: _job(client, rj)["status"] == "failed")
    assert "Skipped" in _job(client, rj)["error"] and "analyze" in _job(client, rj)["error"]
    assert len(transcribe_mock["calls"]) == 1  # the re-transcribe never touched Whisper
    gate.set()
    assert _wait(lambda: _job(client, aj)["status"] == "done")
    # explicit retry works once the meeting is free
    assert client.post(f"/api/queue/{rj}/retry").get_json()["job"]["status"] == "queued"
    client.post("/api/queue/run", json={})
    assert _wait(lambda: _job(client, rj)["status"] == "done")
    assert len(transcribe_mock["calls"]) == 2


def test_stop_request_after_work_finished_keeps_the_result(client, isolated_env, transcribe_mock, monkeypatch):
    """Stop pressed during the final 'saving' step must not turn a finished job into 'interrupted'."""
    import listener.web.app as webapp

    env = isolated_env
    sid = _make_audio_session(env)
    jid = client.post("/api/queue", json={"session_id": sid}).get_json()["job"]["id"]
    real_index = pipeline.index_session

    def index_then_stop(*a, **k):
        webapp._runner.stop_run()  # stop arrives after the transcript is already written
        return real_index(*a, **k)

    monkeypatch.setattr(pipeline, "index_session", index_then_stop)
    client.post("/api/queue/run", json={})
    assert _wait(lambda: _job(client, jid)["status"] in ("done", "interrupted"))
    assert _job(client, jid)["status"] == "done"
    assert (env["transcripts_dir"] / f"{sid}_transcript.md").exists()
    assert _wait(lambda: not _status(client)["run"]["active"])


def test_update_meta_is_safe_under_concurrent_writers(tmp_path):
    sid = "2026-03-04_05-06-07"
    pipeline.update_meta(tmp_path, sid, title="keep me")
    errors = []

    def writer(i):
        try:
            for _ in range(5):
                pipeline.update_meta(tmp_path, sid, **{f"k{i}": i})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []
    meta = pipeline.read_meta(tmp_path, sid)
    assert meta["title"] == "keep me"
    assert all(meta.get(f"k{i}") == i for i in range(12))
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_imports_get_distinct_session_ids(client, isolated_env):
    import io
    import listener.web.app as webapp

    wav = isolated_env["transcripts_dir"] / "_src.wav"
    _write_wav(wav)
    payload = wav.read_bytes()
    wav.unlink()
    results = []

    def go(name):
        c = webapp.app.test_client()
        r = c.post("/api/import", data={"audio": (io.BytesIO(payload), name)},
                   content_type="multipart/form-data")
        results.append(r.get_json())

    threads = [threading.Thread(target=go, args=(f"meeting-{i}.wav",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    ids = [r["session_id"] for r in results]
    assert len(set(ids)) == 3, ids
    for r in results:
        assert (isolated_env["transcripts_dir"] / f"{r['session_id']}.wav").exists()
        meta = json.loads((isolated_env["transcripts_dir"] / f"{r['session_id']}_meta.json").read_text())
        assert meta["source"] == "import" and meta["title"] == r["title"]


def test_denoise_off_uses_the_original_audio(client, isolated_env, transcribe_mock):
    env = isolated_env
    sid = _make_audio_session(env)
    _write_wav(env["transcripts_dir"] / f"{sid}_cleaned.wav")  # cached denoised file exists
    jid = client.post("/api/queue", json={"session_id": sid, "denoise": False}).get_json()["job"]["id"]
    client.post("/api/queue/run", json={})
    assert _wait(lambda: _job(client, jid)["status"] == "done")
    assert transcribe_mock["calls"][-1]["audio_path"].endswith(f"{sid}.wav")
    meta = json.loads((env["transcripts_dir"] / f"{sid}_meta.json").read_text())
    assert meta["denoised"] is False
    # default (denoise on) reuses the cached cleaned file
    jid2 = client.post("/api/queue", json={"session_id": sid, "overwrite": True}).get_json()["job"]["id"]
    client.post("/api/queue/run", json={})
    assert _wait(lambda: _job(client, jid2)["status"] == "done")
    assert transcribe_mock["calls"][-1]["audio_path"].endswith(f"{sid}_cleaned.wav")


def test_run_rejects_malformed_job_ids(client, isolated_env):
    r = client.post("/api/queue/run", json={"job_ids": [1, None]})
    assert r.status_code == 400 and "job id" in r.get_json()["error"].lower()
    assert client.post("/api/queue/run", json={"job_ids": "abc"}).status_code == 400
