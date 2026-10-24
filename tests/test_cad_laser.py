"""«Пакет на лазер» (cad/laser.py over the core of DXFka) without KOMPAS-3D."""
from __future__ import annotations

import os
import time

import pytest

from cad import laser


def _part(name, material="", is_sheet=None):
    return {"file_path": rf"C:\m\{name}.m3d", "name": name, "marking": f"1.{name}", "material": material,
            "is_sheet": is_sheet}


class FakeWorker:
    """Stands for dxfka.worker.ExportWorker: records the call, says what the test wants."""

    made: list = []

    def __init__(self, msg_queue, resume, cancel, skip, mode, payload, lic, logger):
        self.msg_queue, self.mode, self.payload, self.lic = msg_queue, mode, payload, lic
        self.auto_mode = False
        self.template_path = None
        self.thickness_result = None
        import threading

        self.thickness_event = threading.Event()
        FakeWorker.made.append(self)

    def start(self):
        if self.mode == "traverse":
            self.msg_queue.put({"type": "TRAVERSAL_DONE", "parts": [
                _part("Стенка", "Лист$dБ-ПУ-О-10 ГОСТ 19903-2015;345-09Г2С$"),
                _part("Ребро", is_sheet=True),
                _part("Вал", "Круг 40 ГОСТ 2590"),
            ]})
        else:
            out_dir, parts = self.payload
            for p in parts:
                folder = os.path.join(out_dir, "10")
                os.makedirs(folder, exist_ok=True)
                open(os.path.join(folder, p.name + ".dxf"), "w").close()
            self.msg_queue.put({"type": "DONE", "text": "Экспорт завершён.\nУспешно: 2"})

    def is_alive(self):
        return False


@pytest.fixture
def fake_dxfka(monkeypatch):
    import dxfka.worker

    FakeWorker.made = []
    monkeypatch.setattr(dxfka.worker, "ExportWorker", FakeWorker)
    monkeypatch.setattr(laser, "_logger", lambda: None)
    return FakeWorker


def test_only_details_cut_from_a_sheet_go_to_the_laser(tmp_path, fake_dxfka):
    job = laser.LaserJob(r"C:\m\1.000.a3d", tmp_path / "out")
    job.start()
    walk = job.queue.get_nowait()
    assert fake_dxfka.made[0].mode == "traverse" and fake_dxfka.made[0].auto_mode
    assert job.export(walk["parts"]) == 2
    assert [p.name for p in job.parts] == ["Стенка", "Ребро"]
    assert [p.name for p in job.left_out] == ["Вал"]
    assert job.queue.get_nowait()["type"] == "DONE"
    assert laser.thickness_summary(tmp_path / "out") == [("10", 2)]
    assert isinstance(fake_dxfka.made[1].lic, laser.ProLicense)


def test_answers_to_the_worker(tmp_path, fake_dxfka):
    job = laser.LaserJob(r"C:\m\1.000.a3d", tmp_path)
    job.start()
    worker = fake_dxfka.made[0]
    job.answer_thickness("1,5")
    assert worker.thickness_result == ("ok", "1.5") and worker.thickness_event.is_set()
    job.answer_thickness("")
    assert worker.thickness_result == ("skip", "")
    job.answer_thickness(None)
    assert worker.thickness_result == ("cancel", "")
    job.skip()
    assert job.skip_event.is_set() and job.resume_event.is_set()
    job.cancel()
    assert job.cancel_event.is_set()


def test_pro_licence(monkeypatch):
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: feature == "laser_dxf")
    assert laser.ProLicense().reserve()
    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    assert not laser.ProLicense().reserve()
    from licensing import PRO_FEATURES

    assert laser.PRO_FEATURE in PRO_FEATURES


def test_default_out_dir(tmp_path, monkeypatch):
    project = tmp_path / "792720" / "Рабочка"
    (project / "Модель").mkdir(parents=True)
    asm = project / "Модель" / "792720.2801018 - Ловитель.a3d"
    assert laser.default_out_dir(asm).name.endswith("Ловитель - DXF")  # no DXF folder: Документы\Fluxion CAD
    (project / "DXF").mkdir()
    assert laser.default_out_dir(asm) == project / "DXF" / asm.stem  # the project's own DXF folder


def test_vendored_dxfka_has_no_licence_secret():
    import dxfka.config as config

    assert not hasattr(config, "SECRET") and config.DEV_MODE is False
    assert "Fluxion" in config.LICENSE_CONTACT_HINT and "Fluxion" in config.LOG_FILE


def test_window_runs_the_package(tmp_path, monkeypatch, fake_dxfka):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        import desktop_browser.app as appmod
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    import desktop_browser.crash as crash

    monkeypatch.setattr(crash, "crash_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    window = appmod.FluxionWindow(qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    window.start_laser_package(r"C:\m\1.000.a3d", str(tmp_path / "out"))
    deadline = time.time() + 5
    while window.generating and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    text = window.messages.toPlainText()
    assert "на лазер (из листа): 2" in text and "Экспорт завершён." in text
    assert "10 мм — 2 DXF" in text and "Открыть папку с DXF" in text
    assert window.laser_job is None and not window.generating
    window.close()
