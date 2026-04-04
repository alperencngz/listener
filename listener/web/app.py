"""Flask web interface for the conference listening tool.

Provides a localhost UI for recording, transcribing,
and analyzing meetings. Runs on port 8642.
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, jsonify, request, send_from_directory

app = Flask(__name__)
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("./transcripts")

# ---------------------------------------------------------------------------
# Global state — single-user local tool, no DB needed
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_state = {
    "status": "idle",       # idle | recording | processing | done | error
    "session_id": None,
    "start_time": None,
    "step": None,            # transcribing | titling | analyzing
    "error": None,
    "files": {},
    "title": None,
}
_recorder = None

_INTERNAL_KEYS = {"start_time", "_audio_path", "_language", "_model_size", "_skip_analysis"}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route("/api/devices")
def api_devices():
    from listener.recorder import list_input_devices
    return jsonify(list_input_devices())


@app.route("/api/status")
def api_status():
    with _lock:
        s = {k: v for k, v in _state.items() if k not in _INTERNAL_KEYS}
    if _state["status"] == "recording" and _state["start_time"]:
        s["elapsed"] = time.time() - _state["start_time"]
    else:
        s["elapsed"] = 0
    return jsonify(s)


@app.route("/api/start", methods=["POST"])
def api_start():
    global _recorder
    with _lock:
        if _state["status"] not in ("idle", "done", "error"):
            return jsonify({"error": f"Cannot start while {_state['status']}"}), 400

    data = request.json or {}
    device_val = data.get("device")
    device = int(device_val) if device_val is not None and device_val != "" else None
    language = data.get("language") or None
    model_size = data.get("model_size", "large-v3")
    skip_analysis = data.get("skip_analysis", False)

    from listener.recorder import Recorder

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    audio_path = str(OUTPUT_DIR / f"{session_id}.wav")

    recorder = Recorder(device=device)
    try:
        recorder.start(audio_path)
    except Exception as e:
        return jsonify({"error": f"Could not start recording: {e}"}), 500

    with _lock:
        _recorder = recorder
        _state.update({
            "status": "recording",
            "session_id": session_id,
            "start_time": time.time(),
            "step": None,
            "error": None,
            "files": {},
            "title": None,
            "_audio_path": audio_path,
            "_language": language,
            "_model_size": model_size,
            "_skip_analysis": skip_analysis,
        })

    return jsonify({"session_id": session_id})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global _recorder
    with _lock:
        if _state["status"] != "recording":
            return jsonify({"error": "Not currently recording"}), 400

        recorder = _recorder
        _recorder = None
        audio_path = _state["_audio_path"]
        language = _state["_language"]
        model_size = _state["_model_size"]
        skip_analysis = _state["_skip_analysis"]
        session_id = _state["session_id"]

        _state["status"] = "processing"
        _state["step"] = "transcribing"
        _state["start_time"] = None

    recorder.stop()

    threading.Thread(
        target=_process_recording,
        args=(audio_path, session_id, language, model_size, skip_analysis),
        daemon=True,
    ).start()

    return jsonify({"ok": True})


@app.route("/api/sessions")
def api_sessions():
    if not OUTPUT_DIR.exists():
        return jsonify([])

    session_map: dict[str, dict] = {}

    for f in sorted(OUTPUT_DIR.iterdir(), reverse=True):
        if f.suffix not in (".md", ".wav", ".json"):
            continue

        name = f.stem

        # Meta files
        if name.endswith("_meta") and f.suffix == ".json":
            sid = name[:-5]
            if sid not in session_map:
                session_map[sid] = {"id": sid, "files": {}, "title": "", "duration": 0, "language": ""}
            try:
                meta = json.loads(f.read_text())
                session_map[sid]["title"] = meta.get("title", "")
                session_map[sid]["duration"] = meta.get("duration", 0)
                session_map[sid]["language"] = meta.get("language", "")
            except Exception:
                pass
            continue

        # Strip known suffixes to get session id
        for sfx in ("_transcript", "_analysis"):
            if name.endswith(sfx):
                name = name[: -len(sfx)]
                break

        if name not in session_map:
            session_map[name] = {"id": name, "files": {}, "title": "", "duration": 0, "language": ""}

        if f.suffix == ".wav":
            session_map[name]["files"]["audio"] = f.name
        elif "transcript" in f.stem:
            session_map[name]["files"]["transcript"] = f.name
        elif "analysis" in f.stem:
            session_map[name]["files"]["analysis"] = f.name

    sorted_sessions = sorted(session_map.values(), key=lambda s: s["id"], reverse=True)
    return jsonify(sorted_sessions[:30])


@app.route("/api/view/<filename>")
def api_view(filename):
    """Return file contents as plain text for inline display."""
    fpath = OUTPUT_DIR / filename
    if not fpath.exists() or fpath.suffix not in (".md", ".json"):
        return jsonify({"error": "Not found"}), 404
    return fpath.read_text(), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/api/download/<filename>")
def api_download(filename):
    return send_from_directory(OUTPUT_DIR.resolve(), filename, as_attachment=True)


@app.route("/api/speakers/<session_id>")
def api_speakers(session_id):
    """Return speaker info for a session from its meta.json."""
    meta_path = OUTPUT_DIR / f"{session_id}_meta.json"
    if not meta_path.exists():
        return jsonify({"speakers": {}})
    try:
        meta = json.loads(meta_path.read_text())
        return jsonify({"speakers": meta.get("speakers", {})})
    except Exception:
        return jsonify({"speakers": {}})


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------

def _process_recording(audio_path, session_id, language, model_size, skip_analysis):
    try:
        from listener.transcriber import transcribe

        with _lock:
            _state["step"] = "transcribing"

        result = transcribe(audio_path, model_size=model_size, language=language)

        if not result.segments:
            with _lock:
                _state["status"] = "error"
                _state["error"] = "No speech detected in the recording"
                _state["step"] = None
            return

        # Speaker diarization (if HF token is available)
        try:
            from listener.diarizer import get_hf_token, diarize, align_speakers
            hf_token = get_hf_token()
            if hf_token:
                with _lock:
                    _state["step"] = "diarizing"
                diarization = diarize(audio_path, hf_token=hf_token)
                result.segments = align_speakers(diarization, result.segments)
                logger.info("Speaker diarization complete for session %s", session_id)
        except Exception as e:
            logger.warning("Diarization failed (continuing without): %s", e)

        # Save transcript
        transcript_text = result.to_timestamped_text()
        duration_str = _fmt_duration(result.duration)
        dt = datetime.strptime(session_id, "%Y-%m-%d_%H-%M-%S")
        date_display = dt.strftime("%Y-%m-%d %H:%M")

        transcript_md = (
            f"# Meeting Transcript -- {date_display}\n\n"
            f"**Duration:** {duration_str}  \n"
            f"**Language:** {result.language} "
            f"({result.language_probability:.0%} confidence)\n\n"
            f"---\n\n"
            f"{transcript_text}\n"
        )
        transcript_filename = f"{session_id}_transcript.md"
        (OUTPUT_DIR / transcript_filename).write_text(transcript_md)

        files = {"transcript": transcript_filename, "audio": f"{session_id}.wav"}

        # Generate title
        title = f"Meeting {date_display}"
        try:
            with _lock:
                _state["step"] = "titling"
            from listener.analyzer import generate_title_sync
            title = generate_title_sync(transcript_text)
        except Exception as e:
            logger.warning("Title generation failed: %s", e)

        # Save metadata (include speaker info)
        speaker_info = {}
        if result.has_speakers:
            from listener.diarizer import compute_talk_times
            talk_times = compute_talk_times(result.segments)
            speaker_info = {
                label: {"talk_time_seconds": round(secs, 1)}
                for label, secs in sorted(talk_times.items())
            }

        meta = {
            "title": title,
            "language": result.language,
            "language_probability": result.language_probability,
            "duration": result.duration,
            "speakers": speaker_info,
        }
        (OUTPUT_DIR / f"{session_id}_meta.json").write_text(json.dumps(meta, ensure_ascii=False))

        # Analysis
        if not skip_analysis:
            with _lock:
                _state["step"] = "analyzing"

            from listener.analyzer import analyze_transcript_sync

            analysis = analyze_transcript_sync(transcript_text)

            analysis_md = (
                f"# Meeting Analysis -- {date_display}\n\n"
                f"**Duration:** {duration_str}  \n"
                f"**Language:** {result.language}\n\n"
                f"---\n\n"
                f"{analysis}\n\n"
                f"---\n\n"
                f"# Full Transcript\n\n"
                f"{transcript_text}\n"
            )
            analysis_filename = f"{session_id}_analysis.md"
            (OUTPUT_DIR / analysis_filename).write_text(analysis_md)
            files["analysis"] = analysis_filename

        with _lock:
            _state["status"] = "done"
            _state["step"] = None
            _state["files"] = files
            _state["title"] = title

    except Exception as e:
        with _lock:
            _state["status"] = "error"
            _state["error"] = str(e)
            _state["step"] = None


def _fmt_duration(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(port=8642, debug=False):
    """Start the web server."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=port, debug=debug, threaded=True)
