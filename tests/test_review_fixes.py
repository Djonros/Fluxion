"""The fixes of the 0.29.0 review: bending units, /adapters register, stream time limits,
files sent to the bots, the DXF assembly walk."""
from __future__ import annotations

import contextlib
import types

import httpx
import pytest

# ── bending: «м» is metres ──────────────────────────────────────────────────


def test_flanges_in_metres_centimetres_and_millimetres():
    from calc import bending

    assert bending._flanges("40; 0,5 м; 40") == [40.0, 500.0, 40.0]
    assert bending._flanges("40 мм; 10 см; 0,1м") == [40.0, 100.0, 100.0]
    assert bending._flanges("40; 100; 40") == [40.0, 100.0, 40.0]


def test_no_control_characters_in_the_unit_pattern():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "calc" / "bending.py").read_bytes()
    assert b"\x08" not in text


# ── /adapters register in the console ───────────────────────────────────────


def test_adapters_register_in_the_console(tmp_path):
    import cli.app
    from core.config import Settings

    said: list[tuple[str, str]] = []
    renderer = types.SimpleNamespace(**{k: (lambda text, k=k: said.append((k, str(text))))
                                        for k in ("info", "error", "success", "warn")})
    renderer.console = types.SimpleNamespace(print=lambda *a, **k: None)
    repl = types.SimpleNamespace(renderer=renderer)
    settings = Settings()
    settings.paths.data_dir = str(tmp_path)
    registry = cli.app.build_registry(repl, backend=None, settings=settings)
    registry.get("adapters").handler(["register", "drawings", "--model", "fluxion-drawings",
                                      "--path", str(tmp_path), "ГОСТ", "чертежи"])
    assert ("success", "Registered adapter 'drawings' -> fluxion-drawings") in said, said


# ── streams have time limits ────────────────────────────────────────────────


def _stalled(exc):
    @contextlib.contextmanager
    def stream(*args, **kwargs):
        assert kwargs["timeout"] is not None and kwargs["timeout"].read == 300.0
        raise exc
        yield  # pragma: no cover
    return stream


def test_ollama_stream_that_stalls_ends_with_a_clear_message(monkeypatch):
    from core import ollama_client

    monkeypatch.setattr(ollama_client.httpx, "stream", _stalled(httpx.ReadTimeout("stalled")))
    client = ollama_client.OllamaClient("http://127.0.0.1:11434", "qwen")
    with pytest.raises(ollama_client.OllamaError, match="молчит больше 5 мин"):
        list(client.stream_chat([{"role": "user", "content": "привет"}]))


