"""Voice input and reading answers aloud in the chat window, without a
microphone, a speech model or a sound card: recognition and the voice are faked."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")

import time

import numpy as np
import pytest

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from desktop_browser.app import SPEAK_URL, FluxionWindow
except ImportError as exc:  # PySide6 installed but its libraries do not load
    pytest.skip(f"PySide6 unavailable: {exc}", allow_module_level=True)

import voice.models
import voice.stt
import voice.tts


class FakeSpeaker:
    instances: list["FakeSpeaker"] = []

    def __init__(self):
        self.spoken: list[str] = []
        self.talking = False
        self.error = ""
        FakeSpeaker.instances.append(self)

    def speak(self, markdown):
        self.spoken.append(markdown)
        self.talking = True
        return True

    def speaking(self):
        return self.talking

    def stop(self):
        self.talking = False


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _fakes(tmp_path, monkeypatch):
    import desktop_browser.crash as crash

    monkeypatch.setattr(crash, "crash_dir", lambda: tmp_path)
    FakeSpeaker.instances.clear()
    monkeypatch.setattr(voice.tts, "Speaker", FakeSpeaker)


def _window(qapp, tmp_path, assistant=None):
    qsettings = QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat)
    return FluxionWindow(assistant=assistant, qsettings=qsettings)


def _wait(qapp, condition, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not condition():
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def _fake_model(monkeypatch, text="Привет из микрофона"):
    monkeypatch.setattr(voice.models, "is_installed", lambda model_id, models_dir=None: True)
    monkeypatch.setattr(voice.stt, "transcribe", lambda audio, folder, language="ru": text)
    monkeypatch.setattr(
        voice.stt.Recorder, "record", lambda self: np.zeros(voice.stt.SAMPLE_RATE, dtype=np.float32)
    )


class FakeAssistant:
    def ask_stream(self, query, history=None, **kwargs):
        from types import SimpleNamespace

        meta = SimpleNamespace(
            strategy=SimpleNamespace(name="DIRECT"), rag_used=False, rag_sources=[], web_used=False, web_sources=[]
        )
        return meta, iter(["Ответ ", "```py\nx = 1\n```"])


def test_voice_input_without_model_explains_where_to_get_it(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(voice.models, "is_installed", lambda model_id, models_dir=None: False)
    window = _window(qapp, tmp_path)
    window.toggle_voice_input()
    assert window.voice_worker is None
    assert "Распознавание речи" in window.messages.toPlainText()
    window.close()


def test_dictation_goes_into_the_input_field(qapp, tmp_path, monkeypatch):
    _fake_model(monkeypatch)
    window = _window(qapp, tmp_path)
    window.prompt.setText("Скажи:")
    window.toggle_voice_input()
    assert window.voice_worker is not None
    assert window.mic_button.property("recording")  # the stop square while recording
    _wait(qapp, lambda: window.voice_worker is None)

    assert window.prompt.text() == "Скажи: Привет из микрофона"
    assert not window.generating  # not sent: "send at once" is off by default
    assert window.mic_button.isEnabled() and not window.mic_button.property("recording")
    assert not window.mic_button.icon().isNull()
    window.close()


def test_dictation_sent_at_once_when_switched_on(qapp, tmp_path, monkeypatch):
    _fake_model(monkeypatch, "Как дела?")
    window = _window(qapp, tmp_path, assistant=FakeAssistant())
    window.voice_send_at_once.setChecked(True)
    window.toggle_voice_input()
    _wait(qapp, lambda: window.voice_worker is None and not window.generating)
    assert window.history[0] == {"role": "user", "content": "Как дела?"}
    window.close()


def test_nothing_heard_keeps_the_field_and_says_so(qapp, tmp_path, monkeypatch):
    _fake_model(monkeypatch, "")
    window = _window(qapp, tmp_path)
    window.toggle_voice_input()
    _wait(qapp, lambda: window.voice_worker is None)
    assert window.prompt.text() == ""
    assert "Не расслышал" in window.prompt.placeholderText()
    window.close()


def test_recognition_error_shown_as_banner(qapp, tmp_path, monkeypatch):
    _fake_model(monkeypatch)

    def broken(self):
        raise voice.stt.SpeechError("Не удалось включить микрофон: нет устройства")

    monkeypatch.setattr(voice.stt.Recorder, "record", broken)
    window = _window(qapp, tmp_path)
    window.toggle_voice_input()
    _wait(qapp, lambda: window.voice_worker is None)
    assert "нет устройства" in window.messages.toPlainText()
    window.close()


def test_close_while_recording(qapp, tmp_path, monkeypatch):
    _fake_model(monkeypatch)

    def until_stopped(self):
        self._stop.wait(5)
        return np.zeros(10, dtype=np.float32)

    monkeypatch.setattr(voice.stt.Recorder, "record", until_stopped)
    window = _window(qapp, tmp_path)
    window.toggle_voice_input()
    window.close()  # stops the recording and waits for the thread
    assert window.voice_worker is None or window.voice_worker.isFinished()


def test_answers_read_aloud_when_switched_on(qapp, tmp_path):
    window = _window(qapp, tmp_path, assistant=FakeAssistant())
    window.speak_toggle.setChecked(True)
    assert window.voice_speak_answers.isChecked()  # the settings switch follows
    window.prompt.setText("Вопрос")
    window.send_message()
    _wait(qapp, lambda: not window.generating)

    speaker = FakeSpeaker.instances[-1]
    assert speaker.spoken == ["Ответ ```py\nx = 1\n```"]
    assert "Остановить озвучку" in window.messages.toPlainText()
    window.stop_generation()  # "Стоп" also cuts the voice off
    assert not speaker.talking
    assert "Остановить озвучку" not in window.messages.toPlainText()
    window.close()


def test_answers_silent_by_default_and_speak_link_reads_one(qapp, tmp_path):
    window = _window(qapp, tmp_path, assistant=FakeAssistant())
    window.prompt.setText("Вопрос")
    window.send_message()
    _wait(qapp, lambda: not window.generating)
    assert not FakeSpeaker.instances  # nothing read aloud
    assert window.messages.toPlainText().count("Озвучить") == 1  # under the answer, not the question

    index = len(window._messages) - 1
    window._on_anchor(f"{SPEAK_URL}{index}")
    assert FakeSpeaker.instances[-1].spoken
    window._on_anchor(f"{SPEAK_URL}{index}")  # the same link again stops it
    assert not FakeSpeaker.instances[-1].talking
    window.close()


def test_speech_end_restores_the_link(qapp, tmp_path):
    window = _window(qapp, tmp_path, assistant=FakeAssistant())
    window.prompt.setText("Вопрос")
    window.send_message()
    _wait(qapp, lambda: not window.generating)
    window._on_anchor(f"{SPEAK_URL}{len(window._messages) - 1}")
    FakeSpeaker.instances[-1].talking = False  # the voice finished by itself
    window._on_speech_tick()
    assert "Озвучить" in window.messages.toPlainText()
    assert "Остановить озвучку" not in window.messages.toPlainText()
    window.close()


def test_speech_models_card_download_and_choice(qapp, tmp_path, monkeypatch):
    installed = {"whisper-base"}
    monkeypatch.setattr(voice.models, "is_installed", lambda model_id, models_dir=None: model_id in installed)

    def fake_download(model_id, on_progress=None, models_dir=None, timeout=60.0):
        on_progress(5, 10)
        installed.add(model_id)
        return tmp_path / model_id

    monkeypatch.setattr(voice.models, "download", fake_download)
    window = _window(qapp, tmp_path)
    buttons = window._speech_buttons
    assert buttons["whisper-small"].text() == "Скачать"
    assert buttons["whisper-base"].text() == "Использовать"

    window._on_speech_model_button("whisper-small")
    _wait(qapp, lambda: window._speech_download_worker is None)
    assert buttons["whisper-small"].text() == "✓ Используется"
    assert window.qsettings.value("voice_model") == "whisper-small"

    window._on_speech_model_button("whisper-base")
    assert buttons["whisper-base"].text() == "✓ Используется"
    assert buttons["whisper-small"].text() == "Использовать"
    window.close()
