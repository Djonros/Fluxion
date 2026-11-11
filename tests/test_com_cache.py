"""A half-written win32com gen_py cache is rebuilt once instead of leaving KOMPAS without wrappers.

The broken cache shows as «module 'win32com.gen_py.…' has no attribute 'CLSIDToClassMap'»
(or 'MinorVersion') from gencache.EnsureModule; then every laser part failed with
«Нет обёрток API7 — листовое тело не ищу».
"""
from __future__ import annotations

import logging
import queue
import threading
import types

import pytest

import dxfka.kompas.connection as connection
import dxfka.worker as worker_mod

BROKEN = "module 'win32com.gen_py.69AC2981-37C0-4379-84FD-5DD2F3C0A520x0x1x0' has no attribute 'CLSIDToClassMap'"


class FakeGencache:
    """EnsureModule fails *fail* times, then gives a module named after the GUID."""

    def __init__(self, fail: int):
        self.fail = fail
        self.calls: list[str] = []

    def EnsureModule(self, guid, lcid, major, minor):  # noqa: N802 - the pywin32 name
        self.calls.append(guid)
        if self.fail:
            self.fail -= 1
            raise AttributeError(BROKEN)
        return types.SimpleNamespace(guid=guid, constants=types.SimpleNamespace(),
                                     IKompasDocument3D=object, IAssemblyDocument=object)


@pytest.fixture
def fake_cache(monkeypatch):
    def make(fail: int):
        cache = FakeGencache(fail)
        cleared: list[bool] = []
        monkeypatch.setattr(connection, "gencache", cache)
        monkeypatch.setattr(connection, "clear_com_cache", lambda: cleared.append(True) or [])
        return cache, cleared
    return make


def test_a_broken_cache_is_cleared_and_the_module_made_again(fake_cache):
    cache, cleared = fake_cache(fail=1)
    log: list[tuple[str, str]] = []
    module = connection.ensure_module("{API7}", lambda level, text: log.append((level, text)))
    assert module.guid == "{API7}"
    assert cache.calls == ["{API7}", "{API7}"] and cleared == [True]
    assert any("повреждён" in text and "CLSIDToClassMap" in text for _, text in log)
    assert log[-1] == ("info", "Кеш COM-обёрток пересоздан")


def test_a_good_cache_is_left_alone(fake_cache):
    cache, cleared = fake_cache(fail=0)
    connection.ensure_module("{API7}")
    assert cache.calls == ["{API7}"] and cleared == []


def test_a_cache_broken_twice_gives_the_error_to_the_caller(fake_cache):
    cache, cleared = fake_cache(fail=2)
    with pytest.raises(AttributeError, match="CLSIDToClassMap"):
        connection.ensure_module("{API7}")
    assert len(cache.calls) == 2 and cleared == [True]


def test_connect_rebuilds_the_cache_before_going_dynamic(fake_cache, monkeypatch):
    """The first EnsureModule of connect() fails as on the user's PC; the wrappers still come."""
    cache, cleared = fake_cache(fail=1)
    monkeypatch.setattr(connection, "PYWIN32_OK", True)
    monkeypatch.setattr(connection, "pythoncom", types.SimpleNamespace(CoInitialize=lambda: None,
                                                                      IID_IDispatch=None))
    stop = RuntimeError("stop after the typelibs")

    def no_kompas(progid):
        raise stop
    monkeypatch.setattr(connection, "GetActiveObject", no_kompas)
    monkeypatch.setattr(connection, "Dispatch", no_kompas)
    conn = connection.KompasConnection(lambda level, text: None)
    with pytest.raises(connection.KompasConnectionError):
        conn.connect()  # no KOMPAS here: stops after the typelibs
    assert cleared == [True]
    assert conn.constants is not None and conn.api5_module is not None
    assert conn.api7_module is not None and conn.api7_module.guid == connection.TYPELIB_API7


def test_cad_modules_use_the_repairing_ensure(fake_cache):
    from cad import kompas

    cache, cleared = fake_cache(fail=1)
    assert kompas._ensure("{API5}").guid == "{API5}"
    assert cleared == [True]


class NoWrappersConnection:
    """KompasConnection after connect() on a PC where the wrappers could not be made."""

    started_by_us = False
    api7_module = None

    def __init__(self, log):
        pass

    def connect(self):
        pass


def test_export_stops_at_once_without_api7_wrappers(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_mod, "PYWIN32_OK", True)
    monkeypatch.setattr(worker_mod, "pythoncom", types.SimpleNamespace(CoInitialize=lambda: None,
                                                                      CoUninitialize=lambda: None))
    monkeypatch.setattr(worker_mod, "KompasConnection", NoWrappersConnection)

    def no_exporter(*args, **kwargs):
        raise AssertionError("the parts must not be exported one by one")
    monkeypatch.setattr(worker_mod, "PartExporter", no_exporter)
    messages: queue.Queue = queue.Queue()
    worker = worker_mod.ExportWorker(messages, threading.Event(), threading.Event(), threading.Event(),
                                     "export", (str(tmp_path), []), lic=None,
                                     logger=logging.getLogger("test_com_cache"))
    worker.run()
    fatal = [m for m in list(messages.queue) if m.get("type") == "FATAL"]
    assert fatal and "gen_py" in fatal[0]["text"] and "API7" in fatal[0]["text"]
