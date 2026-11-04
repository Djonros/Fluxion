"""A voice message as 16 kHz mono for the speech model.

Messengers send OGG/Opus (VK also MP3). The speech model of Fluxion needs PCM: it is
decoded by libsndfile (the soundfile package, about 1 MB, shipped with Fluxion: OGG
Opus/Vorbis, MP3, FLAC, WAV) and brought to 16 kHz here; a format it does not know
(an M4A sent as music) goes to ffmpeg when it is on PATH, else to PyAV when that is
installed. So no ffmpeg (about 90 MB) has to be downloaded for voice messages.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np

RATE = 16000


class AudioError(RuntimeError):
    pass


def to_16k(data: np.ndarray, rate: int) -> np.ndarray:
    """Mono float32 at RATE: channels averaged, a low-pass under 8 kHz, then resampled."""
    data = np.asarray(data, dtype=np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if rate == RATE or not len(data):
        return data
    if rate > RATE:  # windowed-sinc low-pass: the speech model hears no aliasing
        cutoff = 0.95 * RATE / 2 / rate
        taps = np.arange(-32, 33)
        kernel = 2 * cutoff * np.sinc(2 * cutoff * taps) * np.hamming(len(taps))
        data = np.convolve(data, (kernel / kernel.sum()).astype(np.float32), mode="same")
    length = int(round(len(data) * RATE / rate))
    return np.interp(np.arange(length) * (rate / RATE), np.arange(len(data)), data).astype(np.float32)


def _soundfile(path: Path) -> np.ndarray | None:
    try:
        import soundfile
    except ImportError:
        return None
    try:
        data, rate = soundfile.read(str(path), dtype="float32", always_2d=False)
    except (RuntimeError, soundfile.LibsndfileError, TypeError):
        return None  # a format libsndfile does not know: ffmpeg or PyAV may
    return to_16k(data, int(rate))


def _ffmpeg(path: Path) -> np.ndarray | None:
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    done = subprocess.run([exe, "-nostdin", "-loglevel", "error", "-i", str(path), "-f", "s16le", "-ac", "1",
                           "-ar", str(RATE), "-"], capture_output=True, timeout=120, creationflags=flags)
    if done.returncode != 0:
        raise AudioError(done.stderr.decode("utf-8", "replace").strip() or "ffmpeg не смог прочитать звук")
    return np.frombuffer(done.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _pyav(path: Path) -> np.ndarray | None:
    try:
        import av
    except ImportError:
        return None
    chunks = []
    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def decode(path: str | Path) -> np.ndarray:
    path = Path(path)
    if path.suffix.lower() == ".wav":
        from voice.stt import read_wav

        return read_wav(path)
    for reader in (_soundfile, _ffmpeg, _pyav):
        audio = reader(path)
        if audio is not None:
            return audio
    raise AudioError("этот звук не прочитать: пришлите голосовое сообщение (OGG или MP3) или напишите текстом")
