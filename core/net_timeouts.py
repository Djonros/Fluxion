"""Time limits of the streaming requests to a model (Ollama, cloud APIs).

A stream used to wait without a limit: a connection that stalled kept the chat
waiting until «Стоп». Now the connection has 10 s, and the stream may be silent
for 5 minutes between two pieces (a big local model thinking over a long
context is slow, but not that slow); a model download may be silent for an hour.
"""
from __future__ import annotations

import httpx

CONNECT_S = 10.0
STREAM_READ_S = 300.0
DOWNLOAD_READ_S = 3600.0

STREAM = httpx.Timeout(connect=CONNECT_S, read=STREAM_READ_S, write=60.0, pool=CONNECT_S)
DOWNLOAD = httpx.Timeout(connect=CONNECT_S, read=DOWNLOAD_READ_S, write=60.0, pool=CONNECT_S)


def timeout_message(exc: httpx.TimeoutException, where: str) -> str:
    """What the user reads when *where* (a server name) did not answer in time."""
    if isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout)):
        return f"{where}: нет связи за {CONNECT_S:.0f} с — проверьте, что сервер запущен и доступен."
    return (f"{where}: ответ прервался — модель молчит больше {STREAM_READ_S / 60:.0f} мин. "
            "Повторите запрос или выберите модель поменьше.")
