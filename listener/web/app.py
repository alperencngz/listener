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

from flask import Flask, render_template, jsonify, request, send_from_directory, Response, stream_with_context

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB max upload
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
_streamer = None  # StreamingTranscriber instance for live transcription

# In-memory chat sessions, keyed by session_id
_chat_sessions: dict[str, "ChatSession"] = {}

_INTERNAL_KEYS = {"start_time", "_audio_path", "_language", "_model_size", "_skip_analysis", "_recipe_id", "_denoise"}


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

    global _streamer
    data = request.json or {}
    device_val = data.get("device")
    device = int(device_val) if device_val is not None and device_val != "" else None
    language = data.get("language") or None
    model_size = data.get("model_size", "large-v3")
    skip_analysis = data.get("skip_analysis", False)
    live_transcription = data.get("live_transcription", False)
    recipe_id = data.get("recipe_id") or None
    denoise = data.get("denoise", True)  # Default: noise reduction enabled

    from listener.recorder import Recorder

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    audio_path = str(OUTPUT_DIR / f"{session_id}.wav")

    # Set up streaming transcriber if live mode is requested
    audio_hook = None
    if live_transcription:
        try:
            from listener.streaming import StreamingTranscriber
            streamer = StreamingTranscriber(
                model_size=model_size,
                language=language,
            )
            streamer.start()
            _streamer = streamer
            audio_hook = streamer.feed_audio
            logger.info("Live transcription enabled for session %s", session_id)
        except Exception as e:
            logger.warning("Could not start live transcription: %s", e)
            _streamer = None

    recorder = Recorder(device=device, on_audio=audio_hook)
    try:
        recorder.start(audio_path)
    except Exception as e:
        if _streamer:
            _streamer.stop()
            _streamer = None
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
            "_recipe_id": recipe_id,
            "_denoise": denoise,
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
        recipe_id = _state.get("_recipe_id")
        denoise = _state.get("_denoise", True)
        session_id = _state["session_id"]

        _state["status"] = "processing"
        _state["step"] = "transcribing"
        _state["start_time"] = None

    recorder.stop()

    # Stop live transcription if active
    if _streamer is not None:
        try:
            _streamer.stop()
        except Exception as e:
            logger.warning("Error stopping live transcription: %s", e)
        _streamer = None

    threading.Thread(
        target=_process_recording,
        args=(audio_path, session_id, language, model_size, skip_analysis, recipe_id, denoise),
        daemon=True,
    ).start()

    return jsonify({"ok": True})


@app.route("/api/import", methods=["POST"])
def api_import():
    """Import an existing audio file for transcription and analysis."""
    with _lock:
        if _state["status"] in ("recording", "processing"):
            return jsonify({"error": f"Cannot import while {_state['status']}"}), 400

    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]
    if not audio_file.filename:
        return jsonify({"error": "No file selected"}), 400

    skip_analysis = request.form.get("skip_analysis") == "true"
    recipe_id = request.form.get("recipe_id") or None
    denoise = request.form.get("denoise", "true") == "true"
    language = request.form.get("language") or None

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    audio_path = str(OUTPUT_DIR / f"{session_id}.wav")

    # Save uploaded file
    audio_file.save(audio_path)

    with _lock:
        _state.update({
            "status": "processing",
            "session_id": session_id,
            "start_time": None,
            "step": "transcribing",
            "error": None,
            "files": {},
            "title": None,
        })

    threading.Thread(
        target=_process_recording,
        args=(audio_path, session_id, language, "large-v3", skip_analysis, recipe_id, denoise),
        daemon=True,
    ).start()

    return jsonify({"session_id": session_id})


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
                session_map[sid] = {"id": sid, "files": {}, "title": "", "duration": 0, "language": "", "recipe_id": ""}
            try:
                meta = json.loads(f.read_text())
                session_map[sid]["title"] = meta.get("title", "")
                session_map[sid]["duration"] = meta.get("duration", 0)
                session_map[sid]["language"] = meta.get("language", "")
                session_map[sid]["recipe_id"] = meta.get("recipe_id", "")
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


@app.route("/api/analytics/<session_id>")
def api_analytics(session_id):
    """Return analytics data for a session from its meta.json."""
    meta_path = OUTPUT_DIR / f"{session_id}_meta.json"
    if not meta_path.exists():
        return jsonify({"error": "Session not found"}), 404
    try:
        meta = json.loads(meta_path.read_text())
        analytics = meta.get("analytics", {})
        if not analytics:
            return jsonify({"error": "No analytics available (diarization may not have run)"}), 404
        return jsonify(analytics)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Recipes API (F3)
# ---------------------------------------------------------------------------

