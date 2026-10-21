"""Speech recognition: microphone recording and faster-whisper transcription.

Audio is 16 kHz mono float32 throughout, the input of Whisper. The microphone
is read with ``sounddevice`` (PortAudio, a 2 MB library); Qt multimedia would
bring FFmpeg into the build. Silence at the edges is trimmed here instead of
faster-whisper's VAD filter, which needs onnxruntime (45 MB).
"""
from __future__ import annotations

import sys
import threading
import types
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
BLOCK = 1600  # 0.1 s
SILENCE_LEVEL = 0.012  # RMS below this is a pause
MAX_SECONDS = 60.0


class SpeechError(RuntimeError):
    """Recording or recognition failed; the message is shown to the user as is."""


# ── audio helpers ───────────────────────────────────────────────────────


def read_wav(path: Path | str) -> np.ndarray:
    """Read a PCM WAV into 16 kHz mono float32 (resampled linearly if needed)."""
    with wave.open(str(path), "rb") as wav:
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    if width != 2:
        raise SpeechError("Нужен WAV с 16-битным звуком.")
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE and len(audio):
        positions = np.linspace(0, len(audio) - 1, int(len(audio) * SAMPLE_RATE / rate))
        audio = np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)
    return audio


def rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(block)))) if len(block) else 0.0


def trim_silence(audio: np.ndarray, level: float = SILENCE_LEVEL, pad: float = 0.2) -> np.ndarray:
    """Cut quiet edges, keeping *pad* seconds around the speech; empty if all is quiet."""
    if not len(audio):
        return audio
    blocks = [rms(audio[i : i + BLOCK]) for i in range(0, len(audio), BLOCK)]
    loud = [i for i, value in enumerate(blocks) if value >= level]
    if not loud:
        return audio[:0]
    margin = int(pad * SAMPLE_RATE)
    start = max(0, loud[0] * BLOCK - margin)
    end = min(len(audio), (loud[-1] + 1) * BLOCK + margin)
    return audio[start:end]


# ── recognition ──────────────────────────────────────────────────────────

_model_cache: dict[tuple[str, str], object] = {}
_model_lock = threading.Lock()


def _import_whisper():
    """faster_whisper imports PyAV for decoding files and huggingface_hub for its
    own downloads; the build leaves both out (PyAV is 65 MB of FFmpeg) because
    Fluxion passes the samples and downloads the models itself (voice/models.py)."""
    stubs = []
    for name in ("av", "huggingface_hub"):
        try:
            __import__(name)
        except ImportError:
            stubs.append(name)
            sys.modules[name] = types.ModuleType(name)
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SpeechError(f"Нет пакета faster-whisper ({exc}): pip install faster-whisper") from exc
    finally:
        for name in stubs:  # other code must still see these packages as missing
            sys.modules.pop(name, None)
    return WhisperModel


def load_model(folder: Path | str, device: str = "auto"):
    key = (str(folder), device)
    with _model_lock:
        model = _model_cache.get(key)
        if model is None:
            WhisperModel = _import_whisper()
            try:
                model = WhisperModel(str(folder), device=device, compute_type="int8")
            except Exception as exc:
                if device != "cpu":
                    return load_model(folder, "cpu")  # no usable CUDA libraries
                raise SpeechError(f"Не удалось загрузить модель распознавания: {exc}") from exc
            _model_cache.clear()  # one model in memory at a time
            _model_cache[key] = model
        return model


def transcribe(audio: np.ndarray, folder: Path | str, language: str | None = "ru") -> str:
    """Text of the speech in *audio* (16 kHz mono float32); "" when nothing was said."""
    audio = trim_silence(np.asarray(audio, dtype=np.float32))
    if len(audio) < SAMPLE_RATE // 4:
        return ""
    model = load_model(folder)
    segments, _info = model.transcribe(
        audio, language=language or None, beam_size=5, vad_filter=False, condition_on_previous_text=False
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


# ── microphone ───────────────────────────────────────────────────────────


class Recorder:
    """Records the default microphone until stop(), a pause after speech or MAX_SECONDS."""

    def __init__(self, pause_seconds: float = 1.5, max_seconds: float = MAX_SECONDS, on_level=None):
        self.pause_seconds = pause_seconds
        self.max_seconds = max_seconds
        self.on_level = on_level
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def record(self) -> np.ndarray:
        try:
            import sounddevice as sd
        except Exception as exc:  # ImportError or a missing PortAudio library
            raise SpeechError(f"Запись с микрофона недоступна: {exc}") from exc
        chunks: list[np.ndarray] = []
        heard = False
        quiet = recorded = 0  # samples: time is counted by the audio, not the clock
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=BLOCK) as stream:
                while not self._stop.is_set():
                    block, _overflow = stream.read(BLOCK)
                    block = block[:, 0].copy()
                    chunks.append(block)
                    recorded += len(block)
                    level = rms(block)
                    if self.on_level is not None:
                        self.on_level(level)
                    if level >= SILENCE_LEVEL:
                        heard, quiet = True, 0
                    elif heard:
                        quiet += len(block)
                        if quiet >= self.pause_seconds * SAMPLE_RATE:
                            break
                    if recorded >= self.max_seconds * SAMPLE_RATE:
                        break
        except Exception as exc:
            raise SpeechError(
                f"Не удалось включить микрофон: {exc}. Проверьте, что он подключён и "
                "разрешён в «Параметры Windows» → «Конфиденциальность» → «Микрофон»."
            ) from exc
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
