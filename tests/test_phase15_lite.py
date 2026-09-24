"""Phase 15: Lite build artifacts (spec + packaging script) stay consistent."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LITE_SPEC = ROOT / "fluxion-desktop-browser-lite.spec"
BUILD_PS1 = ROOT / "scripts" / "build_lite.ps1"


def _spec_text() -> str:
    assert LITE_SPEC.is_file(), "fluxion-desktop-browser-lite.spec missing"
    return LITE_SPEC.read_text(encoding="utf-8")


def test_lite_spec_excludes_training_stack():
    """torch/transformers/datasets must never ship inside the Lite exe."""
    spec = _spec_text()
    for module in (
        "torch",
        "transformers",
        "datasets",
        "unsloth",
        "pyarrow",
        "pandas",
        "scipy",
        "sklearn",
        "psycopg2",
        "tiktoken",
    ):
        assert f'"{module}"' in spec, f"Lite spec must exclude {module}"


def test_lite_spec_keeps_chromadb_otel_grpc_chain():
    """chromadb 1.5.9 client path imports the OTLP grpc exporter
    unconditionally (chromadb/telemetry/opentelemetry/__init__.py);
    excluding it breaks RAG at runtime in the frozen Lite exe."""
    spec = _spec_text()
    for module in (
        "grpc",
        "opentelemetry.exporter.otlp.proto.grpc",
        "opentelemetry_exporter_otlp_proto_grpc",
    ):
        assert f'"{module}"' not in spec, (
            f"Lite spec must NOT exclude {module}: "
            "chromadb imports the OTLP grpc exporter at import time"
        )


def test_lite_spec_keeps_core_features():
    """Lite still bundles browser, RAG, and the training UI orchestrator."""
    spec = _spec_text()
    for module in (
        "desktop_browser.training",
        "PySide6.QtWebEngineWidgets",
        "chromadb_rust_bindings",
        "finetune.marketplace",
    ):
        assert f'"{module}"' in spec, f"Lite spec must keep {module}"
    # train entry points run inside data/training_env only: bundling them
    # would drag transformers in via static analysis.
    for heavy in ("finetune.train_hf", "finetune.train_unsloth"):
        assert f'"{heavy}"' not in spec, f"{heavy} must not be bundled in Lite"


def test_lite_spec_outputs_separate_app_name():
    spec = _spec_text()
    assert '"FluxionBrowserLite"' in spec
    assert '"FluxionBrowser"' not in spec.replace('"FluxionBrowserLite"', "")


def test_build_script_trims_and_checks_limit():
    """build_lite.ps1 strips debug paks and asserts the <200 MB installer."""
    assert BUILD_PS1.is_file(), "scripts/build_lite.ps1 missing"
    script = BUILD_PS1.read_text(encoding="utf-8")
    assert "*.debug.pak" in script
    assert "qtwebengine_devtools_resources.pak" in script
    assert "-mx=9" in script
    assert "200" in script


def test_build_full_script_packs_offline_wheels():
    """build_full.ps1 ships the CUDA wheel pack next to the Lite app."""
    script = ROOT / "scripts" / "build_full.ps1"
    assert script.is_file(), "scripts/build_full.ps1 missing"
    text = script.read_text(encoding="utf-8")
    assert "training_pack" in text
    assert "wheels" in text
    assert "--python-version 3.12" in text
    assert "cu126" in text
    assert "-c $constraints" in text
    assert "\\+cu126" in text
    assert "FluxionBrowser-Full-win64-" in text
    for package in ("torch", "unsloth", "bitsandbytes", "gguf"):
        assert package in text


def test_torch_pin_consistent_between_app_and_full_build():
    """The app and build_full.ps1 must install/download the same CUDA torch."""
    import re

    training = (ROOT / "desktop_browser" / "training.py").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "build_full.ps1").read_text(encoding="utf-8")
    pin = re.search(r'TORCH_PIN = "([^"]+)"', training)
    index = re.search(r'TORCH_INDEX_URL = "([^"]+)"', training)
    assert pin, "TORCH_PIN missing in desktop_browser/training.py"
    assert index, "TORCH_INDEX_URL missing in desktop_browser/training.py"
    assert pin.group(1) in script, "build_full.ps1 torch pin diverged from the app"
    assert index.group(1) in script, "build_full.ps1 torch index diverged from the app"


@pytest.mark.parametrize(
    "module",
    ["desktop_browser.training", "finetune.marketplace", "finetune.qlora_config"],
)
def test_training_ui_modules_import_without_ml_stack(module):
    """The training UI must import cleanly even with no torch installed."""
    import importlib

    importlib.import_module(module)
