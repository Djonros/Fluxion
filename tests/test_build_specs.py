"""PyInstaller spec files are Python: check them without running a build.

A spec error otherwise surfaces only in the middle of a 10-minute build
(e.g. a comma swallowed by a comment turned two tuples into a call).
"""
from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPECS = sorted(ROOT.glob("*.spec"))


def _call(tree: ast.AST, name: str) -> ast.Call:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == name)


def _kw(call: ast.Call, name: str):
    return next((k.value for k in call.keywords if k.arg == name), None)


def test_specs_exist():
    assert SPECS, "no *.spec files found"


@pytest.mark.parametrize("spec", SPECS, ids=[s.name for s in SPECS])
def test_spec_compiles_without_warnings(spec):
    source = spec.read_text(encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # SyntaxWarning: 'tuple' object is not callable
        compile(source, str(spec), "exec")


@pytest.mark.parametrize("spec", SPECS, ids=[s.name for s in SPECS])
def test_spec_references_existing_files(spec):
    tree = ast.parse(spec.read_text(encoding="utf-8"))
    analysis = _call(tree, "Analysis")

    scripts = ast.literal_eval(analysis.args[0]) if analysis.args else ast.literal_eval(_kw(analysis, "scripts"))
    for script in scripts:
        assert (ROOT / script).is_file(), f"entry script missing: {script}"

    datas = _kw(analysis, "datas")
    if isinstance(datas, ast.List):
        for element in datas.elts:
            src, _dest = ast.literal_eval(element)
            assert (ROOT / src).exists(), f"datas source missing: {src}"

    icon = _kw(_call(tree, "EXE"), "icon")
    if icon is not None:
        assert (ROOT / ast.literal_eval(icon)).is_file(), "exe icon missing"


def test_window_icon_is_bundled():
    """The app loads assets/icon-256.png at runtime; the build must ship it."""
    app = (ROOT / "desktop_browser" / "app.py").read_text(encoding="utf-8")
    assert '"icon-256.png"' in app
    lite = (ROOT / "fluxion-desktop-browser-lite.spec").read_text(encoding="utf-8")
    assert '"assets/icon-256.png"' in lite


def _spec_list(name: str) -> list[str]:
    tree = ast.parse((ROOT / "fluxion-desktop-browser-lite.spec").read_text(encoding="utf-8"))
    return [ast.literal_eval(e) for e in _kw(_call(tree, "Analysis"), name).elts]


def test_lite_ships_lazily_imported_voice_modules():
    """The window imports voice/* only when the user presses 🎤 or 🔊, so
    PyInstaller cannot see them: every module must be listed."""
    hidden = set(_spec_list("hiddenimports"))
    modules = {"voice"} | {f"voice.{p.stem}" for p in (ROOT / "voice").glob("*.py") if p.stem != "__init__"}
    assert modules <= hidden, sorted(modules - hidden)
    assert {"faster_whisper", "ctranslate2", "sounddevice"} <= hidden


def test_lite_leaves_out_pyav_and_onnxruntime():
    """voice/stt.py works without them (no file decoding, no VAD filter)."""
    excludes = set(_spec_list("excludes"))
    assert {"av", "onnxruntime", "huggingface_hub"} <= excludes
    assert "tokenizers" not in excludes  # faster-whisper needs it at import
    stt = (ROOT / "voice" / "stt.py").read_text(encoding="utf-8")
    assert "vad_filter=False" in stt
