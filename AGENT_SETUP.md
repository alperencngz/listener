# Setting up Listener as a Mac app — for an AI coding agent

**Audience:** an AI coding agent (Claude Code or similar) with shell access, told to install
Listener on this Mac for its user. Follow this top to bottom and run every command yourself.
There are two points where a human has to click something; they are marked **[USER]**.

Requires **macOS** (Apple Silicon or Intel, macOS 11.3 or newer). Everything runs on this
machine: recording, noise reduction, Whisper transcription, search and the meeting memory
files. Only the optional Claude actions (analysis, memory, Ask) call out, through the user's
own Claude Code login or their own API key.

## 1. Prerequisites

`uv` manages Python and the virtualenv:

```bash
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh
```

`ffmpeg` is only needed to import non-WAV audio (mp3, m4a, …). Install it if Homebrew is
present; otherwise skip it and mention to the user that imports must be WAV files:

```bash
command -v ffmpeg || (command -v brew && brew install ffmpeg) || echo "ffmpeg not installed (optional)"
```

## 2. Clone and install

```bash
git clone https://github.com/alperencngz/listener.git
cd listener
uv sync --extra desktop
```

Verify: `.venv/bin/listener --help` prints the CLI help, and
`.venv/bin/python -c "import webview"` prints nothing.

## 3. Build and install the Mac app

```bash
./mac/build_app.sh
rm -rf /Applications/Listener.app
cp -R dist/Listener.app /Applications/
```

This is a py2app **alias-mode** bundle: it runs this repo's `listener/` source and its
`.venv` in place instead of freezing a copy. So the repo folder must stay where it is, but
source updates (`git pull`) take effect on the next launch with **no rebuild**. Rebuild only
if `setup_app.py`, the icon or the Info.plist keys change. A rebuild re-signs the bundle, so
macOS may ask for the Microphone permission again afterwards.

## 4. Launch it and check the log

```bash
open /Applications/Listener.app
sleep 8
tail -5 ~/.listener/listener.log
```

Expected: a window titled **Listener** opens, and the log ends with lines like
`Listener desktop starting; meetings in /Users/<name>/Documents/Listener/transcripts` and
`Running on http://127.0.0.1:8642`. If the window does not appear, read the whole last launch
block in the log; a traceback there is the reason.

## 5. [USER] Microphone

macOS shows its own permission dialog the first time the user presses **Record**. Tell the
user:

> The first time you press Record, macOS will ask for Microphone access for Listener.
> Click **Allow**. If you ever denied it: System Settings → Privacy & Security → Microphone →
> switch Listener on.

## 6. [USER] Claude access (optional, for analysis, memory and Ask)

Recording, transcription and search work without this. Check whether Claude Code is
installed:

```bash
command -v claude && echo "claude cli present" || echo "no claude cli"
```

- If present, the app's **Settings → Claude access** shows "Claude Code login found on this
  Mac" and nothing else is needed, as long as the user has logged in once (`claude` in a
  terminal, then follow the browser login).
- If absent, ask the user which they prefer, verbatim:

> Listener can use Claude for summaries and meeting memory. Either install Claude Code
> (`curl -fsSL https://claude.ai/install.sh | bash`, then run `claude` once to log in with
> your Claude subscription), or paste an Anthropic API key into Listener's Settings under
> "Claude access" → "Anthropic API key". Which do you want? You can also skip this for now.

The API key is stored in `~/.listener/config.yaml` on this machine only.

## 7. Where meetings are stored

Default: `~/Documents/Listener/transcripts` (recordings, transcripts, analyses, memory files).
The search index, the queue and the memory bank live in `~/.listener/listener.db`.

If the user already has a Listener archive (a folder that contains a `transcripts`
subfolder), point the app at it instead of moving files:

```bash
mkdir -p ~/.listener
grep -q '^data_dir:' ~/.listener/config.yaml 2>/dev/null || echo 'data_dir: /path/to/that/folder' >> ~/.listener/config.yaml
```

(Replace the path. The same setting applies to `listener web` and the CLI.)

## 8. First transcription downloads the model

The first **Run queued** downloads the selected Whisper model once (default in the app:
`large-v3-turbo`, about 1.6 GB; `large-v3` is 3 GB and slower but slightly more accurate).
The model can be changed in **Settings → Whisper model**. Tell the user to expect a pause
the first time.

## 9. Optional: open at login

```bash
cp mac/io.github.alperencngz.listener.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.github.alperencngz.listener.plist
```

## 10. Hand back to the user

> Listener is installed: open it from Spotlight (⌘Space → "Listener"). Press **Record**,
> stop, name the recording, **Add to queue**, then **Run queued**. You can record the next
> meeting while the previous one transcribes. Summaries and meeting memory are explicit
> buttons on each meeting. Exports and downloads open a Save dialog (Downloads by default).

## Troubleshooting

- **Log:** `~/.listener/listener.log`. Each launch starts with `[listener] === launch …`.
- **Config:** `~/.listener/config.yaml` (`data_dir`, `default_model`, `claude_auth`,
  `anthropic_api_key`, `hf_token`, `webhooks`).
- **"Port 8642 is in use":** the app then opens a window onto the Listener server that is
  already running (for example a `listener web` started in a terminal). Quit that one first
  if you want the app to run its own.
- **Closing the window while recording:** the app asks, then stops and saves the recording
  before quitting. A running transcription is interrupted and can be retried from the queue.
- **Microphone prompt never appears / recording fails at once:** System Settings → Privacy &
  Security → Microphone → make sure Listener is listed and on. If it is missing, run
  `tccutil reset Microphone io.github.alperencngz.listener` and press Record again.
- **Speaker names (diarization):** optional; needs a HuggingFace token in
  `~/.listener/config.yaml` (`hf_token`), see the README.

## Uninstalling

```bash
launchctl bootout gui/$(id -u)/io.github.alperencngz.listener 2>/dev/null
rm -f ~/Library/LaunchAgents/io.github.alperencngz.listener.plist
rm -rf /Applications/Listener.app
# Your meetings stay in ~/Documents/Listener (or your data_dir). To remove everything:
# rm -rf ~/.listener ~/Documents/Listener
```
