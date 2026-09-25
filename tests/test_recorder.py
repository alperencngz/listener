"""Microphone device handling: refresh, name-based resolution, plain errors,
and no empty WAV when a start fails.

sounddevice is replaced by an in-memory fake; no real audio device is opened.
"""

import types
from pathlib import Path

import pytest

import listener.recorder as recorder


class FakeStream:
    instances: list["FakeStream"] = []
    fail_open = False
    fail_start = False

    def __init__(self, **kw):
        if FakeStream.fail_open:
            raise recorder.sd.PortAudioError(
                "Error opening InputStream: Internal PortAudio error [PaErrorCode -9986]")
        self.kw = kw
        self.started = self.closed = False
        FakeStream.instances.append(self)

    def start(self):
        if FakeStream.fail_start:
            raise RuntimeError("boom at start")
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


def make_fake_sd(devices, default_idx):
    """A stand-in for the sounddevice module with a mutable device table."""
    class PortAudioError(Exception):
        pass

    fake = types.SimpleNamespace(PortAudioError=PortAudioError, InputStream=FakeStream)
    fake.devices = list(devices)
    fake.refreshes = 0
    fake.pending = None            # device table to swap in on the next refresh
    fake.default = types.SimpleNamespace(device=[default_idx, -1])

    def query_devices(index=None, kind=None):
        if index is None:
            return list(fake.devices)
        if not 0 <= index < len(fake.devices):
            raise PortAudioError(f"Error querying device {index}")
        return fake.devices[index]

    def _terminate():
        pass

    def _initialize():
        fake.refreshes += 1
        if fake.pending is not None:
            fake.devices, fake.pending = list(fake.pending), None

    fake.query_devices = query_devices
    fake._terminate = _terminate
    fake._initialize = _initialize
    return fake


def dev(name, inputs=1, rate=48000.0):
    return {"name": name, "max_input_channels": inputs, "max_output_channels": 0, "default_samplerate": rate}


IPHONE = dev("Alperen's iPhone Microphone")
MAC = dev("MacBook Air Microphone")
SPEAKERS = dev("MacBook Air Speakers", inputs=0)


@pytest.fixture
def sd(monkeypatch):
    fake = make_fake_sd([IPHONE, MAC, SPEAKERS], default_idx=1)
    monkeypatch.setattr(recorder, "sd", fake)
    monkeypatch.setattr(recorder, "_OPEN_STREAMS", 0)
    FakeStream.instances.clear()
    FakeStream.fail_open = FakeStream.fail_start = False
    return fake


def test_list_marks_default_and_skips_outputs(sd):
    devs = recorder.list_input_devices()
    assert [d["name"] for d in devs] == [IPHONE["name"], MAC["name"]]
    assert [d["default"] for d in devs] == [False, True]
    assert sd.refreshes == 1


def test_resolve_prefers_name_over_stale_index(sd):
    # The iPhone vanished: the mac mic is now index 0. A remembered (0, "iPhone")
    # must not silently pick whatever sits at index 0 now.
    sd.pending = [MAC, SPEAKERS]
    sd.default.device = [0, -1]
    idx, name, note = recorder.resolve_device(0, IPHONE["name"])
    assert (idx, name) == (0, MAC["name"])
    assert "iPhone" in note and "MacBook Air Microphone" in note

    sd.pending = [IPHONE, MAC, SPEAKERS]
    sd.default.device = [1, -1]
    assert recorder.resolve_device(0, MAC["name"]) == (1, MAC["name"], None)   # name wins
    assert recorder.resolve_device(None, None) == (1, MAC["name"], None)       # system default
    assert recorder.resolve_device(0, None) == (0, IPHONE["name"], None)       # index only
    with pytest.raises(recorder.NoInputDeviceError):
        sd.pending = [SPEAKERS]
        recorder.resolve_device(None, None)


def test_refresh_is_skipped_while_a_stream_is_open(sd, tmp_path):
    rec = recorder.Recorder(device=1)
    rec.start(str(tmp_path / "a.wav"))
    before = sd.refreshes
    assert recorder.refresh_devices() is False
    assert recorder.list_input_devices()[1]["name"] == MAC["name"] and sd.refreshes == before
    rec.stop()
    assert recorder.refresh_devices() is True
    assert recorder._OPEN_STREAMS == 0


