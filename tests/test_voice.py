"""Voice input and output (package voice) without a microphone or a real model."""
from __future__ import annotations

import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest

from voice import models, stt
from voice.tts import CODE_SKIPPED, speech_text


def _tone(seconds: float, amplitude: float = 0.3, rate: int = stt.SAMPLE_RATE) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * stt.SAMPLE_RATE), dtype=np.float32)


def _write_wav(path: Path, audio: np.ndarray, rate: int, channels: int = 1) -> Path:
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    if channels == 2:
        pcm = np.repeat(pcm, 2)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())
    return path


# ── speech text ──────────────────────────────────────────────────────────


def test_speech_text_skips_code_and_markdown():
    md = (
        "## Ответ\n\nВот **пример** и *курсив*:\n\n```python\nprint(1)\n```\n\n"
        "- первый пункт\n- второй [ссылка](http://x.y)\n\nФункция `foo` готова"
    )
    text = speech_text(md)
    assert "print" not in text and "```" not in text and "#" not in text and "*" not in text
    assert CODE_SKIPPED in text
    assert "первый пункт" in text and "ссылка" in text and "foo" in text
    assert "http" not in text


def test_speech_text_says_code_skipped_once_for_adjacent_blocks():
    text = speech_text("```\na\n```\n```\nb\n```")
    assert text.count(CODE_SKIPPED) == 1


def test_speech_text_unclosed_fence_while_streaming():
    assert "secret()" not in speech_text("Смотрите:\n```py\nsecret()")


def test_speech_text_empty():
    assert speech_text("") == ""
    assert speech_text("```\nx\n```") == CODE_SKIPPED


# ── audio helpers ────────────────────────────────────────────────────────


def test_trim_silence_keeps_speech_with_padding():
    audio = np.concatenate([_silence(1.0), _tone(0.5), _silence(1.0)])
    trimmed = stt.trim_silence(audio)
    assert 0.5 * stt.SAMPLE_RATE <= len(trimmed) <= 1.0 * stt.SAMPLE_RATE


def test_trim_silence_of_silence_is_empty():
    assert len(stt.trim_silence(_silence(2.0))) == 0


def test_read_wav_resamples_and_mixes_down(tmp_path):
    path = _write_wav(tmp_path / "a.wav", _tone(1.0, rate=8000), 8000, channels=2)
    audio = stt.read_wav(path)
    assert audio.dtype == np.float32
    assert abs(len(audio) - stt.SAMPLE_RATE) <= 2
    assert 0.2 < np.abs(audio).max() <= 0.31


# ── recognition with a fake model ────────────────────────────────────────


class _FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((len(audio), kwargs))
        return iter([types.SimpleNamespace(text=" Привет,"), types.SimpleNamespace(text=" мир. ")]), None


def test_transcribe_joins_segments(monkeypatch):
    fake = _FakeModel()
    monkeypatch.setattr(stt, "load_model", lambda folder: fake)
    assert stt.transcribe(np.concatenate([_silence(1), _tone(1), _silence(1)]), "m") == "Привет, мир."
    length, kwargs = fake.calls[0]
    assert length < 2 * stt.SAMPLE_RATE  # silent edges cut before Whisper
    assert kwargs["language"] == "ru" and kwargs["vad_filter"] is False


def test_transcribe_silence_does_not_load_model(monkeypatch):
    def boom(folder):
        raise AssertionError("model loaded for silence")

    monkeypatch.setattr(stt, "load_model", boom)
    assert stt.transcribe(_silence(3), "m") == ""


def test_import_whisper_without_pyav(monkeypatch):
    """The build leaves PyAV and huggingface_hub out: faster_whisper must still import."""
    pytest.importorskip("faster_whisper")
    import builtins

    real_import = builtins.__import__

    def no_av(name, *args, **kwargs):
        if name in ("av", "huggingface_hub") and not isinstance(sys.modules.get(name), types.ModuleType):
            raise ImportError(f"no {name}")
        return real_import(name, *args, **kwargs)

    stubbed = ("av", "huggingface_hub")
    for name in [n for n in sys.modules if n in stubbed or n.startswith(("av.", "huggingface_hub.", "faster_whisper"))]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(builtins, "__import__", no_av)
    try:
        assert stt._import_whisper().__name__ == "WhisperModel"
        assert "av" not in sys.modules and "huggingface_hub" not in sys.modules  # stand-ins removed
    finally:
        for name in ("av", "huggingface_hub"):
            sys.modules.pop(name, None)  # monkeypatch puts the real ones back, if they were loaded


# ── microphone with a fake sounddevice ───────────────────────────────────


class _FakeStream:
    def __init__(self, blocks):
        self.blocks = list(blocks)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, frames):
        block = self.blocks.pop(0) if self.blocks else np.zeros(frames, dtype=np.float32)
        return block.reshape(-1, 1), False


def _fake_sounddevice(monkeypatch, blocks):
    module = types.ModuleType("sounddevice")
    module.InputStream = lambda **kwargs: _FakeStream(blocks)
    monkeypatch.setitem(sys.modules, "sounddevice", module)


def _blocks(audio):
    return [audio[i : i + stt.BLOCK] for i in range(0, len(audio), stt.BLOCK)]


def test_recorder_stops_after_pause_in_speech(monkeypatch):
    _fake_sounddevice(monkeypatch, _blocks(np.concatenate([_silence(0.5), _tone(1.0), _silence(5.0)])))
    levels = []
    audio = stt.Recorder(pause_seconds=1.0, on_level=levels.append).record()
    seconds = len(audio) / stt.SAMPLE_RATE
    assert 2.0 <= seconds <= 3.0  # 0.5 quiet + 1 speech + ~1 pause, not the 5 s of silence
    assert max(levels) > stt.SILENCE_LEVEL


