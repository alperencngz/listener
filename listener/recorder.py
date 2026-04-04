"""Audio recording module.

Records from a specified input device (microphone, aggregate device,
or virtual audio device like BlackHole) and saves to WAV.
"""

import sys
import time
from pathlib import Path

import sounddevice as sd
import soundfile as sf


def list_input_devices() -> list[dict]:
    """List available audio input devices."""
    devices = sd.query_devices()
    result = []
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            result.append({
                "id": i,
                "name": dev["name"],
                "channels": dev["max_input_channels"],
                "sample_rate": dev["default_samplerate"],
            })
    return result


class Recorder:
    """Records audio from an input device to a WAV file.

    Usage:
        recorder = Recorder(device=1)
        recorder.start("output.wav")
        # ... user presses Enter ...
        recorder.stop()
    """

    def __init__(self, device: int | None = None, channels: int = 1, on_audio=None):
        self.device = device
        self.channels = channels
        self.on_audio = on_audio  # optional callback: fn(numpy_array, sample_rate)
        self._stream: sd.InputStream | None = None
        self._file: sf.SoundFile | None = None
        self._recording = False
        self._start_time: float = 0
        self._output_path: str = ""
        self._sample_rate: int = 0

    def start(self, output_path: str) -> None:
        """Start recording to the given WAV file."""
        self._output_path = output_path
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Use device's native sample rate
        dev_info = sd.query_devices(self.device if self.device is not None else sd.default.device[0])
        sample_rate = int(dev_info["default_samplerate"])

        self._file = sf.SoundFile(
            output_path,
            mode="w",
            samplerate=sample_rate,
            channels=self.channels,
            format="WAV",
            subtype="PCM_16",
        )
        self._recording = True
        self._start_time = time.time()

        self._sample_rate = sample_rate
        self._stream = sd.InputStream(
            device=self.device,
            samplerate=sample_rate,
            channels=self.channels,
            callback=self._audio_callback,
            blocksize=4096,
        )
        self._stream.start()

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"  Audio warning: {status}", file=sys.stderr)
        if self._recording and self._file is not None:
            self._file.write(indata.copy())
        # Feed audio to streaming transcriber if hook is set
        if self._recording and self.on_audio is not None:
            try:
                self.on_audio(indata.copy(), self._sample_rate)
            except Exception:
                pass  # never let the hook crash the audio callback

    def stop(self) -> str:
        """Stop recording and return the output file path."""
        self._recording = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._file is not None:
            self._file.close()
            self._file = None
        return self._output_path

    @property
    def elapsed(self) -> float:
        if self._start_time == 0:
            return 0
        return time.time() - self._start_time

    @property
    def is_recording(self) -> bool:
        return self._recording
