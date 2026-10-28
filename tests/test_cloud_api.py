"""Cloud model by API (Pro): settings file, environment, checks, app section."""
from __future__ import annotations

import os

import httpx
import pytest

from core import cloud_api
from core.cloud_api import CloudConfig, apply_cloud_config, load_cloud_config, save_cloud_config


def _cfg(**kwargs) -> CloudConfig:
    base = dict(
        enabled=True,
        provider="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key="gsk_test",
        model="llama-3.3-70b-versatile",
    )
    base.update(kwargs)
    return CloudConfig(**base)


# ── settings file ─────────────────────────────────────────────────────────

def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "sub" / "cloud.json"
    save_cloud_config(_cfg(), path)
    assert load_cloud_config(path) == _cfg()


def test_load_missing_or_broken_file_gives_defaults(tmp_path):
    assert load_cloud_config(tmp_path / "none.json") == CloudConfig()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_cloud_config(broken) == CloudConfig()


def test_default_path_follows_override(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_CLOUD_API_FILE", str(tmp_path / "x.json"))
    assert cloud_api.cloud_config_path() == tmp_path / "x.json"


# ── environment ───────────────────────────────────────────────────────────

def test_apply_enables_api_backend():
    from core.backend_factory import _detect_backend_type
    from core.config import Settings

    assert apply_cloud_config(_cfg()) is True
    assert os.environ["FLUXION_BACKEND"] == "api"
    assert os.environ["FLUXION_API_KEY"] == "gsk_test"
    assert os.environ["FLUXION_API_BASE_URL"] == "https://api.groq.com/openai/v1"
    assert os.environ["FLUXION_API_MODEL"] == "llama-3.3-70b-versatile"
    assert _detect_backend_type(Settings()) == "api"

    from core.api_backend import APIBackend

    backend = APIBackend.from_settings(Settings())
    assert backend.model == "llama-3.3-70b-versatile"
    assert backend.base_url == "https://api.groq.com/openai/v1"


def test_apply_disabled_or_incomplete_removes_own_variables():
    apply_cloud_config(_cfg())
    assert apply_cloud_config(_cfg(enabled=False)) is False
    for name in ("FLUXION_BACKEND", "FLUXION_API_KEY", "FLUXION_API_BASE_URL", "FLUXION_API_MODEL"):
        assert name not in os.environ
    assert apply_cloud_config(_cfg(api_key="")) is False
    assert "FLUXION_BACKEND" not in os.environ


def test_user_environment_wins(monkeypatch):
    monkeypatch.setenv("FLUXION_API_KEY", "from-env")
    apply_cloud_config(_cfg())
    assert os.environ["FLUXION_API_KEY"] == "from-env"
    assert cloud_api.env_overrides() == ["FLUXION_API_KEY"]
    apply_cloud_config(_cfg(enabled=False))
    assert os.environ["FLUXION_API_KEY"] == "from-env"   # never removed


# ── connection check ──────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_check_connection_ok(monkeypatch):
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, json=json, headers=headers)
        return _Resp(200, {"choices": [{"message": {"content": "p"}}]})

    monkeypatch.setattr(cloud_api.httpx, "post", fake_post)
    ok, message = cloud_api.check_connection(_cfg(base_url="https://api.groq.com/openai/v1/"))
    assert ok and "отвечает" in message
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer gsk_test"
    assert seen["json"]["max_tokens"] == 1


@pytest.mark.parametrize(
    "status, words",
    [(401, "Ключ не принят"), (404, "не найдены"), (402, "средств"), (429, "ограничил"), (500, "500")],
)
def test_check_connection_errors_are_explained(monkeypatch, status, words):
    monkeypatch.setattr(
        cloud_api.httpx, "post",
        lambda *a, **k: _Resp(status, {"error": {"message": "boom"}}),
    )
    ok, message = cloud_api.check_connection(_cfg())
    assert not ok and words in message and "boom" in message


def test_check_connection_network_error(monkeypatch):
    def fail(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(cloud_api.httpx, "post", fail)
    ok, message = cloud_api.check_connection(_cfg())
    assert not ok and "Нет связи" in message


def test_check_connection_needs_fields():
    assert cloud_api.check_connection(_cfg(api_key=""))[1] == "Укажите ключ API."
    assert cloud_api.check_connection(_cfg(model=""))[1] == "Укажите модель."


def test_list_models(monkeypatch):
    monkeypatch.setattr(
        cloud_api.httpx, "get",
        lambda *a, **k: _Resp(200, {"data": [{"id": "b-model"}, {"id": "A-model"}, {"id": "b-model"}]}),
    )
    assert cloud_api.list_models(_cfg()) == ["A-model", "b-model"]
    monkeypatch.setattr(cloud_api.httpx, "get", lambda *a, **k: _Resp(401, {"error": "bad key"}))
    with pytest.raises(RuntimeError, match="401"):
        cloud_api.list_models(_cfg())


# ── desktop app section ───────────────────────────────────────────────────

@pytest.fixture()
def make_window(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])

    def factory(**kwargs):
        from desktop_browser.app import FluxionWindow

        ini = tmp_path / f"settings_{len(list(tmp_path.iterdir()))}.ini"
        return FluxionWindow(qsettings=QSettings(str(ini), QSettings.IniFormat), **kwargs)

    return factory


