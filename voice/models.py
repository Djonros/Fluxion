"""Speech recognition models: faster-whisper (CTranslate2) folders from Hugging Face.

A Whisper model is a folder of a few files, not one GGUF, so it lives apart
from the GGUF catalog: %LOCALAPPDATA%\\Fluxion\\models\\speech\\<id>. The files are
downloaded one by one with the resume logic of core.model_manager.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

MODEL_FILES = ("config.json", "tokenizer.json", "vocabulary.txt", "model.bin")
DEFAULT_SPEECH_MODEL = "whisper-small"


@dataclass(frozen=True)
class SpeechModel:
    model_id: str
    name: str
    repo_id: str
    size_bytes: int
    summary: str


SPEECH_MODELS: tuple[SpeechModel, ...] = (
    SpeechModel(
        "whisper-base", "Whisper Base", "Systran/faster-whisper-base", 145_000_000,
        "Быстрая, для слабых компьютеров. Русский понимает хуже",
    ),
    SpeechModel(
        "whisper-small", "Whisper Small", "Systran/faster-whisper-small", 484_000_000,
        "Рекомендуется: хороший русский на обычном процессоре",
    ),
    SpeechModel(
        "whisper-medium", "Whisper Medium", "Systran/faster-whisper-medium", 1_530_000_000,
        "Точнее, но медленная: нужен мощный компьютер или видеокарта",
    ),
)


def speech_model(model_id: str) -> SpeechModel | None:
    return next((m for m in SPEECH_MODELS if m.model_id == model_id), None)


def speech_models_dir(models_dir: Path | str | None = None) -> Path:
    if models_dir is None:
        from core.model_manager import _default_models_dir

        models_dir = _default_models_dir()
    return Path(models_dir) / "speech"


def model_path(model_id: str, models_dir: Path | str | None = None) -> Path:
    return speech_models_dir(models_dir) / model_id


def is_installed(model_id: str, models_dir: Path | str | None = None) -> bool:
    folder = model_path(model_id, models_dir)
    return all((folder / name).is_file() for name in MODEL_FILES)


def installed_models(models_dir: Path | str | None = None) -> list[SpeechModel]:
    return [m for m in SPEECH_MODELS if is_installed(m.model_id, models_dir)]


def download(model_id: str, on_progress=None, models_dir: Path | str | None = None, timeout: float = 60.0) -> Path:
    """Download the files of a speech model; finished files are not fetched again."""
    from core.model_manager import ModelDownloadError, ModelManager, _is_http_error, hf_download_url

    model = speech_model(model_id)
    if model is None:
        raise ModelDownloadError(f"Неизвестная модель распознавания речи: {model_id}")
    folder = model_path(model_id, models_dir)
    folder.mkdir(parents=True, exist_ok=True)
    mm = ModelManager(models_dir) if models_dir is not None else ModelManager()
    # model.bin is almost all of the size: report its bytes against the whole model
    small_files = 4_000_000
    for name in MODEL_FILES:
        target = folder / name
        if target.is_file():
            continue
        part = target.with_name(name + ".part")
        report = None
        if on_progress is not None and name == "model.bin":
            report = lambda done, total: on_progress(  # noqa: E731
                small_files + done, small_files + (total or model.size_bytes)
            )
        try:
            mm._fetch(hf_download_url(model.repo_id, name), part, SimpleNamespace(filename=name), report, timeout)
        except ModelDownloadError:
            raise
        except Exception as exc:
            if _is_http_error(exc):
                raise ModelDownloadError(
                    "Нет связи с Hugging Face — проверьте интернет и нажмите «Скачать» "
                    "ещё раз: загрузка продолжится с места остановки."
                ) from exc
            raise
        part.replace(target)
    return folder
