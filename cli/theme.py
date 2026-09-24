"""Color themes for the Fluxion CLI (dark/light, logo-inspired palette)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    fg: str
    accent: str
    accent2: str
    dim: str
    success: str
    warn: str
    error: str
    panel_border: str


DARK = Theme(
    name="dark",
    bg="#0a0e17",
    fg="#ffffff",
    accent="#7B2CBF",
    accent2="#00B4D8",
    dim="#6e7681",
    success="#2ECC71",
    warn="#F1C40F",
    error="#E74C3C",
    panel_border="#7B2CBF",
)

LIGHT = Theme(
    name="light",
    bg="#f5f7fa",
    fg="#1a1d23",
    accent="#7B2CBF",
    accent2="#00B4D8",
    dim="#586069",
    success="#1E8449",
    warn="#B7950B",
    error="#C0392B",
    panel_border="#7B2CBF",
)

THEMES: dict[str, Theme] = {"dark": DARK, "light": LIGHT}


def theme_path(settings) -> Path:
    return Path(settings.paths.data_dir) / "ui-theme.json"


def load_theme(settings) -> Theme:
    path = theme_path(settings)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return THEMES.get(data.get("theme", "dark"), DARK)
        except Exception:
            pass
    env_theme = os.environ.get("FLUXION_THEME", "dark")
    return THEMES.get(env_theme, DARK)


def save_theme(settings, theme: Theme) -> None:
    path = theme_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"theme": theme.name}), encoding="utf-8")
