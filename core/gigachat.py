"""GigaChat (Сбер): an access token from the authorization key.

GigaChat speaks the OpenAI format at ``https://api.giga.chat/v1``, but the
key from the developer's account (the «ключ авторизации») is not sent with
the requests: it is exchanged for an access token that lives 30 minutes.
``auth_headers`` does the exchange and keeps the token until it is about to
expire, so the rest of Fluxion treats GigaChat like any other provider.

Sber's servers use certificates of the Russian Ministry of Digital Development
(«Сертификаты Минцифры»).  ``verify_context`` trusts the certificates of the
system (installed by the user from gosuslugi.ru/crt) together with the usual
ones; ``FLUXION_CA_BUNDLE`` points to a certificate file instead.
"""
from __future__ import annotations

import os
import ssl
import threading
import time
import uuid
from urllib.parse import urlparse

import httpx

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
DEFAULT_SCOPE = "GIGACHAT_API_PERS"
_HOSTS = ("giga.chat", "gigachat.devices.sberbank.ru")
# Renew the token this long before it expires.
_MARGIN = 60.0

_tokens: dict[tuple[str, str], tuple[str, float]] = {}
_lock = threading.Lock()


class GigaChatAuthError(RuntimeError):
    pass


def is_gigachat(base_url: str) -> bool:
    host = (urlparse(base_url.strip()).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _HOSTS)


def verify_context() -> ssl.SSLContext:
    """Certificates of the system (Windows: its store) plus certifi's."""
    bundle = os.environ.get("FLUXION_CA_BUNDLE", "").strip()
    if bundle:
        return ssl.create_default_context(cafile=bundle)
    ctx = ssl.create_default_context()
    try:
        import certifi

        ctx.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    return ctx


def _scope() -> str:
    return os.environ.get("FLUXION_GIGACHAT_SCOPE", "").strip() or DEFAULT_SCOPE


def _explain(exc: Exception) -> str:
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text or isinstance(exc, ssl.SSLError):
        return ("сервер Сбера не прошёл проверку сертификата: установите «Сертификаты Минцифры» "
                "(gosuslugi.ru/crt) и перезапустите Fluxion")
    return text


def access_token(auth_key: str, timeout: float = 15.0) -> str:
    """A valid access token for *auth_key*, from the cache or a new one."""
    auth_key = auth_key.strip()
    if auth_key.lower().startswith("basic "):
        auth_key = auth_key[6:].strip()
    scope = _scope()
    with _lock:
        token, expires = _tokens.get((auth_key, scope), ("", 0.0))
        if token and expires - _MARGIN > time.time():
            return token
        try:
            r = httpx.post(
                OAUTH_URL,
                data={"scope": scope},
                headers={
                    "Authorization": f"Basic {auth_key}",
                    "RqUID": str(uuid.uuid4()),
                    "Accept": "application/json",
                },
                timeout=timeout,
                verify=verify_context(),
            )
        except httpx.HTTPError as exc:
            raise GigaChatAuthError(f"GigaChat: нет связи с сервером авторизации: {_explain(exc)}") from exc
        if r.status_code != 200:
            raise GigaChatAuthError(
                f"GigaChat не принял ключ авторизации ({r.status_code}): {r.text[:200]}"
            )
        try:
            data = r.json()
            token = str(data["access_token"])
        except (ValueError, KeyError, TypeError) as exc:
            raise GigaChatAuthError("GigaChat вернул ответ без токена доступа") from exc
        # expires_at is in milliseconds since the epoch
        expires_at = data.get("expires_at")
        expires = float(expires_at) / 1000 if expires_at else time.time() + 30 * 60
        _tokens[(auth_key, scope)] = (token, expires)
        return token


def forget_tokens() -> None:
    with _lock:
        _tokens.clear()


def auth_headers(base_url: str, api_key: str) -> dict[str, str]:
    """``Authorization`` for *base_url*: the key itself, or GigaChat's token."""
    key = api_key.strip()
    if is_gigachat(base_url):
        key = access_token(key)
    return {"Authorization": f"Bearer {key}"} if key else {}


def request_kwargs(base_url: str) -> dict:
    """Extra httpx arguments for *base_url* (GigaChat: the certificates)."""
    return {"verify": verify_context()} if is_gigachat(base_url) else {}