def test_cloud_section_requires_pro(make_window, monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    window = make_window()
    window._refresh_cloud_card()
    assert window.cloud_save_button.text() == "Доступно в Pro"
    window.cloud_key.setText("gsk_test")
    window.cloud_model.setEditText("m")
    window.cloud_enabled.setChecked(True)
    window._cloud_save()
    assert "Pro" in window.cloud_status.text()
    assert not cloud_api.cloud_config_path().exists()
    assert "FLUXION_API_KEY" not in os.environ


def test_cloud_section_saves_and_switches_engine(make_window, monkeypatch):
    # cli.app binds licensing.feature_enabled at import: import it before the
    # patch, or its first import here keeps the "always Pro" stub for good and
    # later tests (test_licensing) see every feature unlocked
    import cli.app
    import licensing
    from core.config import Settings

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    monkeypatch.setattr(cli.app, "feature_enabled", lambda feature: True)

    class FakeAssistant:
        backend = None

    assistant = FakeAssistant()
    window = make_window(settings=Settings(), assistant=assistant)
    groq = window.cloud_provider.findData("groq")
    window.cloud_provider.setCurrentIndex(groq)
    assert window.cloud_base_url.text() == "https://api.groq.com/openai/v1"
    assert window.cloud_model.currentText() == "llama-3.3-70b-versatile"
    window.cloud_key.setText("gsk_test")

    # enabling without a key is refused
    window.cloud_key.setText("")
    window.cloud_enabled.setChecked(True)
    window._cloud_save()
    assert "заполните" in window.cloud_status.text()

    window.cloud_key.setText("gsk_test")
    window._cloud_save()
    saved = load_cloud_config()
    assert saved.enabled and saved.api_key == "gsk_test" and saved.provider == "groq"
    assert type(window.backend).__name__ == "APIBackend"
    assert assistant.backend is window.backend
    assert window.backend.model == "llama-3.3-70b-versatile"
    assert "облачную модель" in window.cloud_status.text()
    assert "(облако)" in window._status_model_name()

    # switching it off returns to the local engine
    window.cloud_enabled.setChecked(False)
    window._cloud_save()
    assert type(window.backend).__name__ != "APIBackend"
    assert "FLUXION_API_KEY" not in os.environ


# ── Russian providers and GigaChat ───────────────────────────────────────

def test_russian_providers_are_in_the_list():
    keys = [p.key for p in cloud_api.PROVIDERS]
    for key in ("proxyapi", "vsegpt", "aitunnel", "bothub", "gigachat", "yandex", "cloudru"):
        assert key in keys
        provider = cloud_api.provider_by_key(key)
        assert provider.base_url.startswith("https://") and provider.example_model and provider.hint
        assert cloud_api.provider_for_url(provider.base_url + "/") is provider
    assert keys[-1] == "custom" and len(keys) == len(set(keys))


@pytest.fixture()
def gigachat():
    from core import gigachat as module

    module.forget_tokens()
    yield module
    module.forget_tokens()


def test_gigachat_key_is_exchanged_for_a_token_once(monkeypatch, gigachat):
    import time

    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url == gigachat.OAUTH_URL:
            return _Resp(200, {"access_token": "tok-1", "expires_at": int((time.time() + 1800) * 1000)})
        return _Resp(200, {"choices": [{"message": {"content": "p"}}]})

    monkeypatch.setattr(gigachat.httpx, "post", fake_post)
    monkeypatch.setattr(cloud_api.httpx, "post", fake_post)
    cfg = _cfg(provider="gigachat", base_url="https://api.giga.chat/v1", api_key="QmFzZTY0a2V5", model="GigaChat-2")
    assert cloud_api.check_connection(cfg)[0]
    assert cloud_api.check_connection(cfg)[0]
    oauth = [kw for url, kw in calls if url == gigachat.OAUTH_URL]
    assert len(oauth) == 1   # the token is kept for its 30 minutes
    assert oauth[0]["headers"]["Authorization"] == "Basic QmFzZTY0a2V5"
    assert oauth[0]["data"] == {"scope": "GIGACHAT_API_PERS"} and oauth[0]["headers"]["RqUID"]
    chat = [kw for url, kw in calls if url != gigachat.OAUTH_URL]
    assert chat[0]["headers"]["Authorization"] == "Bearer tok-1" and "verify" in chat[0]


def test_gigachat_token_is_renewed_when_it_expires(monkeypatch, gigachat):
    import time

    tokens = iter(["old", "new"])
    monkeypatch.setattr(
        gigachat.httpx, "post",
        lambda url, **kw: _Resp(200, {"access_token": next(tokens), "expires_at": int(time.time() * 1000)}),
    )
    assert gigachat.access_token("k") == "old"
    assert gigachat.access_token("k") == "new"


def test_gigachat_refused_key_is_explained(monkeypatch, gigachat):
    monkeypatch.setattr(gigachat.httpx, "post", lambda url, **kw: _Resp(401, text="bad credentials"))
    cfg = _cfg(base_url="https://api.giga.chat/v1", model="GigaChat-2")
    ok, message = cloud_api.check_connection(cfg)
    assert not ok and "ключ авторизации" in message and "401" in message
    with pytest.raises(RuntimeError, match="GigaChat"):
        cloud_api.list_models(cfg)


def test_other_providers_send_the_key_as_is():
    from core.gigachat import auth_headers, is_gigachat, request_kwargs

    assert auth_headers("https://api.proxyapi.ru/v1", "sk-1") == {"Authorization": "Bearer sk-1"}
    assert request_kwargs("https://api.vsegpt.ru/v1") == {}
    assert is_gigachat("https://gigachat.devices.sberbank.ru/api/v1") and not is_gigachat("https://evilgiga.chat/v1")


def test_provider_hint_is_shown(make_window):
    window = make_window()
    window.cloud_provider.setCurrentIndex(window.cloud_provider.findData("yandex"))
    assert window.cloud_base_url.text() == "https://llm.api.cloud.yandex.net/v1"
    assert window.cloud_model.currentText() == "gpt://<folder_id>/yandexgpt/latest"
    assert "folder_id" in window.cloud_status.text()
