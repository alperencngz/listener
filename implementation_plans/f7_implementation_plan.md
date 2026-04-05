# Implementation Plan: F7 — Noise Preprocessing

## Status: ALREADY IMPLEMENTED

F7 was implemented as part of a previous automation pass. All tasks below are complete and verified. This plan now serves as documentation and a verification reference.

---

## Overview

Add an audio noise-reduction preprocessing step between recording and transcription. Uses the `noisereduce` library with non-stationary mode to clean background noise from meeting recordings before they're passed to Whisper. Includes a `--no-denoise` CLI flag and an "Enable noise reduction" checkbox in the web UI (default: enabled).

---

## Pre-Implementation Checklist

Before writing any code, the implementor **must read these files fresh** (they have been modified by features F1–F5, F8–F10):

| File | Why |
|---|---|
| `/Users/alperencngzz/Desktop/listener/listener/web/app.py` | Shared file modified by F1, F2, F3, F4, F8, F9, F10. You'll modify `_process_recording()` and `api_start()`. |
| `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html` | Shared file. You'll add a checkbox in the settings card and wire it into `doStart()`. |
| `/Users/alperencngzz/Desktop/listener/listener/cli.py` | Shared file. You'll add `--no-denoise` flag to `record` and `transcribe` commands. |
| `/Users/alperencngzz/Desktop/listener/pyproject.toml` | Check current dependencies before adding `noisereduce`. |
| `/Users/alperencngzz/Desktop/listener/listener/transcriber.py` | Understand the `transcribe()` function signature — you won't modify it, but you need to know what it accepts. |
| `/Users/alperencngzz/Desktop/listener/listener/recorder.py` | Understand the recording flow — preprocessor runs after `recorder.stop()`. |

**Verify before starting:**
- `noisereduce` is already in `pyproject.toml` — **CONFIRMED PRESENT** (`noisereduce>=3.0.0`)
- `listener/preprocessor.py` already exists — **CONFIRMED PRESENT**

---

## Dependencies

### Already in pyproject.toml

`"noisereduce>=3.0.0"` is already in the `dependencies` list (last entry).

### Install command

```bash
cd /Users/alperencngzz/Desktop/listener && pip install -e .
```

### External requirements

None. `noisereduce` depends on `numpy` and `scipy`. `numpy` is already a dependency; `scipy` is installed automatically as a transitive dependency.

---

## Implementation Tasks

### Task 1: Add `noisereduce` dependency to pyproject.toml — DONE

- **File:** `/Users/alperencngzz/Desktop/listener/pyproject.toml`
- **Action:** Already modified
- **Current state:** `"noisereduce>=3.0.0"` is the last entry in the dependencies list.
- **Verification:**
```bash
python -c "import noisereduce; print('noisereduce version:', noisereduce.__version__)"
```

---

### Task 2: Create `listener/preprocessor.py` — DONE

- **File:** `/Users/alperencngzz/Desktop/listener/listener/preprocessor.py`
- **Action:** Already created (57 lines)
- **Current implementation:**

```python
"""Audio preprocessing — noise reduction before transcription.

Uses noisereduce with non-stationary mode for meeting audio.
Non-stationary adapts the noise floor over time, which handles
varying background noise (HVAC cycling, cafe chatter, etc.).
"""

import logging
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


def preprocess_audio(input_path: str, output_path: str | None = None) -> str:
    """Apply noise reduction to an audio file.

    Args:
        input_path: Path to the input WAV file.
        output_path: Path for the cleaned output file.
            If None, defaults to input_path with '_cleaned' suffix.

    Returns:
        Path to the cleaned audio file.
    """
    import noisereduce as nr

    if output_path is None:
        p = Path(input_path)
        output_path = str(p.with_stem(p.stem + "_cleaned"))

    logger.info("Preprocessing audio: %s -> %s", input_path, output_path)

    data, sr = sf.read(input_path)

    # Convert stereo to mono if needed
    if data.ndim > 1:
        data = np.mean(data, axis=1)

    # Non-stationary noise reduction (adapts over time — good for meetings)
    # prop_decrease=0.75: moderate reduction. 1.0 can distort speech harmonics
    # and actually hurt Whisper accuracy.
    cleaned = nr.reduce_noise(
        y=data,
        sr=sr,
        stationary=False,
        prop_decrease=0.75,
        time_constant_s=2.0,
        freq_mask_smooth_hz=500,
    )

    sf.write(output_path, cleaned, sr)
    logger.info("Noise reduction complete: %s", output_path)
    return output_path
```

**Key design decisions (from IMPROVEMENTS.md):**
- `prop_decrease=0.75` not `1.0` — full removal distorts speech harmonics and hurts Whisper accuracy.
- `stationary=False` — non-stationary mode adapts the noise floor over time, better for meetings where background noise varies.
- `time_constant_s=2.0` — smoothing window for noise estimation.
- `freq_mask_smooth_hz=500` — frequency-domain smoothing to avoid musical noise artifacts.

- **Verification:**
```bash
python -c "from listener.preprocessor import preprocess_audio; print('OK')"
```

---

