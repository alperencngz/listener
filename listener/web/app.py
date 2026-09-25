"""Flask web interface for Listener.

Localhost UI for recording, queueing transcription, running explicit Claude
analysis / memory generation, and browsing meetings. Runs on port 8642.

State model (all independent of each other):

* **Recording** — at most one microphone recording at a time
  (``_recording``). Stopping a recording only saves the WAV file.
* **Processing queue** — persistent job rows (``listener.jobs``). Transcription
  jobs run only when the user starts a run; Claude jobs run when the user
  clicks the corresponding action. One transcription job at a time.
* **Meetings** — files under ``OUTPUT_DIR`` are the source of truth.

Recording B while A is being transcribed is therefore an ordinary situation:
the recorder and the job runner never share state.
"""

import json
import logging
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, render_template, jsonify, request, send_from_directory, Response, stream_with_context

from listener import jobs as jobsdb
from listener.jobs import JobError, JobRunner
from listener.pipeline import (
    default_title,
    fmt_duration,
    read_meta,
    run_analyze_job,
    run_memory_job,
    run_transcribe_job,
    session_paths,
    update_meta,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB max upload
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("./transcripts")

# ---------------------------------------------------------------------------
# Recording state (independent from processing)
# ---------------------------------------------------------------------------

_rec_lock = threading.Lock()
_RECORDING_IDLE = {
    "active": False,
    "starting": False,    # slot reserved, microphone/live model still opening
    "session_id": None,
    "start_time": None,
    "audio_path": None,
    "device": None,
    "live": False,        # live transcription running (or about to) for this recording
    "live_note": None,    # why live transcription is off / degraded, if requested
}
_recording = dict(_RECORDING_IDLE)
_recorder = None
_streamer = None  # StreamingTranscriber instance for live transcription

# Session ids are allocated and claimed on disk under this lock so two
# starts/imports in the same second never share an id.
_sid_lock = threading.Lock()

# In-memory chat sessions, keyed by session_id
_chat_sessions: dict[str, "ChatSession"] = {}

# ---------------------------------------------------------------------------
# Job runner (executors read OUTPUT_DIR at call time so tests can swap it)
# ---------------------------------------------------------------------------

_runner = JobRunner({
    "transcribe": lambda job, ctx: run_transcribe_job(job, ctx, OUTPUT_DIR),
    "analyze": lambda job, ctx: run_analyze_job(job, ctx, OUTPUT_DIR),
    "memory": lambda job, ctx: run_memory_job(job, ctx, OUTPUT_DIR),
})

_init_lock = threading.Lock()
_initialized = False

LIVE_BLOCKED_BY_RUN = (
    "Live transcription was skipped because a transcription job is running "
    "(only one Whisper instance at a time). The recording itself is unaffected."
)
RUN_BLOCKED_BY_LIVE = (
    "Live transcription is active for the current recording. Stop the recording "
    "(or start it without live transcription) before running the queue."
)


def _ensure_init() -> None:
    """Once per process: mark jobs left running by a previous process as interrupted."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        try:
            jobsdb.recover_on_startup()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Job recovery on startup failed: %s", exc)
        _initialized = True


def _reset_for_tests() -> None:
    """Forget process-level state (used by the test-suite between cases)."""
    global _initialized, _recorder, _streamer, _runner
    _initialized = False
    _recorder = None
    _streamer = None
    _chat_sessions.clear()
    with _rec_lock:
        _recording.update(_RECORDING_IDLE)
    _runner = JobRunner({
        "transcribe": lambda job, ctx: run_transcribe_job(job, ctx, OUTPUT_DIR),
        "analyze": lambda job, ctx: run_analyze_job(job, ctx, OUTPUT_DIR),
        "memory": lambda job, ctx: run_memory_job(job, ctx, OUTPUT_DIR),
    })


def _err(message: str, code: int = 400):
    return jsonify({"error": message}), code


def _new_session_id() -> str:
    """Timestamp id, bumped by a second until no file with that stem exists."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    for _ in range(120):
        sid = now.strftime("%Y-%m-%d_%H-%M-%S")
        if not any(OUTPUT_DIR.glob(f"{sid}*")):
            return sid
        now += timedelta(seconds=1)
    return now.strftime("%Y-%m-%d_%H-%M-%S")


def _reserve_session_id(**meta_fields) -> str:
    """Allocate a fresh session id and claim it by writing its meta file, atomically."""
    with _sid_lock:
        session_id = _new_session_id()
        update_meta(OUTPUT_DIR, session_id, **meta_fields)
    return session_id


def _session_title(session_id: str, cache: dict | None = None) -> str:
    if cache is not None and session_id in cache:
        return cache[session_id]
    title = read_meta(OUTPUT_DIR, session_id).get("title") or default_title(session_id)
    if cache is not None:
        cache[session_id] = title
    return title


def _job_view(job: dict, cache: dict | None = None) -> dict:
    view = dict(job)
    view["title"] = _session_title(job["session_id"], cache)
    return view


def _jobs_payload(limit: int = 100) -> list[dict]:
    cache: dict = {}
    return [_job_view(j, cache) for j in jobsdb.list_jobs(limit=limit)]


def _recording_payload() -> dict:
    with _rec_lock:
        rec = dict(_recording)
    rec["elapsed"] = (time.time() - rec["start_time"]) if rec["active"] and rec["start_time"] else 0
    rec.pop("start_time", None)
    rec.pop("audio_path", None)
    return rec


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@app.route("/api/devices")
def api_devices():
    from listener.recorder import list_input_devices
    return jsonify(list_input_devices())


@app.route("/api/status")
def api_status():
    _ensure_init()
    return jsonify({
        "recording": _recording_payload(),
        "run": _runner.run_status(),
        "claude": _runner.claude_status(),
        "jobs": _jobs_payload(),
    })


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

@app.route("/api/start", methods=["POST"])
def api_start():
    """Start a microphone recording. Independent of any processing job."""
    global _recorder, _streamer
    _ensure_init()
    data = request.json or {}
    device_val = data.get("device")
    device = int(device_val) if device_val is not None and device_val != "" else None
    language = data.get("language") or None
    model_size = data.get("model_size", "large-v3")
    live_requested = bool(data.get("live_transcription", False))

    from listener.recorder import Recorder

    # Reserve the recording slot, the session id and the live-transcription
    # intent in one critical section. From here on a second /api/start gets
    # 409 and /api/queue/run sees "live" (so no second Whisper instance can be
    # started while the live model is still loading). Policy under contention:
    # recording always wins; if a transcription job is running, live mode is
    # skipped and the recording proceeds without live text.
    with _rec_lock:
        if _recording["active"]:
            return _err(f"Already recording session {_recording['session_id']}. Stop it first.", 409)
        live = False
        live_note = None
        if live_requested:
            if _runner.transcription_active():
                live_note = LIVE_BLOCKED_BY_RUN
            else:
                live = True
        try:
            session_id = _reserve_session_id(
                recorded_at=datetime.now().isoformat(timespec="seconds"), device=device, source="recording",
            )
        except Exception as exc:  # noqa: BLE001
            return _err(f"Could not create the meeting files: {exc}", 500)
        audio_path = str(session_paths(OUTPUT_DIR, session_id)["audio"])
        _recording.update({
            "active": True, "starting": True, "session_id": session_id, "start_time": None,
            "audio_path": audio_path, "device": device, "live": live, "live_note": live_note,
        })

    audio_hook = None
    streamer = None
    if live:
        try:
            from listener.streaming import StreamingTranscriber
            streamer = StreamingTranscriber(model_size=model_size, language=language)
            streamer.start()
            audio_hook = streamer.feed_audio
            logger.info("Live transcription enabled for session %s", session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not start live transcription: %s", exc)
            live = False
            live_note = f"Live transcription unavailable: {exc}"
            streamer = None

    recorder = Recorder(device=device, on_audio=audio_hook)
    try:
        recorder.start(audio_path)
    except Exception as exc:  # noqa: BLE001
        if streamer is not None:
            try:
                streamer.stop()
            except Exception:
                pass
        with _rec_lock:
            _recording.update(_RECORDING_IDLE)
        session_paths(OUTPUT_DIR, session_id)["meta"].unlink(missing_ok=True)
        return _err(f"Could not start recording: {exc}", 500)

    with _rec_lock:
        _recorder = recorder
        _streamer = streamer
        _recording.update({"starting": False, "start_time": time.time(), "live": live, "live_note": live_note})

    return jsonify({"session_id": session_id, "live": live, "live_note": live_note})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Stop the recording and save the WAV. Does not queue or process anything."""
    global _recorder, _streamer
    with _rec_lock:
        if not _recording["active"]:
            return _err("Not currently recording", 400)
        if _recording["starting"]:
            return _err("The recording is still starting. Try again in a moment.", 409)
        recorder = _recorder
        streamer = _streamer
        session_id = _recording["session_id"]
        audio_path = _recording["audio_path"]
        _recorder = None
        _streamer = None
        _recording.update(_RECORDING_IDLE)

    try:
        recorder.stop()
    except Exception as exc:  # noqa: BLE001
        logger.error("Recorder stop failed for %s: %s", session_id, exc)

    if streamer is not None:
        try:
            streamer.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error stopping live transcription: %s", exc)

    duration = 0.0
    try:
        import soundfile as sf
        duration = float(sf.info(audio_path).duration)
    except Exception:
        pass
    try:
        update_meta(OUTPUT_DIR, session_id, duration=duration,
                    stopped_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not update meta for %s: %s", session_id, exc)

    return jsonify({"ok": True, "session_id": session_id, "audio": Path(audio_path).name,
                    "duration": duration})


@app.route("/api/import", methods=["POST"])
def api_import():
    """Save an uploaded audio file as a new meeting. Nothing is processed."""
    _ensure_init()
    if "audio" not in request.files:
        return _err("No audio file provided")
    audio_file = request.files["audio"]
    if not audio_file.filename:
        return _err("No file selected")

    imported_at = datetime.now().isoformat(timespec="seconds")
    session_id = _reserve_session_id(source="import", imported_at=imported_at,
                                     original_filename=audio_file.filename)
    paths = session_paths(OUTPUT_DIR, session_id)
    audio_path = str(paths["audio"])

    original_ext = Path(audio_file.filename).suffix.lower() or ".wav"
    temp_path = str(OUTPUT_DIR / f"{session_id}_original{original_ext}")
    try:
        audio_file.save(temp_path)
        if original_ext == ".wav":
            shutil.move(temp_path, audio_path)
        else:
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", temp_path, "-ar", "16000", "-ac", "1", audio_path],
                    capture_output=True, check=True,
                )
                Path(temp_path).unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ffmpeg conversion failed, using original file: %s", exc)
                shutil.move(temp_path, audio_path)
    except Exception as exc:  # noqa: BLE001
        Path(temp_path).unlink(missing_ok=True)
        paths["meta"].unlink(missing_ok=True)
        return _err(f"Could not save the audio file: {exc}", 500)

    duration = 0.0
    try:
        import soundfile as sf
        duration = float(sf.info(audio_path).duration)
    except Exception:
        pass
    title = Path(audio_file.filename).stem.strip() or default_title(session_id)
    update_meta(OUTPUT_DIR, session_id, title=title, duration=duration)

    return jsonify({"session_id": session_id, "title": title, "duration": duration,
                    "files": {"audio": paths["audio"].name}})


# ---------------------------------------------------------------------------
# Processing queue
# ---------------------------------------------------------------------------

def _transcribe_options(data: dict) -> dict:
    opts = {
        "language": data.get("language") or None,
        "model_size": data.get("model_size") or "large-v3",
        "denoise": bool(data.get("denoise", True)),
        "multilingual": bool(data.get("multilingual", False)),
        "overwrite": bool(data.get("overwrite", False)),
        "fresh": bool(data.get("fresh", False)),
    }
    if "diarize" in data:
        opts["diarize"] = data.get("diarize")
    return opts


def _enqueue_transcribe(session_id: str, data: dict) -> dict:
    paths = session_paths(OUTPUT_DIR, session_id)
    if not paths["audio"].exists():
        raise JobError("Audio file not found for this meeting.")
    with _rec_lock:
        if _recording["active"] and _recording["session_id"] == session_id:
            raise JobError("This meeting is still being recorded. Stop the recording first.")
    opts = _transcribe_options(data)
    if paths["transcript"].exists() and not opts["overwrite"]:
        raise JobError(
            "A transcript already exists for this meeting. Choose 'Re-transcribe' to overwrite it."
        )
    return jobsdb.enqueue(session_id, "transcribe", opts)


@app.route("/api/queue", methods=["GET"])
def api_queue_list():
    _ensure_init()
    return jsonify({"jobs": _jobs_payload(200), "run": _runner.run_status(), "claude": _runner.claude_status()})


@app.route("/api/queue", methods=["POST"])
def api_queue_add():
    """Add a transcription job for a meeting. It waits until you run the queue."""
    _ensure_init()
    data = request.json or {}
    session_id = (data.get("session_id") or "").strip()
    kind = data.get("kind", "transcribe")
    if not session_id:
        return _err("session_id is required")
    if kind != "transcribe":
        return _err("Use /api/analyze or /api/memory/<id>/generate for Claude actions.")
    try:
        job = _enqueue_transcribe(session_id, data.get("options") or data)
    except jobsdb.DuplicateJob as exc:
        return _err(str(exc), 409)
    except JobError as exc:
        return _err(str(exc), 409 if isinstance(exc, jobsdb.MeetingBusy) else 400)
    return jsonify({"job": _job_view(job)}), 201


@app.route("/api/process/<session_id>", methods=["POST"])
def api_process(session_id):
    """Compatibility alias: queue transcription (does not start it)."""
    _ensure_init()
    try:
        job = _enqueue_transcribe(session_id, request.json or {})
    except JobError as exc:
        return _err(str(exc), 409 if isinstance(exc, (jobsdb.DuplicateJob, jobsdb.MeetingBusy)) else 400)
    return jsonify({"job": _job_view(job), "queued": True, "note": "Queued. Start the run to process it."}), 201


@app.route("/api/queue/run", methods=["POST"])
def api_queue_run():
    """Run selected (job_ids given) or all queued transcription jobs, sequentially."""
    _ensure_init()
    data = request.json or {}
    job_ids = data.get("job_ids")
    if job_ids is not None and not isinstance(job_ids, list):
        return _err("job_ids must be a list")
    # Held while the run starts: /api/start checks the run under the same lock.
    with _rec_lock:
        if _recording["active"] and _recording["live"]:
            return _err(RUN_BLOCKED_BY_LIVE, 409)
        try:
            run = _runner.start_run(job_ids)
        except jobsdb.RunActive as exc:
            return _err(str(exc), 409)
        except JobError as exc:
            return _err(str(exc), 400)
    return jsonify({"run": run})


@app.route("/api/queue/stop", methods=["POST"])
def api_queue_stop():
    """Ask the current run to stop after the current segment (checkpoint kept)."""
    stopped = _runner.stop_run()
    if not stopped:
        return _err("No run is active", 400)
    return jsonify({"ok": True, "note": "Stopping after the current segment. The job will show as interrupted."})


@app.route("/api/queue/<job_id>/retry", methods=["POST"])
def api_queue_retry(job_id):
    """Put a failed/interrupted job back in the queue. Nothing runs until you run the queue."""
    _ensure_init()
    try:
        job = jobsdb.retry_job(job_id)
    except JobError as exc:
        return _err(str(exc), 400)
    if job["kind"] in jobsdb.CLAUDE_KINDS and (request.json or {}).get("run_now", True):
        _runner.start_claude_job(job["id"])
        job = jobsdb.get_job(job["id"])
    return jsonify({"job": _job_view(job)})


@app.route("/api/queue/<job_id>/run", methods=["POST"])
def api_queue_run_one(job_id):
    """Run one queued job now (Claude jobs immediately; transcribe jobs as a one-job run)."""
    _ensure_init()
    job = jobsdb.get_job(job_id)
    if not job:
        return _err("Job not found", 404)
    try:
        if job["kind"] in jobsdb.CLAUDE_KINDS:
            _runner.start_claude_job(job_id)
            return jsonify({"job": _job_view(jobsdb.get_job(job_id))})
        with _rec_lock:
            if _recording["active"] and _recording["live"]:
                return _err(RUN_BLOCKED_BY_LIVE, 409)
            run = _runner.start_run([job_id])
        return jsonify({"run": run})
    except jobsdb.RunActive as exc:
        return _err(str(exc), 409)
    except JobError as exc:
        return _err(str(exc), 400)


@app.route("/api/queue/<job_id>", methods=["DELETE"])
def api_queue_remove(job_id):
    try:
        removed = jobsdb.remove_job(job_id)
    except JobError as exc:
        return _err(str(exc), 409)
    if not removed:
        return _err("Job not found", 404)
    return jsonify({"ok": True})


@app.route("/api/queue/clear-finished", methods=["POST"])
def api_queue_clear_finished():
    data = request.json or {}
    statuses = tuple(data.get("statuses") or ("done",))
    bad = [s for s in statuses if s not in jobsdb.FINISHED_STATUSES]
    if bad:
        return _err(f"Cannot clear jobs with status: {', '.join(bad)}")
    return jsonify({"removed": jobsdb.clear_finished(statuses)})


# ---------------------------------------------------------------------------
# Explicit Claude actions (analysis, memory)
# ---------------------------------------------------------------------------

def _start_claude_action(session_id: str, kind: str, options: dict):
    paths = session_paths(OUTPUT_DIR, session_id)
    if not paths["transcript"].exists():
        return _err("Transcript not found. Transcribe the meeting first.", 404)
    try:
        job = jobsdb.enqueue(session_id, kind, options)
    except jobsdb.DuplicateJob as exc:
        return _err(str(exc), 409)
    except JobError as exc:
        return _err(str(exc), 409)
    _runner.start_claude_job(job["id"])
    return jsonify({"job": _job_view(jobsdb.get_job(job["id"]))}), 202


@app.route("/api/analyze/<session_id>", methods=["POST"])
@app.route("/api/reanalyze/<session_id>", methods=["POST"])
def api_analyze(session_id):
    """Run a recipe analysis with Claude for one meeting (explicit)."""
    _ensure_init()
    data = request.json or {}
    return _start_claude_action(session_id, "analyze", {"recipe_id": data.get("recipe_id") or None})


@app.route("/api/memory/<session_id>/generate", methods=["POST"])
def api_memory_generate(session_id):
    """Generate or update the meeting memory with Claude for one meeting (explicit)."""
    _ensure_init()
    return _start_claude_action(session_id, "memory", {})


# ---------------------------------------------------------------------------
# Meeting memory (read/edit; no Claude except /ask)
# ---------------------------------------------------------------------------

@app.route("/api/memory")
def api_memory_list():
    from listener import memory
    ids = request.args.getlist("session_id") or None
    return jsonify({"memories": memory.list_memories(ids)})


@app.route("/api/memory/search")
def api_memory_search():
    from listener import memory
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    return jsonify(memory.search_memory(q, limit=min(request.args.get("limit", 20, type=int), 50)))


@app.route("/api/memory/tasks", methods=["GET"])
def api_memory_tasks():
    from listener import memory
    ids = request.args.getlist("session_id") or None
    status = request.args.get("status") or None
    project_id = request.args.get("project_id") or None
    include_stale = request.args.get("include_stale", "true") != "false"
    return jsonify({"tasks": memory.list_tasks(session_ids=ids, status=status, project_id=project_id,
                                               include_stale=include_stale)})


@app.route("/api/memory/tasks", methods=["POST"])
def api_memory_task_add():
    from listener import memory
    data = request.json or {}
    session_id = (data.get("session_id") or "").strip()
    text = (data.get("text") or "").strip()
    if not session_id or not text:
        return _err("session_id and text are required")
    task = memory.add_task(session_id, text, owner=data.get("owner") or None, deadline=data.get("deadline") or None)
    return jsonify({"task": task}), 201


@app.route("/api/memory/tasks/<task_id>", methods=["PATCH"])
def api_memory_task_update(task_id):
    from listener import memory
    data = request.json or {}
    fields = {k: data[k] for k in ("text", "owner", "deadline", "status") if k in data}
    if not fields:
        return _err("Nothing to update")
    try:
        task = memory.update_task(task_id, **fields)
    except KeyError:
        return _err("Task not found", 404)
    except ValueError as exc:
        return _err(str(exc), 400)
    return jsonify({"task": task})


@app.route("/api/memory/tasks/<task_id>", methods=["DELETE"])
def api_memory_task_delete(task_id):
    from listener import memory
    if not memory.delete_task(task_id):
        return _err("Task not found", 404)
    return jsonify({"ok": True})


@app.route("/api/memory/projects", methods=["GET"])
def api_memory_projects():
    from listener import memory
    return jsonify({"projects": memory.list_projects()})


@app.route("/api/memory/projects", methods=["POST"])
def api_memory_project_create():
    from listener import memory
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return _err("name is required")
    try:
        project = memory.create_project(name, data.get("session_ids") or [])
    except ValueError as exc:  # empty or duplicate name
        return _err(str(exc), 409)
    return jsonify({"project": project}), 201


@app.route("/api/memory/projects/<project_id>", methods=["PUT"])
def api_memory_project_update(project_id):
    from listener import memory
    data = request.json or {}
    if "session_ids" not in data:
        return _err("session_ids is required")
    try:
        project = memory.set_project_meetings(project_id, data["session_ids"] or [])
    except KeyError:
        return _err("Project not found", 404)
    except ValueError as exc:
        return _err(str(exc), 400)
    return jsonify({"project": project})


@app.route("/api/memory/projects/<project_id>", methods=["DELETE"])
def api_memory_project_delete(project_id):
    from listener import memory
    if not memory.delete_project(project_id):
        return _err("Project not found", 404)
    return jsonify({"ok": True})


@app.route("/api/memory/ask", methods=["POST"])
def api_memory_ask():
    """Ask Claude about the selected meetings using their stored memory (bounded, cited)."""
    from listener import memory
    data = request.json or {}
    question = (data.get("question") or "").strip()
    session_ids = [s for s in (data.get("session_ids") or []) if s]
    project_id = data.get("project_id") or None
    if not question:
        return _err("question is required")
    if not session_ids and not project_id:
        return _err("Select at least one meeting or a project")
    try:
        result = memory.ask(question, session_ids, project_id=project_id,
                            max_chars=int(data.get("max_chars") or 40000), transcripts_dir=OUTPUT_DIR)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        logger.error("Memory ask failed: %s", exc)
        return _err(f"Ask failed: {exc}", 500)
    return jsonify(result)


@app.route("/api/memory/<session_id>", methods=["GET"])
def api_memory_get(session_id):
    from listener import memory
    record = memory.get_memory(session_id)
    if record is None:
        generations = memory.list_generations(session_id)
        return jsonify({"error": "No memory generated for this meeting yet", "generations": generations}), 404
    return jsonify(record)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

@app.route("/api/sessions")
def api_sessions():
    _ensure_init()
    if not OUTPUT_DIR.exists():
        return jsonify({"sessions": [], "total": 0, "has_more": False})

    offset = request.args.get("offset", 0, type=int)
    limit = request.args.get("limit", 30, type=int)

    session_map: dict[str, dict] = {}

    def entry(sid: str) -> dict:
        if sid not in session_map:
            session_map[sid] = {"id": sid, "files": {}, "title": "", "duration": 0, "language": "",
                                "recipe_id": "", "has_memory": False}
        return session_map[sid]

    for f in sorted(OUTPUT_DIR.iterdir(), reverse=True):
        if f.suffix not in (".md", ".wav", ".json"):
            continue
        name = f.stem
        # Skip internal/cache files
        if "_cleaned" in name or name.endswith("_waveform") or name.endswith("_checkpoint") or "_original" in name:
            continue

        if name.endswith("_meta") and f.suffix == ".json":
            sid = name[:-5]
            s = entry(sid)
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
                s["title"] = meta.get("title", "")
                s["duration"] = meta.get("duration", 0)
                s["language"] = meta.get("language", "")
                s["recipe_id"] = meta.get("recipe_id", "") or ""
            except Exception:
                pass
            continue

        if name.endswith("_memory"):
            sid = name[:-7]
            s = entry(sid)
            if f.suffix == ".md":
                s["files"]["memory"] = f.name
            s["has_memory"] = True
            continue

        for sfx in ("_transcript", "_analysis"):
            if name.endswith(sfx):
                name = name[: -len(sfx)]
                break
        s = entry(name)
        if f.suffix == ".wav":
            s["files"]["audio"] = f.name
        elif "transcript" in f.stem:
            s["files"]["transcript"] = f.name
        elif "analysis" in f.stem:
            s["files"]["analysis"] = f.name

    by_session = jobsdb.jobs_by_session()
    with _rec_lock:
        recording_sid = _recording["session_id"] if _recording["active"] else None
    for sid, s in session_map.items():
        job = by_session.get(sid)
        s["job"] = ({"id": job["id"], "kind": job["kind"], "status": job["status"], "stage": job["stage"],
                     "error": job["error"], "progress": job["progress"]} if job else None)
        s["recording"] = sid == recording_sid

    sorted_sessions = sorted(session_map.values(), key=lambda s: s["id"], reverse=True)
    total = len(sorted_sessions)
    page = sorted_sessions[offset:offset + limit]
    return jsonify({"sessions": page, "total": total, "has_more": offset + limit < total})


@app.route("/api/view/<filename>")
def api_view(filename):
    """Return file contents as plain text for inline display."""
    fpath = OUTPUT_DIR / filename
    if not fpath.exists() or fpath.suffix not in (".md", ".json"):
        return _err("Not found", 404)
    return fpath.read_text(encoding="utf-8"), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/api/download/<filename>")
def api_download(filename):
    return send_from_directory(OUTPUT_DIR.resolve(), filename, as_attachment=True)


@app.route("/api/speakers/<session_id>")
def api_speakers(session_id):
    """Return speaker info for a session from its meta.json."""
    return jsonify({"speakers": read_meta(OUTPUT_DIR, session_id).get("speakers", {})})


@app.route("/api/analytics/<session_id>")
def api_analytics(session_id):
    """Return analytics data for a session from its meta.json."""
    meta_path = OUTPUT_DIR / f"{session_id}_meta.json"
    if not meta_path.exists():
        return _err("Session not found", 404)
    analytics = read_meta(OUTPUT_DIR, session_id).get("analytics", {})
    if not analytics:
        return _err("No analytics available (diarization may not have run)", 404)
    return jsonify(analytics)


# ---------------------------------------------------------------------------
# Recipes API (F3)
# ---------------------------------------------------------------------------

@app.route("/api/recipes")
def api_recipes():
    """List all available analysis recipes."""
    from listener.recipes import load_recipes
    return jsonify([r.to_dict() for r in load_recipes()])


# ---------------------------------------------------------------------------
# Chat with Transcript (F2)
# ---------------------------------------------------------------------------

@app.route("/api/chat/<session_id>", methods=["POST"])
def api_chat(session_id):
    """Send a chat message about a transcript and get AI response."""
    from listener.chat import ChatSession

    data = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return _err("Empty message")
    transcript_file = OUTPUT_DIR / f"{session_id}_transcript.md"
    if not transcript_file.exists():
        return _err("Transcript not found", 404)
    transcript = transcript_file.read_text(encoding="utf-8")
    if session_id not in _chat_sessions:
        _chat_sessions[session_id] = ChatSession(transcript=transcript)
    chat = _chat_sessions[session_id]
    try:
        response = chat.ask_sync(message)
    except Exception as exc:  # noqa: BLE001
        logger.error("Chat error for session %s: %s", session_id, exc)
        return _err(f"Chat failed: {exc}", 500)
    return jsonify({"response": response, "history_length": len(chat.history)})


@app.route("/api/chat/<session_id>/history")
def api_chat_history(session_id):
    chat = _chat_sessions.get(session_id)
    return jsonify({"history": chat.history if chat else []})


@app.route("/api/chat/<session_id>/clear", methods=["POST"])
def api_chat_clear(session_id):
    _chat_sessions.pop(session_id, None)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Webhooks API (F10)
# ---------------------------------------------------------------------------

@app.route("/api/webhooks")
def api_webhooks_list():
    from listener.webhooks import list_webhooks
    return jsonify(list_webhooks())


@app.route("/api/webhooks", methods=["POST"])
def api_webhooks_add():
    from listener.webhooks import add_webhook
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return _err("URL is required")
    if not url.startswith(("http://", "https://")):
        return _err("URL must start with http:// or https://")
    events = data.get("events", ["session_complete"])
    fmt = data.get("format", "json")
    if fmt not in ("json", "slack", "markdown"):
        return _err("Format must be json, slack, or markdown")
    return jsonify(add_webhook(url, events=events, format=fmt)), 201


@app.route("/api/webhooks/<webhook_id>", methods=["DELETE"])
def api_webhooks_delete(webhook_id):
    from listener.webhooks import remove_webhook
    if remove_webhook(webhook_id):
        return jsonify({"ok": True})
    return _err("Webhook not found", 404)


@app.route("/api/webhooks/test/<webhook_id>", methods=["POST"])
def api_webhooks_test(webhook_id):
    from listener.webhooks import build_test_payload
    result = build_test_payload(webhook_id, base_url=request.host_url.rstrip("/"))
    if result is None:
        return _err("Webhook not found", 404)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Waveform Peaks API
# ---------------------------------------------------------------------------

@app.route("/api/waveform/<session_id>")
def api_waveform(session_id):
    """Return precomputed waveform peaks for the audio file (cached as JSON)."""
    import numpy as np

    cache_path = OUTPUT_DIR / f"{session_id}_waveform.json"
    if cache_path.exists():
        return cache_path.read_text(), 200, {"Content-Type": "application/json"}

    audio_path = OUTPUT_DIR / f"{session_id}.wav"
    if not audio_path.exists():
        return _err("Audio not found", 404)

    try:
        import soundfile as sf
        data, samplerate = sf.read(str(audio_path), dtype="float32")
        if data.ndim > 1:
            data = data[:, 0]
        num_peaks = 500
        samples_per_peak = max(1, len(data) // num_peaks)
        peaks = []
        for i in range(num_peaks):
            start = i * samples_per_peak
            end = min(start + samples_per_peak, len(data))
            if start >= len(data):
                break
            peaks.append(float(np.max(np.abs(data[start:end]))))
        max_peak = max(peaks) if peaks else 1.0
        if max_peak > 0:
            peaks = [round(p / max_peak, 3) for p in peaks]
        result = json.dumps({"peaks": peaks, "duration": len(data) / samplerate})
        cache_path.write_text(result)
        return result, 200, {"Content-Type": "application/json"}
    except Exception as exc:  # noqa: BLE001
        logger.error("Waveform generation failed for %s: %s", session_id, exc)
        return _err(str(exc), 500)


# ---------------------------------------------------------------------------
# Session Management (delete, rename)
# ---------------------------------------------------------------------------

@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def api_delete_session(session_id):
    """Delete all files, jobs, index rows and memory for a session."""
    _ensure_init()
    with _rec_lock:
        if _recording["active"] and _recording["session_id"] == session_id:
            return _err("This meeting is being recorded. Stop the recording first.", 409)
    if any(j["status"] == "running" for j in jobsdb.list_jobs(session_id=session_id)):
        return _err("A job is running for this meeting. Stop the run first.", 409)

    deleted, errors = [], []
    files_to_delete = list(OUTPUT_DIR.glob(f"{session_id}*"))
    logger.info("Deleting session %s: %d files found", session_id, len(files_to_delete))
    for fpath in files_to_delete:
        if fpath.is_file():
            try:
                fpath.unlink()
                deleted.append(fpath.name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to delete %s: %s", fpath.name, exc)
                errors.append({"file": fpath.name, "error": str(exc)})
    try:
        from listener.db import DB_LOCK, get_db
        conn = get_db()
        with DB_LOCK:
            conn.execute("DELETE FROM meetings WHERE session_id = ?", (session_id,))
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to delete search index entry for %s: %s", session_id, exc)
    for job in jobsdb.list_jobs(session_id=session_id):
        try:
            jobsdb.remove_job(job["id"])
        except JobError:
            pass
    try:
        from listener import memory
        memory.delete_memory(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to delete memory for %s: %s", session_id, exc)
    _chat_sessions.pop(session_id, None)
    return jsonify({"deleted": deleted, "errors": errors})


@app.route("/api/sessions/<session_id>/rename", methods=["POST"])
def api_rename_session(session_id):
    """Rename a session's title (works before and after transcription)."""
    data = request.json or {}
    new_title = data.get("title", "").strip()
    if not new_title:
        return _err("Title cannot be empty")
    paths = session_paths(OUTPUT_DIR, session_id)
    if not paths["audio"].exists() and not paths["transcript"].exists():
        return _err("Session not found", 404)
    try:
        update_meta(OUTPUT_DIR, session_id, title=new_title)
    except OSError as exc:
        return _err(f"Could not save the new title: {exc}", 500)
    try:
        from listener.db import DB_LOCK, get_db
        conn = get_db()
        with DB_LOCK:
            conn.execute("UPDATE meetings SET title = ? WHERE session_id = ?", (new_title, session_id))
            conn.commit()
    except Exception:
        pass
    try:
        from listener import memory
        memory.rename_meeting(session_id, new_title)
    except Exception:
        pass
    return jsonify({"ok": True, "title": new_title})


# ---------------------------------------------------------------------------
# Full-Text Search (F4)
# ---------------------------------------------------------------------------

@app.route("/api/search")
def api_search():
    from listener.db import search_meetings
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    limit = request.args.get("limit", 20, type=int)
    return jsonify(search_meetings(q, limit=min(limit, 50)))


# ---------------------------------------------------------------------------
# Live Transcription SSE (F9)
# ---------------------------------------------------------------------------

@app.route("/api/live-stream")
def api_live_stream():
    """Server-Sent Events endpoint for real-time transcript updates."""
    def generate():
        streamer = _streamer
        if streamer is None:
            yield f"data: {json.dumps({'error': 'No live transcription active', 'done': True})}\n\n"
            return
        for update in streamer.updates():
            data = {
                "finalized": [{"start": s.start, "end": s.end, "text": s.text} for s in update.finalized_segments],
                "tentative": update.tentative_text,
                "elapsed": round(update.elapsed, 1),
                "done": update.done,
            }
            if update.error:
                data["error"] = update.error
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            if update.done:
                return

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Multi-Format Export (F6)
# ---------------------------------------------------------------------------

@app.route("/api/export/<session_id>")
def api_export(session_id):
    """Export a session in the requested format (docx, pdf, srt, json)."""
    fmt = request.args.get("format", "").lower()
    if fmt not in ("docx", "pdf", "srt", "json"):
        return _err(f"Unsupported format: {fmt}. Use: docx, pdf, srt, json")

    paths = session_paths(OUTPUT_DIR, session_id)
    if not paths["transcript"].exists():
        return _err("Transcript not found", 404)

    transcript_text = paths["transcript"].read_text(encoding="utf-8")
    analysis_text = paths["analysis"].read_text(encoding="utf-8") if paths["analysis"].exists() else None

    meta = read_meta(OUTPUT_DIR, session_id)
    title = meta.get("title") or f"Meeting {session_id}"
    duration_seconds = meta.get("duration", 0.0) or 0.0
    language = meta.get("language", "") or ""
    language_confidence = meta.get("language_probability", 0.0) or 0.0
    recipe_id = meta.get("recipe_id", "") or ""

    parts = session_id.split("_")
    date_str = parts[0] + " " + (parts[1] if len(parts) > 1 else "").replace("-", ":")
    duration_str = fmt_duration(duration_seconds) if duration_seconds else ""

    from listener.export import EXPORTERS
    exporter = EXPORTERS[fmt]
    kwargs = dict(
        session_id=session_id, title=title, date_str=date_str, duration_str=duration_str,
        language=language, transcript_text=transcript_text, analysis_text=analysis_text,
    )
    if fmt == "json":
        kwargs["duration_seconds"] = duration_seconds
        kwargs["language_confidence"] = language_confidence
        kwargs["recipe_id"] = recipe_id

    try:
        buf = exporter(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.error("Export failed for %s as %s: %s", session_id, fmt, exc)
        return _err(f"Export failed: {exc}", 500)

    mime_map = {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "srt": "text/srt; charset=utf-8",
        "json": "application/json; charset=utf-8",
    }
    ext_map = {"docx": ".docx", "pdf": ".pdf", "srt": ".srt", "json": ".json"}
    filename = f"{session_id}{ext_map[fmt]}"
    return Response(buf.read(), mimetype=mime_map[fmt],
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(port=8642, debug=False):
    """Start the web server."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_init()

    # F4: Backfill any existing sessions into search index
    try:
        from listener.db import backfill_from_transcripts
        count = backfill_from_transcripts(OUTPUT_DIR)
        if count:
            logger.info("Backfilled %d sessions into search index", count)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Search index backfill failed: %s", exc)

    # Flask's debug reloader would start two processes (two recorders); keep it off.
    app.run(host="127.0.0.1", port=port, debug=debug, use_reloader=False, threaded=True)