@app.route("/api/recipes")
def api_recipes():
    """List all available analysis recipes."""
    from listener.recipes import load_recipes
    recipes = load_recipes()
    return jsonify([r.to_dict() for r in recipes])


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
        return jsonify({"error": "Empty message"}), 400
    transcript_file = OUTPUT_DIR / f"{session_id}_transcript.md"
    if not transcript_file.exists():
        return jsonify({"error": "Transcript not found"}), 404
    transcript = transcript_file.read_text()
    if session_id not in _chat_sessions:
        _chat_sessions[session_id] = ChatSession(transcript=transcript)
    chat = _chat_sessions[session_id]
    try:
        response = chat.ask_sync(message)
    except Exception as e:
        logger.error("Chat error for session %s: %s", session_id, e)
        return jsonify({"error": f"Chat failed: {str(e)}"}), 500
    return jsonify({"response": response, "history_length": len(chat.history)})


@app.route("/api/chat/<session_id>/history")
def api_chat_history(session_id):
    """Get chat history for a session."""
    chat = _chat_sessions.get(session_id)
    return jsonify({"history": chat.history if chat else []})


@app.route("/api/chat/<session_id>/clear", methods=["POST"])
def api_chat_clear(session_id):
    """Clear chat history for a session."""
    _chat_sessions.pop(session_id, None)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Webhooks API (F10)
# ---------------------------------------------------------------------------

@app.route("/api/webhooks")
def api_webhooks_list():
    """List all configured webhooks."""
    from listener.webhooks import list_webhooks
    return jsonify(list_webhooks())


@app.route("/api/webhooks", methods=["POST"])
def api_webhooks_add():
    """Add a new webhook."""
    from listener.webhooks import add_webhook
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "URL must start with http:// or https://"}), 400
    events = data.get("events", ["session_complete"])
    fmt = data.get("format", "json")
    if fmt not in ("json", "slack", "markdown"):
        return jsonify({"error": "Format must be json, slack, or markdown"}), 400
    webhook = add_webhook(url, events=events, format=fmt)
    return jsonify(webhook), 201


@app.route("/api/webhooks/<webhook_id>", methods=["DELETE"])
def api_webhooks_delete(webhook_id):
    """Remove a webhook by ID."""
    from listener.webhooks import remove_webhook
    if remove_webhook(webhook_id):
        return jsonify({"ok": True})
    return jsonify({"error": "Webhook not found"}), 404


@app.route("/api/webhooks/test/<webhook_id>", methods=["POST"])
def api_webhooks_test(webhook_id):
    """Send a test payload to a specific webhook."""
    from listener.webhooks import build_test_payload
    result = build_test_payload(webhook_id, base_url=request.host_url.rstrip("/"))
    if result is None:
        return jsonify({"error": "Webhook not found"}), 404
    return jsonify(result)


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


# ---------------------------------------------------------------------------
# Live Transcription SSE (F9)
# ---------------------------------------------------------------------------

@app.route("/api/live-stream")
def api_live_stream():
    """Server-Sent Events endpoint for real-time transcript updates.

    Returns a stream of SSE events with partial transcription results.
    Each event is a JSON object with:
      - finalized: list of {start, end, text} segments (confirmed)
      - tentative: string (current unconfirmed text, may change)
      - elapsed: float (seconds since recording started)
      - done: bool (true when recording/transcription is complete)
    """
    def generate():
        streamer = _streamer
        if streamer is None:
            # No live transcription active — send error and close
            data = json.dumps({"error": "No live transcription active", "done": True})
            yield f"data: {data}\n\n"
            return

        for update in streamer.updates():
            data = {
                "finalized": [
                    {"start": s.start, "end": s.end, "text": s.text}
                    for s in update.finalized_segments
                ],
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
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
        },
    )


# ---------------------------------------------------------------------------
# Multi-Format Export (F6)
# ---------------------------------------------------------------------------

