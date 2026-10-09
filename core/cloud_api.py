"""Cloud model settings of the desktop app (Pro): provider, key, model.

The API backend (``core/api_backend.py``) reads ``FLUXION_API_KEY``,
``FLUXION_API_BASE_URL`` and ``FLUXION_API_MODEL``.  The app's "Облачная
модель" section stores these per user in ``cloud_api.json`` and puts them into
the environment on start and on save, so the backend factory picks them up.
Variables the user set in the system environment or ``.env`` always win.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    base_url: str
    example_model: str


PROVIDERS: tuple[Provider, ...] = (
    Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "qwen/qwen-2.5-coder-32b-instruct"),
    Provider("groq", "Groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    Provider("together", "Together AI", "https://api.together.xyz/v1", "Qwen/Qwen2.5-Coder-32B-Instruct"),
    Provider("openai", "OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    Provider("custom", "Свой адрес (vLLM, LM Studio…)", "", ""),
)


def provider_by_key(key: str) -> Provider:
    return next((p for p in PROVIDERS if p.key == key), PROVIDERS[-1])


def provider_for_url(base_url: str) -> Provider:
    url = base_url.strip().rstrip("/")
    return next((p for p in PROVIDERS if p.base_url and p.base_url == url), PROVIDERS[-1])


@dataclass
class CloudConfig:
    enabled: bool = False
    provider: str = "openrouter"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: str = ""
    model: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.api_key.strip() and self.base_url.strip() and self.model.strip())


def cloud_config_path() -> Path:
    """Frozen builds keep it per user in %APPDATA%\\Fluxion (next to the
    licence); dev runs use data/."""
    override = os.environ.get("FLUXION_CLOUD_API_FILE", "").strip()
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "Fluxion" / "cloud_api.json"
    return Path(__file__).resolve().parents[1] / "data" / "cloud_api.json"


def load_cloud_config(path: Path | None = None) -> CloudConfig:
    path = path or cloud_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CloudConfig()
    if not isinstance(data, dict):
        return CloudConfig()
    known = {k: data[k] for k in asdict(CloudConfig()) if k in data}
    cfg = CloudConfig(**known)
    cfg.enabled = bool(cfg.enabled)
    for name in ("provider", "base_url", "api_key", "model"):
        setattr(cfg, name, str(getattr(cfg, name) or "").strip())
    return cfg


def save_cloud_config(cfg: CloudConfig, path: Path | None = None) -> Path:
    path = path or cloud_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


_ENV_KEYS = ("FLUXION_BACKEND", "FLUXION_API_KEY", "FLUXION_API_BASE_URL", "FLUXION_API_MODEL")
# Variables this module put into os.environ (and so may change or remove).
_applied: dict[str, str] = {}


def _owned(name: str) -> bool:
    """True when the variable is unset or was set by us, not by the user."""
    return name not in os.environ or _applied.get(name) == os.environ.get(name)


def apply_cloud_config(cfg: CloudConfig | None = None) -> bool:
    """Put the saved cloud settings into the environment (or take them out).

    Returns True when the API backend is switched on by these settings.
    """
    cfg = cfg if cfg is not None else load_cloud_config()
    wanted: dict[str, str] = {}
    if cfg.enabled and cfg.complete:
        wanted = {
            "FLUXION_BACKEND": "api",
            "FLUXION_API_KEY": cfg.api_key.strip(),
            "FLUXION_API_BASE_URL": cfg.base_url.strip().rstrip("/"),
            "FLUXION_API_MODEL": cfg.model.strip(),
        }
    for name in _ENV_KEYS:
        if not _owned(name):
            continue
        if name in wanted:
            os.environ[name] = wanted[name]
            _applied[name] = wanted[name]
        elif name in _applied:
            os.environ.pop(name, None)
            _applied.pop(name, None)
    return bool(wanted)


def env_overrides() -> list[str]:
    """Cloud variables set outside the app (system environment or .env)."""
    return [name for name in _ENV_KEYS if not _owned(name)]


def _error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            return str(err.get("message") or err)[:200]
        if err:
            return str(err)[:200]
    except ValueError:
        pass
    return response.text[:200]


def check_connection(cfg: CloudConfig, timeout: float = 15.0) -> tuple[bool, str]:
    """Send a one-token request; returns (ok, message for the user)."""
    if not cfg.base_url.strip():
        return False, "Укажите адрес API."
    if not cfg.api_key.strip():
        return False, "Укажите ключ API."
    if not cfg.model.strip():
        return False, "Укажите модель."
    url = cfg.base_url.strip().rstrip("/") + "/chat/completions"
    try:
        r = httpx.post(
            url,
            json={
                "model": cfg.model.strip(),
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            headers={"Authorization": f"Bearer {cfg.api_key.strip()}"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return False, f"Нет связи с {cfg.base_url}: {exc}"
    if r.status_code == 200:
        return True, f"Связь есть: модель {cfg.model} отвечает."
    detail = _error_text(r)
    if r.status_code in (401, 403):
        return False, f"Ключ не принят ({r.status_code}): {detail}"
    if r.status_code == 404:
        return False, f"Модель или адрес не найдены (404): {detail}"
    if r.status_code == 402:
        return False, f"На счёте у провайдера не хватает средств (402): {detail}"
    if r.status_code == 429:
        return False, f"Провайдер ограничил запросы (429), попробуйте позже: {detail}"
    return False, f"Провайдер ответил ошибкой {r.status_code}: {detail}"


def list_models(cfg: CloudConfig, timeout: float = 15.0) -> list[str]:
    """Model ids from the provider's /models (raises RuntimeError on failure)."""
    if not cfg.base_url.strip():
        raise RuntimeError("Укажите адрес API.")
    headers = {"Authorization": f"Bearer {cfg.api_key.strip()}"} if cfg.api_key.strip() else {}
    try:
        r = httpx.get(cfg.base_url.strip().rstrip("/") + "/models", headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Нет связи с {cfg.base_url}: {exc}") from exc
    if r.status_code != 200:
        raise RuntimeError(f"Список моделей недоступен ({r.status_code}): {_error_text(r)}")
    try:
        data = r.json()
    except ValueError as exc:
        raise RuntimeError("Провайдер вернул не JSON") from exc
    items = data.get("data", []) if isinstance(data, dict) else data
    ids = [str(m.get("id")) for m in items or [] if isinstance(m, dict) and m.get("id")]
    return sorted(set(ids), key=str.lower)