### Task 3: Integrate preprocessing into `_process_recording()` in app.py — DONE

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/app.py`
- **Action:** Already modified
- **Current state:** All integration points are in place:

#### 3a: State and API — Already done

- `_INTERNAL_KEYS` includes `"_denoise"` (line 41)
- `api_start()` reads `denoise = data.get("denoise", True)` (line 90) and stores it in `_state` as `"_denoise": denoise` (line 139)
- `api_stop()` extracts `denoise = _state.get("_denoise", True)` (line 159) and passes it to the thread (line 176-180)

#### 3b: Processing pipeline — Already done

- `_process_recording()` signature includes `denoise=True` parameter (line 447)
- Denoising step runs between recording and transcription (lines 452-463):
  - Sets state to `"denoising"`
  - Calls `preprocess_audio()` to create `_cleaned.wav`
  - Falls back to original audio on error
  - Only `transcribe()` uses the cleaned path; diarization and playback use the original

- **Verification:**
```bash
python -c "from listener.web.app import app; print('app.py imports OK')"
```

---

### Task 4: Add `--no-denoise` flag to CLI commands — DONE

- **File:** `/Users/alperencngzz/Desktop/listener/listener/cli.py`
- **Action:** Already modified

#### 4a: `record` command — Already done
- `--no-denoise` flag added (line 138)
- Passed to `_run_pipeline()` (line 214)

#### 4b: `transcribe` command — Already done
- `--no-denoise` flag added (line 237)
- Passed to `_run_pipeline()` (line 243)

#### 4c: `_run_pipeline()` — Already done
- Signature includes `no_denoise=False` parameter (line 320)
- Preprocessing step runs before transcription (lines 325-336):
  - Prints "--- Noise Reduction ---" header
  - Calls `preprocess_audio()` to create `_cleaned.wav`
  - Falls back to original audio on error
  - Passes `transcribe_path` (cleaned or original) to `transcribe()`

- **Verification:**
```bash
cd /Users/alperencngzz/Desktop/listener && python -m listener record --help | grep denoise
python -m listener transcribe --help | grep denoise
```

Expected output: `--no-denoise  Skip noise reduction preprocessing`

---

### Task 5: Add "Enable noise reduction" checkbox to web UI — DONE

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** Already modified

#### 5a: Checkbox HTML — Already done
- `<input type="checkbox" id="denoise" checked>` with label (lines 524-525)
- Placed before `skip-analysis` checkbox in the settings card

#### 5b: JS wiring in `doStart()` — Already done
- `denoise:Q('#denoise').checked` included in request body (line 698)

#### 5c: Disable during recording — Already done
- `Q('#denoise').disabled=lock;` (line 726)

#### 5d: Status text for denoising step — Already done
- `denoising:'Reducing noise...'` in the step-to-text mapping (line 791)

---

## Summary of All File Changes

| File | Action | Status |
|---|---|---|
| `/Users/alperencngzz/Desktop/listener/pyproject.toml` | Modify | DONE — `noisereduce>=3.0.0` added |
| `/Users/alperencngzz/Desktop/listener/listener/preprocessor.py` | **Create** | DONE — 57 lines, `preprocess_audio()` function |
| `/Users/alperencngzz/Desktop/listener/listener/web/app.py` | Modify | DONE — `_denoise` in internal keys, passed through start/stop/process, denoising step before transcription |
| `/Users/alperencngzz/Desktop/listener/listener/cli.py` | Modify | DONE — `--no-denoise` flag on `record` and `transcribe`, preprocessing step in `_run_pipeline()` |
| `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html` | Modify | DONE — checkbox, JS wiring, disable logic, status text |

---

## Final Verification

Run these commands in order to verify the complete implementation:

### 1. Dependency check
```bash
cd /Users/alperencngzz/Desktop/listener
python -c "import noisereduce; print('noisereduce OK:', noisereduce.__version__)"
```

### 2. Module import check
```bash
python -c "from listener.preprocessor import preprocess_audio; print('preprocessor OK')"
```

### 3. Web app import check
```bash
python -c "from listener.web.app import app; print('web app OK')"
```

### 4. CLI flag check
```bash
python -m listener record --help | grep -A1 denoise
python -m listener transcribe --help | grep -A1 denoise
```

Expected: `--no-denoise  Skip noise reduction preprocessing`

### 5. Integration test (if a test WAV file is available)
```bash
python -c "
from listener.preprocessor import preprocess_audio
import tempfile, numpy as np, soundfile as sf
# Create a test WAV with some noise
sr = 16000
duration = 2
t = np.linspace(0, duration, sr * duration)
# Speech-like signal + noise
signal = 0.5 * np.sin(2 * np.pi * 440 * t)
noise = 0.1 * np.random.randn(len(t))
noisy = signal + noise
with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
    sf.write(f.name, noisy, sr)
    cleaned = preprocess_audio(f.name, f.name.replace('.wav', '_cleaned.wav'))
    print(f'Input: {f.name}')
    print(f'Output: {cleaned}')
    # Verify output exists and has same duration
    data, sr2 = sf.read(cleaned)
    print(f'Duration: {len(data)/sr2:.1f}s, Sample rate: {sr2}')
    print('Integration test PASSED')
"
```

---

## Notes for Implementor

**F7 is fully implemented. No code changes are needed.**

Key design notes for reference:

1. **The cleaned audio file (`_cleaned.wav`) is only used for transcription.** The original WAV is kept for playback, diarization, and download. `files["audio"]` points to the original.

2. **Error handling is in place.** If `noisereduce` fails for any reason (memory, corrupt audio, etc.), the pipeline falls back to using the original audio. Preprocessing failure never blocks transcription.

3. **The `_cleaned.wav` files accumulate in the transcripts/ directory.** This is acceptable for now — they can be cleaned up in a future maintenance task.

4. **Default is ON.** Both the web checkbox (`checked` attribute), the web API (`denoise` defaults to `True`), and the CLI (no `--no-denoise` flag means denoise=True) default to noise reduction enabled.
