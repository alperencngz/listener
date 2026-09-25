"""Job executors: the actual work behind each queue job kind.

Each executor takes the job row and a ``JobContext`` (stage/progress/stop
hooks) and returns a small result dict. They are plain functions so the CLI
and tests can call them without the web app.

* ``run_transcribe_job`` — denoise (optional) → Whisper (resumable) → speaker
  diarization (if a HuggingFace token is configured) → transcript/meta files →
  search index. **No Claude calls happen here.**
* ``run_analyze_job`` — recipe analysis with Claude (explicit action).
* ``run_memory_job`` — meeting memory generation with Claude (explicit action).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from listener.jobs import JobContext, JobInterrupted

logger = logging.getLogger(__name__)

DEFAULT_MODEL_SIZE = "large-v3"

# Serialises read-modify-write of <id>_meta.json inside this process (request
# threads, the run thread and the Claude thread can all touch the same meta).
META_LOCK = threading.RLock()


class PipelineError(Exception):
    """A job could not be completed; the message is shown to the user."""


# ---------------------------------------------------------------------------
# Small helpers shared by executors and the web app
# ---------------------------------------------------------------------------

def fmt_duration(seconds: float) -> str:
    seconds = float(seconds or 0)
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


def date_display(session_id: str) -> str:
    try:
        return datetime.strptime(session_id, "%Y-%m-%d_%H-%M-%S").strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return session_id


def iso_date(session_id: str) -> str:
    parts = session_id.split("_")
    if len(parts) > 1:
        return f"{parts[0]}T{parts[1].replace('-', ':')}"
    return parts[0]


def session_paths(output_dir: Path, session_id: str) -> dict[str, Path]:
    return {
        "audio": output_dir / f"{session_id}.wav",
        "cleaned": output_dir / f"{session_id}_cleaned.wav",
        "transcript": output_dir / f"{session_id}_transcript.md",
        "analysis": output_dir / f"{session_id}_analysis.md",
        "meta": output_dir / f"{session_id}_meta.json",
        "memory_md": output_dir / f"{session_id}_memory.md",
        "memory_json": output_dir / f"{session_id}_memory.json",
    }


def read_meta(output_dir: Path, session_id: str) -> dict:
    meta_path = session_paths(output_dir, session_id)["meta"]
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_meta(output_dir: Path, session_id: str, meta: dict) -> None:
    with META_LOCK:
        atomic_write_text(session_paths(output_dir, session_id)["meta"],
                          json.dumps(meta, ensure_ascii=False, indent=2))


def update_meta(output_dir: Path, session_id: str, *, defaults: dict | None = None, **fields) -> dict:
    """Merge fields into meta.json without dropping what is already there.

    ``defaults`` are only applied to keys that are missing or empty (used to
    keep a user-given title). The read-modify-write runs under ``META_LOCK`` so
    concurrent updates from different threads cannot drop each other's fields.
    """
    with META_LOCK:
        meta = read_meta(output_dir, session_id)
        for key, value in (defaults or {}).items():
            if not meta.get(key):
                meta[key] = value
        meta.update(fields)
        write_meta(output_dir, session_id, meta)
    return meta


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a uniquely named temp file in the same directory, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def default_title(session_id: str) -> str:
    return f"Meeting {date_display(session_id)}"


def index_session(output_dir: Path, session_id: str, transcript_text: str | None = None) -> None:
    """(Re)index one session in the SQLite FTS table from its files."""
    from listener.db import insert_meeting

    paths = session_paths(output_dir, session_id)
    if transcript_text is None:
        if not paths["transcript"].exists():
            return
        transcript_text = paths["transcript"].read_text(encoding="utf-8")
    analysis_text = paths["analysis"].read_text(encoding="utf-8") if paths["analysis"].exists() else ""
    meta = read_meta(output_dir, session_id)
    insert_meeting(
        session_id=session_id,
        title=meta.get("title", "") or "",
        date=iso_date(session_id),
        duration=float(meta.get("duration", 0) or 0),
        language=meta.get("language", "") or "",
        lang_confidence=float(meta.get("language_probability", 0) or 0),
        transcript=transcript_text,
        analysis=analysis_text,
        audio_path=str(paths["audio"]) if paths["audio"].exists() else "",
    )


def _clear_checkpoints(paths: dict[str, Path]) -> None:
    from listener.transcriber import _checkpoint_path

    for key in ("audio", "cleaned"):
        _checkpoint_path(str(paths[key])).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Transcribe
# ---------------------------------------------------------------------------

def run_transcribe_job(job: dict, ctx: JobContext, output_dir: Path) -> dict:
    """Transcribe one recording. Never calls Claude."""
    from listener.transcriber import TranscriptionInterrupted, transcribe

    session_id = job["session_id"]
    opts = job.get("options") or {}
    language = opts.get("language") or None
    model_size = opts.get("model_size") or DEFAULT_MODEL_SIZE
    denoise = bool(opts.get("denoise", True))
    overwrite = bool(opts.get("overwrite", False))
    fresh = bool(opts.get("fresh", False))
    multilingual = bool(opts.get("multilingual", False))
    diarize_opt = opts.get("diarize", "auto")

    paths = session_paths(output_dir, session_id)
    if not paths["audio"].exists():
        raise PipelineError(f"Audio file not found: {paths['audio'].name}")
    if paths["transcript"].exists() and not overwrite:
        raise PipelineError(
            "A transcript already exists for this meeting. Queue it again with "
            "'overwrite' to re-transcribe."
        )
    if fresh:
        _clear_checkpoints(paths)

    # 1. Noise reduction (cached in <id>_cleaned.wav; ignored when denoise is off)
    transcribe_path = paths["audio"]
    if denoise and paths["cleaned"].exists():
        transcribe_path = paths["cleaned"]
    elif denoise:
        ctx.set_stage("denoising")
        try:
            from listener.preprocessor import preprocess_audio
            transcribe_path = Path(preprocess_audio(str(paths["audio"]), str(paths["cleaned"])))
        except Exception as exc:  # noqa: BLE001 - denoise is best effort
            logger.warning("Noise reduction failed for %s (using original): %s", session_id, exc)
            transcribe_path = paths["audio"]

    if ctx.should_stop():
        raise JobInterrupted("Stopped before transcription started.")

    # 2. Whisper (resumes from checkpoint automatically)
    ctx.set_stage("transcribing")
    last_report = 0.0

    def on_progress(segments_done: int, last_end: float, duration: float) -> None:
        nonlocal last_report
        now = time.monotonic()
        if now - last_report < 2.0:
            return
        last_report = now
        ctx.set_progress({
            "segments": segments_done,
            "position_seconds": round(last_end, 1),
            "duration_seconds": round(duration, 1),
        })

    try:
        result = transcribe(
            str(transcribe_path), model_size=model_size, language=language,
            multilingual=multilingual, should_stop=ctx.should_stop, on_progress=on_progress,
        )
    except TranscriptionInterrupted as exc:
        raise JobInterrupted(str(exc)) from exc

    if not result.segments:
        raise PipelineError("No speech detected in the recording.")

    # 3. Speaker diarization (local pyannote; only if a HF token is configured)
    diarized = False
    if diarize_opt in (True, "auto", "true", 1):
        try:
            from listener.diarizer import align_speakers, diarize, get_hf_token
            hf_token = get_hf_token()
            if hf_token:
                ctx.set_stage("diarizing")
                diarization = diarize(str(paths["audio"]), hf_token=hf_token)
                result.segments = align_speakers(diarization, result.segments)
                diarized = True
        except Exception as exc:  # noqa: BLE001 - diarization is best effort
            logger.warning("Diarization failed for %s (continuing without): %s", session_id, exc)

    # 4. Save transcript + meta (overwrite re-checked at write time)
    ctx.set_stage("saving")
    if paths["transcript"].exists() and not overwrite:
        raise PipelineError("Transcript appeared while transcribing; not overwriting it.")

    transcript_text = result.to_timestamped_text()
    transcript_md = (
        f"# Meeting Transcript -- {date_display(session_id)}\n\n"
        f"**Duration:** {fmt_duration(result.duration)}  \n"
        f"**Language:** {result.language} "
        f"({result.language_probability:.0%} confidence)\n\n"
        f"---\n\n"
        f"{transcript_text}\n"
    )
    atomic_write_text(paths["transcript"], transcript_md)

    fields = {
        "language": result.language,
        "language_probability": result.language_probability,
        "duration": result.duration,
        "transcribed_at": datetime.now().isoformat(timespec="seconds"),
        "model_size": model_size,
        "diarized": diarized,
        "denoised": transcribe_path == paths["cleaned"],
    }
    speaker_info: dict = {}
    analytics_data: dict = read_meta(output_dir, session_id).get("analytics", {}) or {}
    if result.has_speakers:
        from listener.diarizer import compute_talk_times
        speaker_info = {
            label: {"talk_time_seconds": round(secs, 1)}
            for label, secs in sorted(compute_talk_times(result.segments).items())
        }
        try:
            from listener.analytics import compute_analytics
            analytics_data = compute_analytics(result.segments, result.duration).to_dict()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Analytics failed for %s: %s", session_id, exc)
    fields["speakers"] = speaker_info
    fields["analytics"] = analytics_data
    meta = update_meta(output_dir, session_id, defaults={"title": default_title(session_id)}, **fields)

    # 5. Search index (local SQLite)
    try:
        index_session(output_dir, session_id, transcript_md)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Search indexing failed for %s: %s", session_id, exc)

    files = {"transcript": paths["transcript"].name, "audio": paths["audio"].name}
    if paths["analysis"].exists():
        files["analysis"] = paths["analysis"].name

    # 6. Existing opt-in webhooks (none configured by default)
    try:
        from listener.webhooks import fire_webhooks
        fire_webhooks(event="session_complete", session_id=session_id, meta=dict(meta), files=files)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Webhook firing failed for %s: %s", session_id, exc)

    return {
        "files": files,
        "language": result.language,
        "duration": result.duration,
        "segments": len(result.segments),
        "diarized": diarized,
    }


# ---------------------------------------------------------------------------
# Analyze (Claude, explicit)
# ---------------------------------------------------------------------------

def run_analyze_job(job: dict, ctx: JobContext, output_dir: Path) -> dict:
    """Run a recipe analysis with Claude and save <id>_analysis.md."""
    from listener.analyzer import analyze_transcript_sync

    session_id = job["session_id"]
    opts = job.get("options") or {}
    recipe_id = opts.get("recipe_id") or None
    paths = session_paths(output_dir, session_id)
    if not paths["transcript"].exists():
        raise PipelineError("Transcript not found. Transcribe the meeting first.")

    ctx.set_stage("analyzing")
    transcript_text = paths["transcript"].read_text(encoding="utf-8")
    analysis = analyze_transcript_sync(transcript_text, recipe_id=recipe_id)

    meta = read_meta(output_dir, session_id)
    analysis_md = (
        f"# Meeting Analysis -- {date_display(session_id)}\n\n"
        f"**Duration:** {fmt_duration(meta.get('duration', 0))}  \n"
        f"**Language:** {meta.get('language', '')}\n\n---\n\n"
        f"{analysis}\n"
    )
    ctx.set_stage("saving")
    atomic_write_text(paths["analysis"], analysis_md)

    fields = {"recipe_id": recipe_id, "analyzed_at": datetime.now().isoformat(timespec="seconds")}
    with META_LOCK:  # topics are merged into the analytics dict written by the transcribe job
        meta = read_meta(output_dir, session_id)
        analytics_data = meta.get("analytics") or {}
        if analytics_data:
            try:
                from listener.analytics import extract_topics_from_analysis
                topics = extract_topics_from_analysis(analysis)
                if topics:
                    analytics_data["topics"] = topics
                    fields["analytics"] = analytics_data
            except Exception as exc:  # noqa: BLE001
                logger.warning("Topic extraction failed for %s: %s", session_id, exc)
        update_meta(output_dir, session_id, **fields)

    try:
        index_session(output_dir, session_id, transcript_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Search indexing failed for %s: %s", session_id, exc)

    return {"files": {"analysis": paths["analysis"].name}, "recipe_id": recipe_id}


# ---------------------------------------------------------------------------
# Memory (Claude, explicit)
# ---------------------------------------------------------------------------

def run_memory_job(job: dict, ctx: JobContext, output_dir: Path) -> dict:
    """Generate or update the meeting memory for one transcribed meeting."""
    from listener.memory import generate_memory

    session_id = job["session_id"]
    paths = session_paths(output_dir, session_id)
    if not paths["transcript"].exists():
        raise PipelineError("Transcript not found. Transcribe the meeting first.")

    ctx.set_stage("generating")
    transcript_text = paths["transcript"].read_text(encoding="utf-8")
    meta = read_meta(output_dir, session_id)
    record = generate_memory(
        session_id, transcript_text,
        title=meta.get("title") or default_title(session_id),
        language=meta.get("language", "") or "",
        transcripts_dir=output_dir,
    )
    tasks = record.get("tasks", []) if record else []
    return {
        "files": {"memory": paths["memory_md"].name},
        "generated_at": (record or {}).get("generated_at"),
        "tasks": len(tasks),
        "open_tasks": sum(1 for t in tasks if t.get("status") == "open"),
        "decisions": len((record or {}).get("decisions", [])),
        "open_questions": len((record or {}).get("open_questions", [])),
    }
