"""«Мой стиль»: the profile of the user's manner of writing and its way into the
chat (orchestrator/writing_style.py, the prompt, the chip of the window)."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from orchestrator import writing_style
from orchestrator.prompt_builder import PromptBuilder, PromptContext
from orchestrator.writing_style import LETTER, MEMO, NOTE, OTHER, Sample, StyleProfile

MEMO_TEXT = ("Генеральному директору ООО «Сибтрейл» Иванову И.И.\n\nСлужебная записка\n\n"
             "Прошу согласовать закупку листа 09Г2С толщиной 10 мм для рамы 792720. "
             "Обоснование: текущий запас исчерпан.\n\nНачальник КБ Петров П.П.")
LETTER_TEXT = ("Уважаемый Сергей Николаевич!\n\nНаправляем Вам чертежи трейлера 792720 на согласование. "
               "Просим рассмотреть до 30.10.2026 и сообщить замечания.\n\nС уважением, КБ «Сибтрейл»")


class FakeBackend:
    def __init__(self, reply="- Обращение: «Уважаемый …!»\n- Подпись: «С уважением, КБ»"):
        self.reply = reply
        self.calls = []

    def generate(self, messages, gen=None):
        self.calls.append(messages)
        return self.reply


def test_kind_of_a_request_or_a_sample():
    assert writing_style.detect_kind("напиши служебку на закупку листа") == MEMO
    assert writing_style.detect_kind("Служебная записка") == MEMO
    assert writing_style.detect_kind("составь поясниловку к проекту") == NOTE
    assert writing_style.detect_kind("ПЗ к рабочему проекту") == NOTE
    assert writing_style.detect_kind("ответь заказчику, что сроки сдвигаются") == LETTER
    assert writing_style.detect_kind("напиши письмо в Камаз") == LETTER
    assert writing_style.detect_kind("как посчитать момент инерции") == OTHER


def test_profile_and_samples_are_kept(tmp_path):
    assert not writing_style.load(tmp_path).ready
    style = StyleProfile("- Пишет от лица КБ", [Sample("записка.docx", MEMO, MEMO_TEXT)])
    writing_style.save(tmp_path, style)
    again = writing_style.load(tmp_path)
    assert again.ready and again.profile == style.profile
    assert again.samples == style.samples


def test_read_sample_guesses_its_kind(tmp_path):
    path = tmp_path / "Служебка закупка.txt"
    path.write_text(MEMO_TEXT, encoding="utf-8")
    sample = writing_style.read_sample(path)
    assert sample.kind == MEMO and sample.name == path.name and "09Г2С" in sample.text


def test_analyze_sends_the_samples_and_returns_the_profile():
    backend = FakeBackend()
    profile = writing_style.analyze([Sample("a", MEMO, MEMO_TEXT), Sample("b", LETTER, LETTER_TEXT)], backend)
    assert profile.startswith("- Обращение")
    body = backend.calls[0][1]["content"]
    assert "Образец 1 — Служебная записка" in body and "Уважаемый Сергей Николаевич" in body


def test_analyze_needs_a_real_sample():
    with pytest.raises(writing_style.WritingStyleError):
        writing_style.analyze([Sample("a", OTHER, "коротко")], FakeBackend())
    with pytest.raises(writing_style.WritingStyleError):
        writing_style.analyze([Sample("a", MEMO, MEMO_TEXT)], FakeBackend(reply="  "))


def test_prompt_block_takes_the_samples_of_the_same_kind():
    style = StyleProfile("- Подпись: «С уважением, КБ»", [
        Sample("записка", MEMO, MEMO_TEXT), Sample("письмо", LETTER, LETTER_TEXT),
        Sample("ещё записка", MEMO, MEMO_TEXT.replace("закупку", "ремонт")),
    ])
    block = writing_style.prompt_block(style, "напиши письмо заказчику о переносе сроков")
    assert "С уважением, КБ" in block and "Сейчас нужен вид текста: Письмо заказчику" in block
    assert block.index("--- Письмо заказчику ---") < block.index("--- Служебная записка ---")
    assert block.count("--- ") == writing_style.PROMPT_SAMPLES
    assert writing_style.prompt_block(StyleProfile(), "напиши письмо") == ""


def test_style_goes_into_the_system_prompt():
    builder = PromptBuilder(system_prompt="BASE")
    plain = builder.build(PromptContext(query="q"))
    styled = builder.build(PromptContext(query="q", style="Пиши в манере пользователя"))
    assert plain[0]["content"] == "BASE"
    assert styled[0]["content"].startswith("BASE") and "Пиши в манере пользователя" in styled[0]["content"]


def test_writing_style_is_a_pro_feature():
    from licensing import PRO_FEATURES

    assert writing_style.PRO_FEATURE in PRO_FEATURES


class FakeAssistant:
    def __init__(self):
        self.calls = []

    def ask_stream(self, query, history=None, **kwargs):
        self.calls.append(kwargs)
        meta = SimpleNamespace(strategy=SimpleNamespace(name="DIRECT"), rag_used=False, rag_sources=[],
                               web_used=False, web_sources=[])
        return meta, iter(["Ок"])


def test_chip_sends_the_style_with_the_request(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        import licensing
        from desktop_browser.app import FluxionWindow
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    app = QApplication.instance() or QApplication([])
    style = StyleProfile("- Подпись: «С уважением, КБ»", [Sample("письмо", LETTER, LETTER_TEXT)])
    monkeypatch.setattr(writing_style, "load", lambda data_dir: style)
    pro = {"on": False}
    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: pro["on"])
    fake = FakeAssistant()
    window = FluxionWindow(assistant=fake, qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    from desktop_browser import app as appmod

    monkeypatch.setattr(appmod.QMessageBox, "information", lambda *a, **k: None)

    def send(text):
        window.prompt.setText(text)
        window.send_message()
        deadline = time.time() + 5
        while window.generating and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)

    window.chat_style.setChecked(True)  # without Pro the chip does not stay on
    assert not window.chat_style.isChecked()
    pro["on"] = True
    window.chat_style.setChecked(True)
    assert window.chat_style.isChecked()
    send("напиши письмо заказчику")
    assert "С уважением, КБ" in fake.calls[-1]["style"]
    window.chat_style.setChecked(False)
    send("а теперь просто вопрос")
    assert "style" not in fake.calls[-1]
    window.close()


def test_dialog_makes_and_saves_the_profile(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication

        from desktop_browser import writing_style_dialog as dialogmod
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "письмо.txt"
    path.write_text(LETTER_TEXT, encoding="utf-8")
    monkeypatch.setattr(dialogmod.QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(path)], ""))
    dialog = dialogmod.WritingStyleDialog(FakeBackend(), tmp_path / "data")
    dialog._add_files()
    assert dialog.table.rowCount() == 1 and dialog.samples()[0].kind == LETTER
    dialog.table.cellWidget(0, 0).setCurrentText(MEMO)  # the kind can be changed by hand
    dialog._analyze()
    dialog.worker.wait(5000)
    app.processEvents()
    assert dialog.profile.toPlainText().startswith("- Обращение")
    dialog._save()
    saved = writing_style.load(tmp_path / "data")
    assert saved.ready and saved.samples[0].kind == MEMO and saved.samples[0].name == "письмо.txt"
    dialog.table.selectRow(0)
    dialog._remove()
    assert dialog.table.rowCount() == 0 and dialog.samples() == []
