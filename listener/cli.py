"""CLI for the conference listening tool.

Commands:
    listener record     Record + transcribe + analyze a meeting
    listener transcribe Transcribe an existing audio file
    listener analyze    Analyze an existing transcript with Claude
    listener devices    List available audio input devices
"""

import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

import click


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """Conference listening tool -- record, transcribe, and analyze meetings."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# -----------------------------------------------------------------------
# listener devices
# -----------------------------------------------------------------------

@cli.command()
def devices():
    """List available audio input devices."""
    from listener.recorder import list_input_devices

    devs = list_input_devices()
    if not devs:
        click.echo("No input devices found.")
        return

    click.echo("Available input devices:\n")
    for dev in devs:
        click.echo(f"  [{dev['id']}] {dev['name']}")
        click.echo(f"      Channels: {dev['channels']}, "
                    f"Sample rate: {int(dev['sample_rate'])} Hz")
    click.echo()
    click.echo("Tip: pass --device ID to select a device for recording.")


# -----------------------------------------------------------------------
# listener recipes
# -----------------------------------------------------------------------

@cli.command()
def recipes():
    """List available analysis recipes."""
    from listener.recipes import load_recipes

    all_recipes = load_recipes()
    if not all_recipes:
        click.echo("No recipes found.")
        return

    # Group by category
    categories = {}
    for r in all_recipes:
        if r.category not in categories:
            categories[r.category] = []
        categories[r.category].append(r)

    click.echo("Available analysis recipes:\n")
    for cat in sorted(categories.keys()):
        click.echo(f"  [{cat.upper()}]")
        for r in categories[cat]:
            tag = "built-in" if r.is_builtin else "custom"
            click.echo(f"    {r.id:24s} {r.name} ({tag})")
            if r.description:
                click.echo(f"    {'':24s} {r.description}")
        click.echo()

    click.echo("Use: listener record --recipe <id>")
    click.echo("  or: listener analyze --recipe <id> transcript.md")


# -----------------------------------------------------------------------
# listener search
# -----------------------------------------------------------------------

@cli.command()
@click.argument("query")
@click.option("--limit", "-n", default=10, help="Max results to return")
def search(query, limit):
    """Search across all meeting transcripts."""
    from listener.db import search_meetings, backfill_from_transcripts
    import re

    # Backfill first to ensure index is current
    transcripts_dir = Path("./transcripts")
    backfill_from_transcripts(transcripts_dir)

    results = search_meetings(query, limit=limit)
    if not results:
        click.echo("No results found.")
        return

    for i, r in enumerate(results, 1):
        title = r["title"] or r["session_id"]
        click.echo(f"\n{i}. {title}")
        click.echo(f"   Date: {r['date']}  Duration: {r['duration']:.0f}s  Lang: {r['language']}")
        if r.get("snippet"):
            # Strip HTML tags for terminal display
            snippet = re.sub(r'<[^>]+>', '', r["snippet"])
            click.echo(f"   ...{snippet}...")


# -----------------------------------------------------------------------
# listener export
# -----------------------------------------------------------------------

@cli.command("export")
@click.argument("session_id")
@click.option("--format", "-f", "fmt", required=True,
              type=click.Choice(["docx", "pdf", "srt", "json"], case_sensitive=False),
              help="Export format")
@click.option("--output-dir", "-o", default="./transcripts",
              help="Directory containing session files (default: ./transcripts)")
@click.option("--output-file", default=None,
              help="Output file path (default: <session_id>.<format> in output-dir)")
def export_cmd(session_id, fmt, output_dir, output_file):
    """Export a meeting transcript in various formats.

    SESSION_ID is the session identifier (e.g., 2026-04-04_14-30-00).

    Examples:
        listener export 2026-04-04_14-30-00 -f pdf
        listener export 2026-04-04_14-30-00 -f docx -o ./exports
        listener export 2026-04-04_14-30-00 -f srt --output-file meeting.srt
    """
    import json as _json
    from listener.export import EXPORTERS

    out_dir = Path(output_dir)
    transcript_path = out_dir / f"{session_id}_transcript.md"
    analysis_path = out_dir / f"{session_id}_analysis.md"
    meta_path = out_dir / f"{session_id}_meta.json"

    if not transcript_path.exists():
        click.echo(f"Error: transcript not found at {transcript_path}", err=True)
        click.echo(f"Available sessions in {out_dir}:")
        if out_dir.exists():
            sessions = set()
            for f in out_dir.iterdir():
                if f.stem.endswith("_transcript"):
                    sessions.add(f.stem[:-11])  # strip _transcript
            for s in sorted(sessions):
                click.echo(f"  {s}")
        sys.exit(1)

    transcript_text = transcript_path.read_text()
    analysis_text = analysis_path.read_text() if analysis_path.exists() else None

    # Load metadata
    title = f"Meeting {session_id}"
    duration_seconds = 0.0
    language = ""
    language_confidence = 0.0
    recipe_id = ""
    if meta_path.exists():
        try:
            meta = _json.loads(meta_path.read_text())
            title = meta.get("title", title)
            duration_seconds = meta.get("duration", 0.0)
            language = meta.get("language", "")
            language_confidence = meta.get("language_probability", 0.0)
            recipe_id = meta.get("recipe_id", "") or ""
        except Exception:
            pass

    parts = session_id.split("_")
    date_str = parts[0] + " " + (parts[1] if len(parts) > 1 else "").replace("-", ":")
    duration_str = _fmt_duration(duration_seconds) if duration_seconds else ""

    exporter = EXPORTERS[fmt.lower()]

    kwargs = dict(
        session_id=session_id,
        title=title,
        date_str=date_str,
        duration_str=duration_str,
        language=language,
        transcript_text=transcript_text,
        analysis_text=analysis_text,
    )
    if fmt.lower() == "json":
        kwargs["duration_seconds"] = duration_seconds
        kwargs["language_confidence"] = language_confidence
        kwargs["recipe_id"] = recipe_id

    click.echo(f"Exporting {session_id} as {fmt.upper()}...")

    try:
        buf = exporter(**kwargs)
    except Exception as e:
        click.echo(f"Export failed: {e}", err=True)
        sys.exit(1)

    ext_map = {"docx": ".docx", "pdf": ".pdf", "srt": ".srt", "json": ".json"}
    if output_file:
        dest = Path(output_file)
    else:
        dest = out_dir / f"{session_id}{ext_map[fmt.lower()]}"

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.read())
    click.echo(f"Exported: {dest}")


# -----------------------------------------------------------------------
# listener record
# -----------------------------------------------------------------------

@cli.command("record")
@click.option("--device", "-d", type=int, default=None,
              help="Audio input device ID (run 'listener devices' to list)")
@click.option("--language", "-l", default=None,
              help="Transcription language: en, tr, or omit for auto-detect")
@click.option("--model-size", "-m", default="large-v3",
              help="Whisper model size (default: large-v3)")
@click.option("--no-analyze", is_flag=True,
              help="Skip Claude analysis, only transcribe")
@click.option("--output-dir", "-o", default="./transcripts",
              help="Output directory (default: ./transcripts)")
@click.option("--hf-token", default=None,
              help="HuggingFace token for speaker diarization (or set HF_TOKEN env var)")
@click.option("--no-diarize", is_flag=True,
              help="Skip speaker diarization even if HF token is available")
@click.option("--recipe", "-r", default=None,
              help="Analysis recipe ID (run 'listener recipes' to list)")
@click.option("--no-denoise", is_flag=True,
              help="Skip noise reduction preprocessing")
def record_cmd(device, language, model_size, no_analyze, output_dir, hf_token, no_diarize, recipe, no_denoise):
    """Record a meeting, then transcribe and analyze.

    Starts recording from the selected audio input device.
    Press Enter or Ctrl+C to stop. Audio is then transcribed
    with Whisper and optionally analyzed by Claude.
    """
    import sounddevice as sd
    from listener.recorder import Recorder

    # Resolve device
    if device is None:
        device = sd.default.device[0]
        if device is None or device < 0:
            click.echo("Error: no default input device found.", err=True)
            click.echo("Run 'listener devices' and pass --device ID.")
            sys.exit(1)

    try:
        dev_info = sd.query_devices(device)
    except Exception as e:
        click.echo(f"Error: device [{device}] not found: {e}", err=True)
        click.echo("Run 'listener devices' to list available devices.")
        sys.exit(1)

    if dev_info["max_input_channels"] < 1:
        click.echo(f"Error: [{device}] '{dev_info['name']}' has no input channels.", err=True)
        sys.exit(1)

    click.echo(f"Device: [{device}] {dev_info['name']}")

    # Paths
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = str(out_dir / f"{timestamp}.wav")

    # Record
    recorder = Recorder(device=device)
    try:
        recorder.start(audio_path)
    except Exception as e:
        click.echo(f"Error: could not start recording: {e}", err=True)
        sys.exit(1)

    click.echo(f"Saving to: {audio_path}")
    click.echo("Press Enter to stop recording...\n")

    stop_event = threading.Event()

    def _wait_for_enter():
        try:
            sys.stdin.readline()
        except Exception:
            pass
        stop_event.set()

    threading.Thread(target=_wait_for_enter, daemon=True).start()

    prev_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())

    while not stop_event.is_set():
        elapsed = recorder.elapsed
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        ts = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        click.echo(f"\r  Recording... [{ts}]", nl=False)
        stop_event.wait(0.5)

    signal.signal(signal.SIGINT, prev_sigint)
    recorder.stop()
    click.echo(f"\n\nRecording saved: {audio_path}\n")

    _run_pipeline(audio_path, language, model_size, no_analyze, output_dir, timestamp,
                  hf_token=hf_token, no_diarize=no_diarize, recipe_id=recipe, no_denoise=no_denoise)


# -----------------------------------------------------------------------
# listener transcribe
# -----------------------------------------------------------------------

@cli.command("transcribe")
@click.argument("audio_file", type=click.Path(exists=True))
@click.option("--language", "-l", default=None,
              help="Language: en, tr, or omit for auto")
@click.option("--model-size", "-m", default="large-v3",
              help="Whisper model size")
@click.option("--no-analyze", is_flag=True, help="Skip Claude analysis")
@click.option("--output-dir", "-o", default="./transcripts",
              help="Output directory")
@click.option("--hf-token", default=None,
              help="HuggingFace token for speaker diarization (or set HF_TOKEN env var)")
@click.option("--no-diarize", is_flag=True,
              help="Skip speaker diarization even if HF token is available")
@click.option("--recipe", "-r", default=None,
              help="Analysis recipe ID (run 'listener recipes' to list)")
@click.option("--no-denoise", is_flag=True,
              help="Skip noise reduction preprocessing")
def transcribe_cmd(audio_file, language, model_size, no_analyze, output_dir, hf_token, no_diarize, recipe, no_denoise):
    """Transcribe an existing audio file."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    _run_pipeline(audio_file, language, model_size, no_analyze, output_dir, timestamp,
                  hf_token=hf_token, no_diarize=no_diarize, recipe_id=recipe, no_denoise=no_denoise)