def test_failed_open_leaves_no_wav_and_no_open_stream(sd, tmp_path):
    FakeStream.fail_open = True
    rec = recorder.Recorder(device=0)
    with pytest.raises(sd.PortAudioError):
        rec.start(str(tmp_path / "x.wav"))
    assert not (tmp_path / "x.wav").exists()
    assert recorder._OPEN_STREAMS == 0

    FakeStream.fail_open = False
    FakeStream.fail_start = True
    with pytest.raises(RuntimeError):
        rec.start(str(tmp_path / "y.wav"))
    assert not (tmp_path / "y.wav").exists()
    assert FakeStream.instances[-1].closed and recorder._OPEN_STREAMS == 0


def test_successful_start_writes_file_and_uses_device_rate(sd, tmp_path):
    rec = recorder.Recorder(device=0)
    rec.start(str(tmp_path / "ok.wav"))
    assert rec.device_name == IPHONE["name"]
    assert FakeStream.instances[-1].kw["samplerate"] == 48000
    assert (tmp_path / "ok.wav").exists()
    assert rec.stop() == str(tmp_path / "ok.wav")


def test_describe_audio_error_is_plain_language(sd):
    err = sd.PortAudioError("Error opening InputStream: Internal PortAudio error [PaErrorCode -9986]")
    msg = recorder.describe_audio_error(err, "Alperen's iPhone Microphone")
    assert "iPhone Microphone" in msg and "Privacy & Security" in msg and "-9986" not in msg
    assert "not available" in recorder.describe_audio_error(sd.PortAudioError("Error querying device 7"), "X")
    assert recorder.describe_audio_error(RuntimeError("weird")) == "Could not start recording: weird"
    assert recorder.describe_audio_error(recorder.NoInputDeviceError("No microphone found.")) == "No microphone found."


# ---------------------------------------------------------------------------
# Web app: a vanished microphone never leaves an empty meeting behind
# ---------------------------------------------------------------------------

@pytest.fixture
def client(isolated_env, monkeypatch, tmp_path):
    from listener import settings
    import listener.web.app as webapp
    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "config.yaml")
    webapp._reset_for_tests()
    return webapp.app.test_client()


def test_api_start_with_vanished_device(client, sd, isolated_env):
    # The remembered iPhone is gone; the stream open fails outright.
    sd.pending = [MAC, SPEAKERS]
    sd.default.device = [0, -1]
    FakeStream.fail_open = True
    r = client.post("/api/start", json={"device": 0, "device_name": IPHONE["name"]})
    assert r.status_code == 500
    err = r.get_json()["error"]
    assert "MacBook Air Microphone" in err and "-9986" not in err
    files = list(Path(isolated_env["transcripts_dir"]).iterdir())
    assert files == [], files
    assert client.get("/api/status").get_json()["recording"]["active"] is False


def test_api_start_falls_back_to_default_with_note(client, sd, isolated_env):
    sd.pending = [MAC, SPEAKERS]
    sd.default.device = [0, -1]
    r = client.post("/api/start", json={"device": 0, "device_name": IPHONE["name"]})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["device_name"] == MAC["name"] and "iPhone" in body["device_note"]
    status = client.get("/api/status").get_json()["recording"]
    assert status["device_name"] == MAC["name"] and status["device_note"] == body["device_note"]
    assert client.post("/api/stop").status_code == 200
    audio = Path(isolated_env["transcripts_dir"]) / f"{body['session_id']}.wav"
    assert audio.exists()


def test_devices_endpoint_and_remembered_device(client, sd):
    devs = client.get("/api/devices").get_json()
    assert [d["default"] for d in devs] == [False, True]
    r = client.post("/api/settings", json={"input_device": MAC["name"]})
    assert r.get_json()["input_device"] == MAC["name"]
    # A start without an explicit device uses the remembered one.
    r = client.post("/api/start", json={})
    assert r.status_code == 200 and r.get_json()["device_name"] == MAC["name"]
    client.post("/api/stop")
    assert client.post("/api/settings", json={"input_device": None}).get_json()["input_device"] is None
    assert client.post("/api/settings", json={"input_device": 5}).status_code == 400
