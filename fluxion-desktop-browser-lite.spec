# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Fluxion Browser LITE desktop app.

The app build (chat + agent + RAG + browser + wizard/health) without the
training stack: QLoRA training runs
inside the on-demand `data/training_env` venv (Phase 15), so torch /
transformers / datasets and friends never ship inside the exe.
Post-build trimming (debug paks, translations) + 7z packaging is done by
scripts/build_lite.ps1.
"""
import os
import glob as _glob
import importlib.util as _importlib_util

_llama_spec = _importlib_util.find_spec("llama_cpp")
_llama_lib_dir = (
    os.path.join(list(_llama_spec.submodule_search_locations)[0], "lib")
    if _llama_spec and _llama_spec.submodule_search_locations
    else ""
)
llama_cpp_binaries = (
    [
        (dll, "llama_cpp/lib")
        for dll in sorted(_glob.glob(os.path.join(_llama_lib_dir, "*.dll")))
    ]
    if _llama_lib_dir
    else []
)

block_cipher = None

a = Analysis(
    ["run_desktop_browser.py"],
    pathex=[os.path.abspath(".")],
    binaries=llama_cpp_binaries,
    datas=[
        ("assets/icon-256.png", "assets"),  # runtime needs only the window icon
        ("config", "config"),
        ("finetune", "finetune"),
        ("licensing", "licensing"),
        # plain sources for the training env's own interpreter (PYTHONPATH)
        ("finetune", "pysource/finetune"),
        ("licensing", "pysource/licensing"),
    ],
    hiddenimports=[
        "desktop_browser",
        "desktop_browser.app",
        "desktop_browser.banner",
        "desktop_browser.browser",
        "desktop_browser.engine",
        "desktop_browser.license_dialog",
        "desktop_browser.training",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebChannel",
        "web",
        "web.pipeline",
        "web.searxng",
        "web.fetcher",
        "web.cache",
        "finetune",
        "finetune.qlora_config",
        "finetune.dataset_loader",
        "finetune.marketplace",
        "cli",
        "cli.theme",
        "cli.render",
        "cli.repl",
        "cli.commands",
        "cli.app",
        "core",
        "core.config",
        "core.inference",
        "core.language",
        "core.ollama_client",
        "core.backend_factory",
        "core.api_backend",
        "core.cloud_api",
        "core.llama_cpp_backend",
        "core.model_manager",
        "core.model_runtime",
        "core.update",
        "web.search_backend",
        "orchestrator",
        "orchestrator.assistant",
        "orchestrator.agent",
        "orchestrator.router",
        "orchestrator.strategy",
        "orchestrator.prompt_builder",
        "orchestrator.git_helper",
        "rag",
        "rag.service",
        "rag.chunker",
        "rag.embedder",
        "rag.indexer",
        "rag.retriever",
        "rag.store",
        "chromadb",
        "chromadb.api",
        "chromadb.api.client",
        "chromadb.api.rust",
        "chromadb_rust_bindings",
        "chromadb.execution.expression.plan",
        "chromadb.config",
        "chromadb.db",
        "chromadb.db.impl",
        "chromadb.segment",
        "chromadb.segment.impl",
        "chromadb.telemetry",
        "chromadb.telemetry.product",
        "chromadb.telemetry.product.posthog",
        "chromadb.telemetry.product.events",
        "licensing",
        "licensing.models",
        "licensing.verifier",
        "licensing.store",
        "licensing.gate",
        "licensing.fingerprint",
        "licensing.trial",
        "licensing.release",
        "cryptography.hazmat.primitives.asymmetric.ed25519",
        "yaml",
        "dotenv",
        "httpx",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # --- training stack: lives in data/training_env, never bundled ---
        "sentence_transformers",
        "torch",
        "torchvision",
        "torchaudio",
        "unsloth",
        "transformers",
        "datasets",
        "peft",
        "trl",
        "accelerate",
        "bitsandbytes",
        "gguf",
        "safetensors",
        "tokenizers",
        "huggingface_hub",
        "hf_xet",
        "pyarrow",
        "pandas",
        "scipy",
        "sklearn",
        "scikit_learn",
        "datasketch",
        # --- dead weight / optional fast paths ---
        "psycopg2",
        "psycopg2_binary",
        "tiktoken",
        "tkinter",
        "_tkinter",
        "flashrank",
        "trafilatura",
        "tree_sitter",
        "duckdb",
        # NOTE: do NOT exclude grpc / opentelemetry.exporter.otlp.proto.grpc:
        # chromadb 1.5.9 client path imports the OTLP grpc exporter
        # unconditionally (chromadb/telemetry/opentelemetry/__init__.py).
        # --- PySide6 modules the widgets+webengine app never imports ---
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.QtQuickWidgets",
        "PySide6.QtQuickControls2",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtDesigner",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtGraphs",
        "PySide6.QtGraphsWidgets",
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DRender",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtSpatialAudio",
        "PySide6.QtNetworkAuth",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtTextToSpeech",
        "PySide6.QtWebSockets",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtStateMachine",
        "PySide6.QtHelp",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtLocation",
        "PySide6.QtUiTools",
        "PySide6.QtHttpServer",
        "PyInstaller",
    ],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FluxionBrowserLite",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FluxionBrowserLite",
)