def test_cloud_stream_without_connection_ends_with_a_clear_message(monkeypatch):
    from core import api_backend

    monkeypatch.setattr(api_backend.httpx, "stream", _stalled(httpx.ConnectTimeout("no route")))
    backend = api_backend.APIBackend.__new__(api_backend.APIBackend)
    backend.base_url = "https://api.example.test/v1"
    backend.api_key = "k"
    backend.model = "m"
    backend.timeout = 120.0
    monkeypatch.setattr(backend, "_payload", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(backend, "_headers", lambda: {}, raising=False)
    with pytest.raises(RuntimeError, match="нет связи за 10 с"):
        list(backend.stream([{"role": "user", "content": "привет"}]))


def test_model_download_keeps_a_long_read_limit():
    from core.net_timeouts import DOWNLOAD

    assert DOWNLOAD.read == 3600.0 and DOWNLOAD.connect == 10.0


# ── files sent to the bots ──────────────────────────────────────────────────


@pytest.mark.parametrize("sent, saved", [
    ("..\\..\\Windows\\evil.exe", "evil.exe"),
    ("../../.ssh/authorized_keys", "authorized_keys"),
    ("CON.txt", "file_CON.txt"),
    ('чертёж<1>:"a"?.dxf', "чертёж_1___a__.dxf"),
    ("", "file"),
    (" .. ", "file"),
])
def test_safe_names(sent, saved):
    from bots.base import safe_name

    assert safe_name(sent) == saved


def test_a_long_name_keeps_its_extension():
    from bots.base import safe_name

    name = safe_name("д" * 300 + ".dxf")
    assert len(name) <= 150 and name.endswith(".dxf")


def test_a_download_stays_in_its_folder_and_keeps_an_older_file(tmp_path):
    from bots.base import save_download

    (tmp_path / "plan.pdf").write_bytes(b"old")
    first = save_download(tmp_path, "../plan.pdf", b"new", "Telegram")
    second = save_download(tmp_path, "plan.pdf", b"newer", "Telegram")
    assert first == tmp_path.resolve() / "plan (2).pdf" and first.read_bytes() == b"new"
    assert second.name == "plan (3).pdf"
    assert (tmp_path / "plan.pdf").read_bytes() == b"old"
    assert not (tmp_path.parent / "plan.pdf").exists()


def test_a_too_big_download_is_not_saved(tmp_path, monkeypatch):
    from bots import base

    monkeypatch.setattr(base, "MAX_DOWNLOAD", 10)
    with pytest.raises(base.BotError, match="больше"):
        base.save_download(tmp_path, "big.bin", b"x" * 11, "MAX")
    assert not list(tmp_path.iterdir())


def test_telegram_download_uses_the_safe_name(tmp_path):
    from bots.base import Attachment
    from bots.telegram import Telegram

    class Response:
        status_code = 200
        content = b"%PDF"

        def json(self):
            return {"ok": True, "result": {"file_path": "documents/file_7.pdf"}}

    class Session:
        def get(self, url, **kwargs):
            return Response()

        def post(self, url, **kwargs):
            return Response()

    bot = Telegram("123:ABC", session=Session())
    saved = bot.download(Attachment(kind="file", ref="id", name="..\\..\\boot.ini"), tmp_path)
    assert saved == tmp_path.resolve() / "boot.ini" and saved.read_bytes() == b"%PDF"


# ── DXF: the walk of an assembly ────────────────────────────────────────────


@pytest.mark.parametrize("path, standard", [
    (r"C:\Program Files\ASCON\KOMPAS-3D v24\Libs\PolynomLib\DATA\PLib_Bolt_1_900.m3d", True),
    (r"D:\Program Files (x86)\ASCON\a.m3d", True),
    (r"E:\KOMPAS-3D v23\Libs\b.m3d", True),
    (r"D:\Работа\ASCON\Надстройка\Лист 3.m3d", False),
    (r"C:\Users\PC\Documents\Mascon\d.m3d", False),
    (r"Y:\Яндекс.Диск\Проекты\562001\Пластина.m3d", False),
])
def test_standard_parts_are_library_files_only(path, standard, monkeypatch):
    from dxfka import utils

    monkeypatch.setenv("ProgramData", r"C:\ProgramData")
    assert utils.is_standard_part_path(path) is standard


def test_program_data_ascon_is_a_library(monkeypatch):
    from dxfka import utils

    monkeypatch.setenv("ProgramData", r"C:\ProgramData")
    assert utils.is_standard_part_path(r"C:\ProgramData\ASCON\Standard\c.m3d")


class Collection:
    """A COM collection of KOMPAS: Count and Item(i); *missing* indices give nothing."""

    def __init__(self, items, base=0, missing=()):
        self.items, self.base, self.missing = items, base, set(missing)
        self.Count = len(items)

    def Item(self, index):  # noqa: N802 - the COM name
        if index in self.missing:
            raise RuntimeError("Неверный индекс")
        position = index - self.base
        if not 0 <= position < len(self.items):
            raise RuntimeError("Неверный индекс")
        return self.items[position]


def test_one_missing_component_does_not_empty_the_list():
    from dxfka.utils import com_items

    log: list[tuple[str, str]] = []
    items = com_items(Collection(["a", "b", "c", "d"], missing={2}), lambda lv, t: log.append((lv, t)))
    assert items == ["a", "b", "d"]
    assert log and log[0][0] == "warning" and "1 из 4" in log[0][1]


def test_collections_numbered_from_one():
    from dxfka.utils import com_items

    assert com_items(Collection(["a", "b"], base=1)) == ["a", "b"]


def _part(name, path="", marking="", ref=""):
    return types.SimpleNamespace(Name=name, PathName=path, FileName="", Path="", Marking=marking,
                                 Reference=ref, Material="Лист 3", Parts=None,
                                 IsAssembly="", AssemblyContent="")


def test_parts_without_reference_and_marking_are_not_merged(monkeypatch, tmp_path):
    from dxfka.kompas import walker as walker_mod

    monkeypatch.setattr(walker_mod, "CastTo", lambda obj, iface: obj)
    walk = walker_mod.AssemblyWalker(conn=None, log=lambda lv, t: None)
    a = _part("Пластина", str(tmp_path / "Пластина.m3d"))
    b = _part("Пластина", str(tmp_path / "Пластина-01.m3d"))
    pairs: list = []
    refs: set = set()
    seen: set = set()
    for child in (a, b):
        walk._process_component(child, pairs, refs, seen, 1, str(tmp_path))
    assert [info.file_path for info, _ in pairs] == [a.PathName, b.PathName]