@app.route("/api/export/<session_id>")
def api_export(session_id):
    """Export a session in the requested format (docx, pdf, srt, json)."""
    fmt = request.args.get("format", "").lower()
    if fmt not in ("docx", "pdf", "srt", "json"):
        return jsonify({"error": f"Unsupported format: {fmt}. Use: docx, pdf, srt, json"}), 400

    # Load session data
    meta_path = OUTPUT_DIR / f"{session_id}_meta.json"
    transcript_path = OUTPUT_DIR / f"{session_id}_transcript.md"
    analysis_path = OUTPUT_DIR / f"{session_id}_analysis.md"

    if not transcript_path.exists():
        return jsonify({"error": "Transcript not found"}), 404

    transcript_text = transcript_path.read_text()
    analysis_text = analysis_path.read_text() if analysis_path.exists() else None

    # Parse metadata
    title = f"Meeting {session_id}"
    duration_seconds = 0.0
    language = ""
    language_confidence = 0.0
    recipe_id = ""
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            title = meta.get("title", title)
            duration_seconds = meta.get("duration", 0.0)
            language = meta.get("language", "")
            language_confidence = meta.get("language_probability", 0.0)
            recipe_id = meta.get("recipe_id", "") or ""
        except Exception:
            pass

    # Format display strings
    parts = session_id.split("_")
    date_str = parts[0] + " " + (parts[1] if len(parts) > 1 else "").replace("-", ":")
    duration_str = _fmt_duration(duration_seconds) if duration_seconds else ""

    from listener.export import EXPORTERS
    exporter = EXPORTERS[fmt]

    # Build kwargs — SRT and JSON accept extra params
    kwargs = dict(
        session_id=session_id,
        title=title,
        date_str=date_str,
        duration_str=duration_str,
        language=language,
        transcript_text=transcript_text,
        analysis_text=analysis_text,
    )

    if fmt == "json":
        kwargs["duration_seconds"] = duration_seconds
        kwargs["language_confidence"] = language_confidence
        kwargs["recipe_id"] = recipe_id

    try:
        buf = exporter(**kwargs)
    except Exception as e:
        logger.error("Export failed for %s as %s: %s", session_id, fmt, e)
        return jsonify({"error": f"Export failed: {str(e)}"}), 500

    # MIME types and file extensions
    mime_map = {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "srt": "text/srt; charset=utf-8",
        "json": "application/json; charset=utf-8",
    }
    ext_map = {"docx": ".docx", "pdf": ".pdf", "srt": ".srt", "json": ".json"}

    filename = f"{session_id}{ext_map[fmt]}"
    return Response(
        buf.read(),
        mimetype=mime_map[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------

def _process_recording(audio_path, session_id, language, model_size, skip_analysis, recipe_id=None, denoise=True):
    try:
        from listener.transcriber import transcribe

        # F7: Noise preprocessing
        transcribe_path = audio_path  # default: use original audio
        if denoise:
            try:
                with _lock:
                    _state["step"] = "denoising"
                from listener.preprocessor import preprocess_audio
                cleaned_path = audio_path.replace(".wav", "_cleaned.wav")
                transcribe_path = preprocess_audio(audio_path, cleaned_path)
                logger.info("Audio denoised for session %s", session_id)
            except Exception as e:
                logger.warning("Noise reduction failed (continuing with original): %s", e)
                transcribe_path = audio_path

        with _lock:
            _state["step"] = "transcribing"

        result = transcribe(transcribe_path, model_size=model_size, language=language)

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

        # Compute analytics (requires diarized segments from F1)
        analytics_data = {}
        speaker_info = {}
        if result.has_speakers:
            from listener.diarizer import compute_talk_times
            talk_times = compute_talk_times(result.segments)
            speaker_info = {
                label: {"talk_time_seconds": round(secs, 1)}
                for label, secs in sorted(talk_times.items())
            }

            # F8: Meeting analytics
            try:
                from listener.analytics import compute_analytics
                analytics = compute_analytics(result.segments, result.duration)
                analytics_data = analytics.to_dict()
            except Exception as e:
                logger.warning("Analytics computation failed: %s", e)

        meta = {
            "title": title,
            "language": result.language,
            "language_probability": result.language_probability,
            "duration": result.duration,
            "speakers": speaker_info,
            "analytics": analytics_data,
            "recipe_id": recipe_id,
        }
        (OUTPUT_DIR / f"{session_id}_meta.json").write_text(json.dumps(meta, ensure_ascii=False))

        # Analysis
        if not skip_analysis:
            with _lock:
                _state["step"] = "analyzing"

            from listener.analyzer import analyze_transcript_sync

            analysis = analyze_transcript_sync(transcript_text, recipe_id=recipe_id)

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

            # F8: Extract topics from analysis and add to analytics
            if analytics_data:
                try:
                    from listener.analytics import extract_topics_from_analysis
                    topics = extract_topics_from_analysis(analysis)
                    if topics:
                        analytics_data["topics"] = topics
                        # Re-save meta with topics
                        meta["analytics"] = analytics_data
                        (OUTPUT_DIR / f"{session_id}_meta.json").write_text(
                            json.dumps(meta, ensure_ascii=False)
                        )
                except Exception as e:
                    logger.warning("Topic extraction failed: %s", e)

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

        # F10: Fire webhooks asynchronously
        try:
            from listener.webhooks import fire_webhooks
            webhook_meta = dict(meta)  # copy meta dict
            # Attach analysis text for summary/action_items extraction
            analysis_file = files.get("analysis")
            if analysis_file:
                try:
                    webhook_meta["_analysis_text"] = (OUTPUT_DIR / analysis_file).read_text()[:1000]
                except Exception:
                    pass
            fire_webhooks(
                event="session_complete",
                session_id=session_id,
                meta=webhook_meta,
                files=files,
                base_url="http://127.0.0.1:8642",
            )
        except Exception as e:
            logger.warning("Webhook firing failed: %s", e)

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

    # F4: Backfill any existing sessions into search index
    try:
        from listener.db import backfill_from_transcripts
        count = backfill_from_transcripts(OUTPUT_DIR)
        if count:
            logger.info("Backfilled %d sessions into search index", count)
    except Exception as e:
        logger.warning("Search index backfill failed: %s", e)

    app.run(host="127.0.0.1", port=port, debug=debug, threaded=True)
