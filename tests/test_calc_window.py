"""«Инженерный калькулятор» in the desktop app: the window with its tabs, the Pro report,
«/расчёт» in the chat with its question and the window opened with the chat calculation."""
from __future__ import annotations

import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_every_tab_calculates(app):
    from desktop_browser.calc_dialog import CalcDialog

    dialog = CalcDialog()
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == ["Швы", "Болты", "Балки", "Гибка", "Валы"]
    filled = {
        "weld": {"force": "50 кН", "leg": "6", "length": "200"},
        "bolt": {"diameter": "М16", "shear": "40 кН", "count": "4", "plates": "8"},
        "beam": {"length": "6 м", "supports": "1500; 4500", "loads": "20 кН в 0; 5 кН/м", "profile": "труба 100х50х4"},
        "bend": {"thickness": "3", "flanges": "30; 60; 30", "bend_length": "500"},
        "shaft": {"diameter": "40", "torque": "400 Н·м", "moment": "300 Н·м"},
    }
    for kind, values in filled.items():
        tab = dialog.pages[kind]
        dialog.tabs.setCurrentWidget(tab)
        assert not dialog.excel.isEnabled()
        tab.set_values(values)
        tab.run()
        assert tab.result is not None, kind
        assert tab.result.verdict() in tab.view.toPlainText()
        assert dialog.excel.isEnabled() and dialog.pdf.isEnabled()
    assert dialog.pages["beam"]._diagram.endswith(".png")
    dialog.tabs.setCurrentWidget(dialog.pages["weld"])
    dialog.pages["weld"].set_values({"leg": "0"})
    dialog.pages["weld"].run()
    assert "Не получается посчитать" in dialog.pages["weld"].view.toPlainText() and not dialog.excel.isEnabled()
    dialog.close()


def test_report_is_pro(app, tmp_path, monkeypatch):
    import licensing
    from desktop_browser import calc_dialog
    from desktop_browser.calc_dialog import CalcDialog

    dialog = CalcDialog(start=("bolt", {"diameter": "М12", "shear": "10 кН", "plates": "6"}))
    assert dialog.tabs.currentWidget() is dialog.pages["bolt"] and dialog.pages["bolt"].result is not None
    told, opened = [], []
    monkeypatch.setattr(calc_dialog.QMessageBox, "information", lambda parent, title, text: told.append(text))
    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    dialog.save("xlsx")
    assert told and "функция Pro" in told[0]
    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", lambda url: opened.append(url))
    for fmt in ("xlsx", "pdf"):
        target = tmp_path / f"болты.{fmt}"
        monkeypatch.setattr(calc_dialog.QFileDialog, "getSaveFileName", lambda *a, t=target: (str(t), ""))
        dialog.save(fmt)
        assert target.exists() and target.stat().st_size > 1000
    assert (tmp_path / "болты.pdf").read_bytes().startswith(b"%PDF") and len(opened) == 2
    dialog.close()


class _Backend:
    def __init__(self, *answers):
        self.answers = list(answers)

    def generate(self, messages, gen=None):
        return json.dumps(self.answers.pop(0), ensure_ascii=False) if self.answers else "Шов держит с запасом."

    def is_available(self):
        return True


def test_chat_command_asks_counts_and_fills_the_window(app, tmp_path, monkeypatch):
    import desktop_browser.app as appmod

    assert appmod.calc_command("/расчёт балка") == "балка" and appmod.calc_command("/расчётный") is None
    assert appmod.spoken_command("Расчёт, шов катет 6") == "/расчёт шов катет 6"
    assert appmod.spoken_command("посчитай сколько будет два плюс два") == "посчитай сколько будет два плюс два"
    backend = _Backend({"calc": "weld", "params": {"leg": "6 мм", "force": "5 тс"}},
                       {"calc": "weld", "params": {"leg": "6 мм", "force": "5 тс", "length": "200 мм"}})
    window = appmod.FluxionWindow(backend=backend, qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))

    def drain():
        deadline = time.time() + 5
        while window.generating and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()

    window.prompt.setText("/расчёт угловой шов катет 6, 5 тонн")
    window.send_message()
    drain()
    assert "уточните" in window.messages.toPlainText().lower() and window._calc_conversation is not None
    window.prompt.setText("длина 200")
    window.send_message()
    drain()
    text = window.messages.toPlainText()
    assert "Сварной шов: угловой — Запас" in text and "Пояснение (модель): Шов держит" in text
    assert window._calc_conversation is None and window._last_calc[0] == "weld"
    opened = []
    monkeypatch.setattr("desktop_browser.calc_dialog.CalcDialog.exec", lambda self: opened.append(self))
    window._open_calc()
    tab = opened[0].pages["weld"]
    assert opened[0].tabs.currentWidget() is tab and tab.result is not None and tab.values()["length"] == "200 мм"
    window.close()