def test_recorder_waits_for_speech_then_max_seconds(monkeypatch):
    _fake_sounddevice(monkeypatch, [])  # endless silence
    audio = stt.Recorder(pause_seconds=0.5, max_seconds=0.0).record()
    assert len(audio) == stt.BLOCK  # one block, then the time limit


def test_recorder_gives_up_when_nobody_speaks(monkeypatch):
    _fake_sounddevice(monkeypatch, [])  # endless silence
    audio = stt.Recorder(wait_seconds=2.0).record()
    assert len(audio) == 2 * stt.SAMPLE_RATE


def test_recorder_wait_does_not_cut_speech(monkeypatch):
    _fake_sounddevice(monkeypatch, _blocks(np.concatenate([_silence(0.5), _tone(3.0), _silence(3.0)])))
    audio = stt.Recorder(pause_seconds=1.0, wait_seconds=1.0).record()
    assert len(audio) / stt.SAMPLE_RATE >= 3.5  # speech began before the wait ran out


def test_recorder_stop_before_start(monkeypatch):
    _fake_sounddevice(monkeypatch, [])
    recorder = stt.Recorder()
    recorder.stop()
    assert len(recorder.record()) == 0


def test_recorder_reports_missing_microphone(monkeypatch):
    module = types.ModuleType("sounddevice")

    def no_device(**kwargs):
        raise OSError("No Default Input Device Available")

    module.InputStream = no_device
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    with pytest.raises(stt.SpeechError, match="микрофон"):
        stt.Recorder().record()


# ── models ───────────────────────────────────────────────────────────────


def test_default_model_is_in_catalog():
    assert models.speech_model(models.DEFAULT_SPEECH_MODEL) is not None
    assert all(m.repo_id.startswith("Systran/faster-whisper-") for m in models.SPEECH_MODELS)


def test_download_fetches_missing_files_only(tmp_path, monkeypatch):
    from core.model_manager import ModelManager

    fetched = []

    def fake_fetch(self, url, part, entry, on_progress, timeout):
        fetched.append(url.rsplit("/", 1)[1])
        part.write_bytes(b"x" * 10)
        if on_progress is not None:
            on_progress(10, 10)

    monkeypatch.setattr(ModelManager, "_fetch", fake_fetch)
    folder = models.model_path("whisper-base", tmp_path)
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{}", encoding="utf-8")
    progress = []
    path = models.download("whisper-base", on_progress=lambda d, t: progress.append((d, t)), models_dir=tmp_path)

    assert path == folder
    assert fetched == ["tokenizer.json", "vocabulary.txt", "model.bin"]
    assert models.is_installed("whisper-base", tmp_path)
    assert [m.model_id for m in models.installed_models(tmp_path)] == ["whisper-base"]
    assert progress and progress[-1][0] == progress[-1][1]
    assert not list(folder.glob("*.part"))


def test_download_unknown_model(tmp_path):
    from core.model_manager import ModelDownloadError

    with pytest.raises(ModelDownloadError):
        models.download("whisper-huge", models_dir=tmp_path)


# ── live check on a Windows machine with a downloaded model ──────────────


@pytest.mark.skipif(sys.platform != "win32", reason="SAPI voices are Windows only")
def test_live_sapi_voice_to_whisper(tmp_path):
    """Both directions at once: a Windows voice says a phrase into a WAV, Whisper hears it."""
    import os

    real_models = Path(os.environ.get("LOCALAPPDATA", "")) / "Fluxion" / "models"  # conftest isolates the default
    installed = models.installed_models(real_models)
    if not installed:
        pytest.skip("no speech model downloaded")
    pytest.importorskip("faster_whisper")
    win32 = pytest.importorskip("win32com.client")
    voice = win32.Dispatch("SAPI.SpVoice")
    russian = [t for t in voice.GetVoices() if t.GetAttribute("Language").split(";")[0] == "419"]
    if not russian:
        pytest.skip("no Russian SAPI voice")
    voice.Voice = russian[0]
    fmt = win32.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 18  # 16 kHz 16 bit mono
    stream = win32.Dispatch("SAPI.SpFileStream")
    stream.Format = fmt
    path = tmp_path / "phrase.wav"
    stream.Open(str(path), 3, False)
    voice.AudioOutputStream = stream
    voice.Speak("Построй втулку из бронзы.")
    stream.Close()

    text = stt.transcribe(stt.read_wav(path), models.model_path(installed[0].model_id, real_models)).lower()
    assert "втулк" in text and "бронз" in text


def test_speech_text_of_a_part_reply():
    """Paths are not read out; sizes are read as a person would say them."""
    reply = (
        r"Готово: деталь «Уголок гнутый» сохранена в C:\Users\PC\Documents\Fluxion CAD\Уголок (2).m3d" "\n"
        "Масса: 152 г.\n"
        "Развёртка (КОМПАС): 100.00 × 96.15 мм, толщина 2 мм.\n"
        r"DXF развёртки: C:\Users\PC\Documents\Fluxion CAD\Уголок (2) — развёртка.dxf"
    )
    text = speech_text(reply)
    assert "C:" not in text and "Users" not in text and "DXF" not in text
    assert "сохранена." in text and "100 на 96,15 мм" in text
    assert speech_text("box 5 и 30х20") == "box 5 и 30 на 20"
