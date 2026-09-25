"""Audio recording module.

Records from a specified input device (microphone, aggregate device,
or virtual audio device like BlackHole) and saves to WAV.
"""

import sys
import threading
import time
from pathlib import Path

import sounddevice as sd
import soundfile as sf

# PortAudio reads the device list once, when it is initialised. A microphone
# that appears or disappears afterwards (an iPhone "Continuity" microphone, a
# Bluetooth headset, a USB interface) is not seen until the library is
# re-initialised, and its stale index then fails with the unhelpful
# "Internal PortAudio error [PaErrorCode -9986]". ``refresh_devices`` does that
# re-initialisation, but only while no stream is open: terminating PortAudio
# under a live stream would kill the recording.
_PA_LOCK = threading.RLock()
_OPEN_STREAMS = 0


class NoInputDeviceError(RuntimeError):
    """No microphone is available at all."""


def refresh_devices() -> bool:
    """Re-read the audio device list from the OS.

    Returns True when the list was refreshed, False when a recording is in
    progress (the cached list is kept so the open stream is not disturbed).
    """
    with _PA_LOCK:
        if _OPEN_STREAMS > 0:
            return False
        terminate = getattr(sd, "_terminate", None)
        initialize = getattr(sd, "_initialize", None)
        if terminate is None or initialize is None:
            return False
        try:
            terminate()
            initialize()
        except Exception as exc:  # noqa: BLE001 - keep whatever list we have
            print(f"  Audio warning: could not refresh devices: {exc}", file=sys.stderr)
            return False
        return True


def _default_input_index() -> int | None:
    try:
        idx = sd.default.device[0]
    except Exception:  # noqa: BLE001
        return None
    if idx is None or idx < 0:
        return None
    return int(idx)


def list_input_devices(refresh: bool = True) -> list[dict]:
    """List available audio input devices.

    Each entry has ``id`` (PortAudio index), ``name``, ``channels``,
    ``sample_rate`` and ``default`` (True for the system default input).
    """
    if refresh:
        refresh_devices()
    with _PA_LOCK:
        devices = sd.query_devices()
        default_idx = _default_input_index()
    result = []
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            result.append({
                "id": i,
                "name": dev["name"],
                "channels": dev["max_input_channels"],
                "sample_rate": dev["default_samplerate"],
                "default": i == default_idx,
            })
    return result


def resolve_device(device: int | None = None, name: str | None = None,
                   refresh: bool = True) -> tuple[int | None, str | None, str | None]:
    """Turn a remembered device choice into a device that exists right now.

    Returns ``(index, name, note)``. The name wins over the index (indices
    shift whenever a device comes or goes); a device that is no longer
    present falls back to the system default microphone with an explanatory
    ``note``. Raises :class:`NoInputDeviceError` when there is no microphone.
    """
    devices = list_input_devices(refresh=refresh)
    if not devices:
        raise NoInputDeviceError("No microphone found. Connect an input device and try again.")
    default = next((d for d in devices if d["default"]), devices[0])
    wanted = (name or "").strip()
    if wanted:
        for dev in devices:
            if dev["name"] == wanted:
                return dev["id"], dev["name"], None
    if device is not None and not wanted:
        for dev in devices:
            if dev["id"] == device:
                return dev["id"], dev["name"], None
    if wanted or device is not None:
        gone = wanted or f"device {device}"
        note = (f"\u201c{gone}\u201d is not available any more, so this recording uses "
                f"\u201c{default['name']}\u201d. Pick another input device if you meant a different microphone.")
        return default["id"], default["name"], note
    return default["id"], default["name"], None


def describe_audio_error(exc: BaseException, device_name: str | None = None) -> str:
    """Translate PortAudio's error codes into something a person can act on."""
    text = str(exc)
    which = f"\u201c{device_name}\u201d" if device_name else "the selected microphone"
    lowered = text.lower()
    if isinstance(exc, NoInputDeviceError):
        return text
    if "-9986" in text or "internal portaudio error" in lowered:
        return (f"Could not open {which}. Either it is no longer available (an iPhone or Bluetooth "
                "microphone that disconnected: choose another input device), or macOS has not "
                "granted Listener microphone access (System Settings \u2192 Privacy & Security "
                "\u2192 Microphone).")
    if "-9996" in text or "invalid device" in lowered or "error querying device" in lowered:
        return f"{which[0].upper() + which[1:]} is not available any more. Choose another input device."
    if "-9998" in text or "invalid sample rate" in lowered:
        return f"{which[0].upper() + which[1:]} does not support the requested sample rate. Choose another input device."
    return f"Could not start recording: {text}"


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
        self.device_name: str | None = None
        self.channels = channels
        self.on_audio = on_audio  # optional callback: fn(numpy_array, sample_rate)
        self._stream: sd.InputStream | None = None
        self._file: sf.SoundFile | None = None
        self._recording = False
        self._start_time: float = 0
        self._output_path: str = ""
        self._sample_rate: int = 0

    def start(self, output_path: str) -> None:
        """Start recording to the given WAV file.

        The input stream is opened before the WAV file is created, so a
        microphone that cannot be opened leaves no empty file behind.
        """
        global _OPEN_STREAMS
        self._output_path = output_path
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        with _PA_LOCK:
            # Use device's native sample rate
            dev_index = self.device if self.device is not None else _default_input_index()
            if dev_index is None:
                raise NoInputDeviceError("No microphone found. Connect an input device and try again.")
            dev_info = sd.query_devices(dev_index)
            self.device_name = dev_info["name"]
            sample_rate = int(dev_info["default_samplerate"])
            self._sample_rate = sample_rate

            stream = sd.InputStream(
                device=self.device,
                samplerate=sample_rate,
                channels=self.channels,
                callback=self._audio_callback,
                blocksize=4096,
            )
            _OPEN_STREAMS += 1
            self._stream = stream

        try:
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
            stream.start()
        except Exception:
            self._recording = False
            self._close_stream()
            if self._file is not None:
                self._file.close()
                self._file = None
                Path(output_path).unlink(missing_ok=True)
            raise

    def _close_stream(self) -> None:
        global _OPEN_STREAMS
        stream, self._stream = self._stream, None
        if stream is None:
            return
        with _PA_LOCK:
            try:
                stream.stop()
                stream.close()
            finally:
                _OPEN_STREAMS = max(0, _OPEN_STREAMS - 1)

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
        self._close_stream()
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
