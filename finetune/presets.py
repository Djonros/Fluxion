"""Training presets as files (downloadable from the Fluxion website).

A preset is a small JSON document with QLoRA hyperparameters. Presets come
from the internet, so loading is strict: only known fields, value ranges are
checked, and the base model must be one of the supported models — the
trainers load it with ``trust_remote_code=True``, so an arbitrary Hugging
Face repository would be able to run code on the user's machine.

Format (``"format": "fluxion-training-preset"``, ``"version": 1``)::

    {
      "format": "fluxion-training-preset", "version": 1,
      "id": "economy", "name": "Экономный", "description": "...",
      "trainer": "low",            # "low" = unsloth, "standard" = HF peft+trl
      "min_vram_gb": 6,
      "base_model": "Qwen/Qwen2.5-Coder-7B-Instruct",   # optional
      "max_samples": 200,                                # optional
      "settings": {"lora_r": 32, "lora_alpha": 64, ...}
    }
"""
from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PRESET_FORMAT = "fluxion-training-preset"
PRESET_VERSION = 1
MAX_PRESET_BYTES = 64 * 1024

TRAINERS = ("low", "standard")

# Base models the pipeline (ChatML template, merge, GGUF export) supports.
SUPPORTED_BASE_MODELS = (
    "Qwen/Qwen2.5-Coder-1.5B-Instruct",
    "Qwen/Qwen2.5-Coder-3B-Instruct",
    "Qwen/Qwen2.5-Coder-7B-Instruct",
)

KNOWN_TARGET_MODULES = (
    "q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj",
)

# field: (type, minimum, maximum)
_NUMERIC_FIELDS: dict[str, tuple[type, float, float]] = {
    "lora_r": (int, 4, 256),
    "lora_alpha": (int, 4, 512),
    "lora_dropout": (float, 0.0, 0.5),
    "max_seq_length": (int, 256, 8192),
    "num_train_epochs": (int, 1, 20),
    "per_device_train_batch_size": (int, 1, 16),
    "gradient_accumulation_steps": (int, 1, 128),
    "learning_rate": (float, 1e-6, 1e-2),
    "warmup_ratio": (float, 0.0, 0.5),
    "weight_decay": (float, 0.0, 0.5),
}
_CHOICE_FIELDS = {"lr_scheduler_type": ("cosine", "linear", "constant")}
_SETTINGS_FIELDS = set(_NUMERIC_FIELDS) | set(_CHOICE_FIELDS) | {"target_modules"}
_TOP_FIELDS = {
    "format", "version", "id", "name", "description", "trainer",
    "min_vram_gb", "base_model", "max_samples", "settings",
}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


class PresetError(ValueError):
    """The file is not a valid training preset (message is user-facing)."""


