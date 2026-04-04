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
def record_cmd(device, language, model_size, no_analyze, output_dir):
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

    _run_pipeline(audio_path, language, model_size, no_analyze, output_dir, timestamp)


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
def transcribe_cmd(audio_file, language, model_size, no_analyze, output_dir):
    """Transcribe an existing audio file."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    _run_pipeline(audio_file, language, model_size, no_analyze, output_dir, timestamp)


# -----------------------------------------------------------------------
# listener analyze
# -----------------------------------------------------------------------

@cli.command("analyze")
@click.argument("transcript_file", type=click.Path(exists=True))
@click.option("--output", "-o", default=None, help="Output file path")
@click.option("--model", default="claude-sonnet-4-5", help="Claude model")
def analyze_cmd(transcript_file, output, model):
    """Analyze an existing transcript file with Claude."""
    from listener.analyzer import analyze_transcript_sync

    text = Path(transcript_file).read_text()
    click.echo("Analyzing transcript with Claude...")

    try:
        analysis = analyze_transcript_sync(text, model=model)
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

def _run_pipeline(audio_path, language, model_size, no_analyze, output_dir, timestamp):
    """Transcribe audio and optionally analyze with Claude."""
    from listener.transcriber import transcribe

    click.echo("--- Transcription ---\n")
    result = transcribe(audio_path, model_size=model_size, language=language)

    if not result.segments:
        click.echo("No speech detected in the audio.")
        return

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
    click.echo("Analyzing with Claude...")

    from listener.analyzer import analyze_transcript_sync

    try:
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
