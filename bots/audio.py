"""A voice message as 16 kHz mono for the speech model.

Messengers send OGG/Opus (VK also MP3). The speech model of Fluxion needs PCM: it is
decoded by ffmpeg when it is on PATH, else by PyAV when that is installed; a WAV is
read as it is. Without both a voice message is answered «напишите текстом».
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np

RATE = 16000


class AudioError(RuntimeError):
    pass


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
    for reader in (_ffmpeg, _pyav):
        audio = reader(path)
        if audio is not None:
            return audio
    raise AudioError("голосовые сообщения не прочитать: на компьютере нет ffmpeg")