@dataclass
class TrainingPreset:
    id: str
    name: str
    trainer: str
    description: str = ""
    min_vram_gb: float = 0.0
    base_model: str | None = None
    max_samples: int | None = None
    settings: dict[str, Any] = field(default_factory=dict)

    # ── loading ──────────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: Any) -> "TrainingPreset":
        if not isinstance(data, dict):
            raise PresetError("Файл пресета должен содержать JSON-объект.")
        if data.get("format") != PRESET_FORMAT:
            raise PresetError("Это не пресет обучения Fluxion (нет поля format).")
        if data.get("version") != PRESET_VERSION:
            raise PresetError(
                f"Версия пресета {data.get('version')!r} не поддерживается — "
                "обновите Fluxion."
            )
        unknown = set(data) - _TOP_FIELDS
        if unknown:
            raise PresetError(f"Неизвестные поля пресета: {', '.join(sorted(unknown))}.")

        preset_id = data.get("id")
        if not isinstance(preset_id, str) or not _ID_RE.match(preset_id):
            raise PresetError("Поле id: латиница в нижнем регистре, цифры и дефис.")
        name = data.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise PresetError("Поле name: непустая строка до 80 символов.")
        description = data.get("description", "")
        if not isinstance(description, str) or len(description) > 500:
            raise PresetError("Поле description: строка до 500 символов.")
        trainer = data.get("trainer")
        if trainer not in TRAINERS:
            raise PresetError("Поле trainer: \"low\" (unsloth) или \"standard\" (HF).")
        min_vram = data.get("min_vram_gb", 0)
        if isinstance(min_vram, bool) or not isinstance(min_vram, (int, float)) or not 0 <= min_vram <= 96:
            raise PresetError("Поле min_vram_gb: число от 0 до 96.")
        base_model = data.get("base_model")
        if base_model is not None and base_model not in SUPPORTED_BASE_MODELS:
            raise PresetError(
                "Базовая модель не поддерживается: "
                f"{base_model!r}. Допустимы: {', '.join(SUPPORTED_BASE_MODELS)}."
            )
        max_samples = data.get("max_samples")
        if max_samples is not None and (
            isinstance(max_samples, bool) or not isinstance(max_samples, int)
            or not 1 <= max_samples <= 10_000_000
        ):
            raise PresetError("Поле max_samples: целое число от 1.")

        settings = data.get("settings", {})
        if not isinstance(settings, dict):
            raise PresetError("Поле settings должно быть объектом.")
        return cls(
            id=preset_id,
            name=name.strip(),
            trainer=trainer,
            description=description.strip(),
            min_vram_gb=float(min_vram),
            base_model=base_model,
            max_samples=max_samples,
            settings=_validate_settings(settings),
        )

    @classmethod
    def load(cls, path: str | Path) -> "TrainingPreset":
        path = Path(path)
        try:
            if path.stat().st_size > MAX_PRESET_BYTES:
                raise PresetError("Файл слишком большой для пресета.")
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            raise PresetError(f"Файл не найден: {path}") from None
        except json.JSONDecodeError as exc:
            raise PresetError(f"Файл не является корректным JSON: {exc.msg}.") from None
        except OSError as exc:
            raise PresetError(f"Не удалось прочитать файл: {exc}") from None
        return cls.from_dict(data)

    # ── use ──────────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "format": PRESET_FORMAT,
            "version": PRESET_VERSION,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "trainer": self.trainer,
            "min_vram_gb": self.min_vram_gb,
        }
        if self.base_model:
            data["base_model"] = self.base_model
        if self.max_samples:
            data["max_samples"] = self.max_samples
        data["settings"] = dict(self.settings)
        return data

    def apply(self, settings):
        """Return a copy of QLoRASettings with this preset's values."""
        changes = dict(self.settings)
        if "target_modules" in changes:
            changes["target_modules"] = list(changes["target_modules"])
        if self.base_model:
            changes["base_model"] = self.base_model
        return dataclasses.replace(settings, **changes)


def _validate_settings(settings: dict[str, Any]) -> dict[str, Any]:
    unknown = set(settings) - _SETTINGS_FIELDS
    if unknown:
        raise PresetError(f"Неизвестные параметры обучения: {', '.join(sorted(unknown))}.")
    clean: dict[str, Any] = {}
    for key, value in settings.items():
        if key in _NUMERIC_FIELDS:
            kind, low, high = _NUMERIC_FIELDS[key]
            ok_type = (
                isinstance(value, int) and not isinstance(value, bool)
                if kind is int
                else isinstance(value, (int, float)) and not isinstance(value, bool)
            )
            if not ok_type or not low <= value <= high:
                raise PresetError(f"Параметр {key}: ожидается {kind.__name__} от {low} до {high}.")
            clean[key] = kind(value)
        elif key in _CHOICE_FIELDS:
            if value not in _CHOICE_FIELDS[key]:
                raise PresetError(f"Параметр {key}: одно из {', '.join(_CHOICE_FIELDS[key])}.")
            clean[key] = value
        else:  # target_modules
            if (
                not isinstance(value, list) or not value
                or any(m not in KNOWN_TARGET_MODULES for m in value)
                or len(set(value)) != len(value)
            ):
                raise PresetError(
                    "Параметр target_modules: непустой список без повторов из "
                    f"{', '.join(KNOWN_TARGET_MODULES)}."
                )
            clean[key] = list(value)
    return clean


def load_preset(path: str | Path) -> TrainingPreset:
    return TrainingPreset.load(path)
