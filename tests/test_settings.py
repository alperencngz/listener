"""Settings (config.yaml), the settings API and the Claude API-key access mode.

No network: the Anthropic SDK is replaced by a fake. Config is written to a
temp file, never to ~/.listener.
"""

import asyncio
from pathlib import Path

import pytest

from listener import settings


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    for var in (settings.ENV_DATA_DIR, settings.ENV_DEFAULT_MODEL, "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return path


def test_data_dir_resolution_order(cfg, monkeypatch, tmp_path):
    assert settings.resolve_data_dir() is None
    assert settings.transcripts_dir() == Path("./transcripts")
    assert settings.transcripts_dir(tmp_path / "app") == tmp_path / "app" / "transcripts"
    settings.update_config(data_dir=str(tmp_path / "cfg"))
    assert settings.transcripts_dir(tmp_path / "app") == tmp_path / "cfg" / "transcripts"
    monkeypatch.setenv(settings.ENV_DATA_DIR, str(tmp_path / "env"))
    assert settings.resolve_data_dir() == tmp_path / "env"


def test_default_model_resolution_and_validation(cfg, monkeypatch):
    assert settings.default_model() == "large-v3"
    monkeypatch.setenv(settings.ENV_DEFAULT_MODEL, "large-v3-turbo")
    assert settings.default_model() == "large-v3-turbo"
    settings.update_config(default_model="small")
    assert settings.default_model() == "small"          # config wins over env
    settings.update_config(default_model="not-a-model")
    assert settings.default_model() == "large-v3-turbo"  # invalid config falls through


def test_update_config_keeps_other_keys_and_removes_none(cfg):
    settings.save_config({"hf_token": "hf_x", "webhooks": [{"url": "http://h"}]})
    settings.update_config(default_model="medium")
    settings.update_config(default_model=None)
    data = settings.load_config()
    assert data == {"hf_token": "hf_x", "webhooks": [{"url": "http://h"}]}


def test_claude_status_modes(cfg, monkeypatch):
    monkeypatch.setattr(settings.shutil, "which", lambda name: None)
    st = settings.claude_status()
    assert st["mode"] == "max" and st["ready"] is False and "not installed" in st["note"]
    monkeypatch.setattr(settings.shutil, "which", lambda name: "/usr/local/bin/claude")
    assert settings.claude_status()["ready"] is True

    st = settings.set_claude_access("api")
    assert st["mode"] == "api" and st["ready"] is False and "No API key" in st["note"]
    st = settings.set_claude_access("api", "sk-ant-secret-1234")
    assert st["ready"] is True and st["api_key_set"] is True and st["api_key_hint"] == "…1234"
    assert "secret" not in str(st)
    # switching back keeps the key stored; clearing removes it
    settings.set_claude_access("max")
    assert settings.load_config()["anthropic_api_key"] == "sk-ant-secret-1234"
    settings.set_claude_access("max", clear_key=True)
    assert "anthropic_api_key" not in settings.load_config()
    with pytest.raises(ValueError):
        settings.set_claude_access("bogus")


# ---------------------------------------------------------------------------
# Web API
# ---------------------------------------------------------------------------

@pytest.fixture
def client(isolated_env, cfg, monkeypatch):
    import listener.web.app as webapp
    webapp._reset_for_tests()
    webapp.app.config["TESTING"] = True
    monkeypatch.setattr(settings.shutil, "which", lambda name: None)
    with webapp.app.test_client() as c:
        yield c


def test_settings_api_roundtrip_never_returns_key(client, isolated_env):
    r = client.get("/api/settings")
    assert r.status_code == 200
    d = r.get_json()
    assert d["data_dir"] == str(isolated_env["transcripts_dir"].resolve())
    assert d["default_model"] == "large-v3" and [m["id"] for m in d["models"]][:2] == ["large-v3", "large-v3-turbo"]
    assert d["claude"]["mode"] == "max" and d["claude"]["ready"] is False

    assert client.post("/api/settings", json={"default_model": "nope"}).status_code == 400
    r = client.post("/api/settings", json={"default_model": "large-v3-turbo"})
    assert r.status_code == 200 and r.get_json()["default_model"] == "large-v3-turbo"

    r = client.post("/api/settings/claude", json={"mode": "api", "api_key": "sk-ant-abcdefgh9999"})
    assert r.status_code == 200
    c = r.get_json()["claude"]
    assert c["mode"] == "api" and c["ready"] is True and c["api_key_hint"] == "…9999"
    again = client.get("/api/settings")
    assert "abcdefgh" not in again.get_data(as_text=True)
    assert again.get_json()["claude"]["api_key_hint"] == "…9999"
    assert client.post("/api/settings/claude", json={"mode": "api", "api_key": 5}).status_code == 400
    assert client.post("/api/settings/claude", json={"mode": "weird"}).status_code == 400


# ---------------------------------------------------------------------------
# Runner: API-key mode through a fake Anthropic client
# ---------------------------------------------------------------------------

class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Message:
    def __init__(self, text):
        self.content = [_Block(text)]


def _fake_anthropic(monkeypatch, reply="fake answer", calls=None):
    import anthropic

    class FakeMessages:
        async def create(self, **kw):
            (calls if calls is not None else []).append(kw)
            return _Message(reply)

    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClient)


def test_runner_uses_api_mode_from_settings(cfg, monkeypatch):
    from listener.claude import runner

    calls = []
    _fake_anthropic(monkeypatch, '{"ok": true}', calls)
    settings.set_claude_access("api", "sk-ant-test-key-0001")
    out = asyncio.run(runner.run_claude_session("hello", system_prompt="sys", model="claude-sonnet-4-5"))
    assert out == '{"ok": true}'
    assert calls[0]["system"] == "sys" and calls[0]["messages"] == [{"role": "user", "content": "hello"}]

    validated = asyncio.run(runner.run_with_schema_validation(
        "x", {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}))
    assert validated == {"ok": True}


def test_runner_api_mode_without_key_fails_clearly(cfg, monkeypatch):
    from listener.claude import runner

    _fake_anthropic(monkeypatch)
    settings.set_claude_access("api")
    with pytest.raises(runner.ClaudeTaskError, match="No Anthropic API key"):
        asyncio.run(runner.run_claude_session("hello"))


def test_runner_max_mode_never_touches_anthropic_sdk(cfg, monkeypatch):
    """In login mode the Anthropic SDK is not used; the Claude Code SDK path is taken."""
    from listener.claude import runner

    used = []
    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: used.append(kw))

    async def fake_query(prompt, options):
        from claude_code_sdk.types import ResultMessage
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                            num_turns=1, session_id="s", result="via login")

    monkeypatch.setattr(runner, "query", fake_query)
    settings.set_claude_access("max")
    assert asyncio.run(runner.run_claude_session("hello")) == "via login"
    assert used == []