# -----------------------------------------------------------------------
# listener analyze
# -----------------------------------------------------------------------

@cli.command("analyze")
@click.argument("transcript_file", type=click.Path(exists=True))
@click.option("--output", "-o", default=None, help="Output file path")
@click.option("--model", default="claude-sonnet-4-5", help="Claude model")
@click.option("--recipe", "-r", default=None,
              help="Analysis recipe ID (run 'listener recipes' to list)")
def analyze_cmd(transcript_file, output, model, recipe):
    """Analyze an existing transcript file with Claude."""
    from listener.analyzer import analyze_transcript_sync

    text = Path(transcript_file).read_text()
    click.echo("Analyzing transcript with Claude...")

    try:
        analysis = analyze_transcript_sync(text, model=model, recipe_id=recipe)
    except Exception as e:
        click.echo(f"Analysis failed: {e}", err=True)
        sys.exit(1)

    if output is None:
        p = Path(transcript_file)
        output = str(p.parent / p.name.replace("_transcript", "_analysis"))

    Path(output).write_text(analysis)
    click.echo(f"Analysis saved: {output}")


# -----------------------------------------------------------------------
# listener web
# -----------------------------------------------------------------------

@cli.command("web")
@click.option("--port", "-p", type=int, default=8642,
              help="Port to run the web server on (default: 8642)")
def web_cmd(port):
    """Launch the web interface in your browser."""
    import webbrowser
    from listener.web.app import run

    url = f"http://127.0.0.1:{port}"
    click.echo(f"Starting Listener web UI at {url}")
    webbrowser.open(url)
    run(port=port)


# -----------------------------------------------------------------------
# listener automate
# -----------------------------------------------------------------------

@cli.command("automate")
@click.option("--start-from", default=None,
              help="Feature ID to resume from (e.g. F4). Skips earlier features.")
def automate_cmd(start_from):
    """Run the overnight autonomous implementation pipeline.

    Iterates through all features in IMPROVEMENTS.md, spinning up
    a planner agent then an implementor agent for each one.
    Progress is logged to progress.md and automation_logs/.
    """
    from listener.automation.orchestrate import run_pipeline
    import asyncio

    asyncio.run(run_pipeline(start_from=start_from))


# -----------------------------------------------------------------------
# Shared pipeline
# -----------------------------------------------------------------------

def _run_pipeline(audio_path, language, model_size, no_analyze, output_dir, timestamp,
                  hf_token=None, no_diarize=False, recipe_id=None, no_denoise=False):
    """Transcribe audio, optionally diarize, and optionally analyze with Claude."""
    from listener.transcriber import transcribe

    # F7: Noise preprocessing
    transcribe_path = audio_path
    if not no_denoise:
        click.echo("--- Noise Reduction ---\n")
        try:
            from listener.preprocessor import preprocess_audio
            cleaned_path = audio_path.replace(".wav", "_cleaned.wav")
            transcribe_path = preprocess_audio(audio_path, cleaned_path)
            click.echo(f"Cleaned audio saved: {transcribe_path}\n")
        except Exception as e:
            click.echo(f"Noise reduction failed (continuing with original): {e}", err=True)
            transcribe_path = audio_path

    click.echo("--- Transcription ---\n")
    result = transcribe(transcribe_path, model_size=model_size, language=language)

    if not result.segments:
        click.echo("No speech detected in the audio.")
        return

    # Speaker diarization
    if not no_diarize:
        from listener.diarizer import get_hf_token
        token = get_hf_token(cli_token=hf_token)
        if token:
            click.echo("\n--- Speaker Diarization ---\n")
            try:
                from listener.diarizer import diarize, align_speakers
                diarization = diarize(audio_path, hf_token=token)
                result.segments = align_speakers(diarization, result.segments)
                click.echo(f"Speakers assigned to {len(result.segments)} segments.\n")
            except Exception as e:
                click.echo(f"Diarization failed (continuing without speakers): {e}", err=True)
        else:
            click.echo("\nNo HuggingFace token found — skipping speaker diarization.")
            click.echo("Set HF_TOKEN env var or use --hf-token to enable.\n")

    transcript_text = result.to_timestamped_text()
    duration_str = _fmt_duration(result.duration)
    dt = datetime.strptime(timestamp, "%Y-%m-%d_%H-%M-%S")
    date_display = dt.strftime("%Y-%m-%d %H:%M")

    # Save transcript
    transcript_md = (
        f"# Meeting Transcript -- {date_display}\n\n"
        f"**Duration:** {duration_str}  \n"
        f"**Language:** {result.language} "
        f"({result.language_probability:.0%} confidence)\n\n"
        f"---\n\n"
        f"{transcript_text}\n"
    )
    transcript_path = Path(output_dir) / f"{timestamp}_transcript.md"
    transcript_path.write_text(transcript_md)
    click.echo(f"\nTranscript saved: {transcript_path}")

    if no_analyze:
        return

    # Analyze
    click.echo("\n--- Analysis ---\n")
    if recipe_id:
        from listener.recipes import get_recipe
        rec = get_recipe(recipe_id)
        if rec:
            click.echo(f"Using recipe: {rec.name}")
        else:
            click.echo(f"Warning: recipe '{recipe_id}' not found, using default analysis")
            recipe_id = None
    click.echo("Analyzing with Claude...")

    from listener.analyzer import analyze_transcript_sync

    try:
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
        analysis_path = Path(output_dir) / f"{timestamp}_analysis.md"
        analysis_path.write_text(analysis_md)
        click.echo(f"Analysis saved: {analysis_path}")

    except Exception as e:
        click.echo(f"\nAnalysis failed: {e}", err=True)
        click.echo("Transcript was saved successfully. Re-run with:")
        click.echo(f"  listener analyze {transcript_path}")


def _fmt_duration(seconds: float) -> str:
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


if __name__ == "__main__":
    cli()
