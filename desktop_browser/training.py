"""Desktop training pipeline: orchestrates finetune stages for the GUI.

Stages: QLoRA train -> merge -> GGUF export (optional, needs LLAMA_CPP_DIR)
-> ollama create (optional) -> adapter registry. All heavy imports stay lazy
so the slim desktop build bundles this module safely.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from finetune.marketplace import AdapterInfo, AdapterRegistry
from finetune.qlora_config import QLoRASettings, VRAMPreset


class TrainingError(Exception):
    """Pipeline failure with a user-facing message."""


ENV_DIR_NAME = "training_env"
ENV_MARKER = "fluxion_ready.json"
TRAINING_PACK_NAME = "training_pack"
WHEELS_DIR_NAME = "wheels"
TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu126"
TORCH_PIN = "torch==2.12.1+cu126"
TRAINING_PACKAGES = [
    "unsloth",
    "peft",
    "trl",
    "transformers",
    "datasets",
    "accelerate",
    "bitsandbytes",
    "gguf",
    "cryptography",
]


def _source_root() -> Path:
    """Source tree visible to external interpreters (works frozen and raw)."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else Path(__file__).resolve().parents[1]


def app_root() -> Path:
    """App home dir: exe folder when frozen, repo root otherwise."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def find_offline_wheels(root: str | Path | None = None) -> Path | None:
    """Locate the shipped CUDA wheel pack (Full artifact) for offline installs."""
    override = os.environ.get("FLUXION_OFFLINE_WHEELS", "").strip()
    if override:
        candidate = Path(override)
        return candidate if candidate.is_dir() else None
    base = Path(root) if root is not None else app_root()
    candidate = base / TRAINING_PACK_NAME / WHEELS_DIR_NAME
    return candidate if candidate.is_dir() else None


def env_python_path(env_dir: str | Path) -> Path:
    env_dir = Path(env_dir)
    if os.name == "nt":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def detect_training_env(env_dir: str | Path) -> str | None:
    """Return the env interpreter path when a ready marker exists."""
    env_dir = Path(env_dir)
    py = env_python_path(env_dir)
    marker = env_dir / ENV_MARKER
    if not (marker.is_file() and py.is_file()):
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    recorded = data.get("python")
    if recorded and Path(recorded).is_file():
        return str(recorded)
    return str(py)


def _locate_windows_python() -> str | None:
    """Find a 3.12/3.11 interpreter via py-launcher or standard dirs."""
    if os.name != "nt":
        return None
    import shutil

    launcher = shutil.which("py")
    dirs: list[Path] = []
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        dirs.append(Path(program_files))
    local_programs = os.environ.get("LOCALAPPDATA")
    if local_programs:
        dirs.append(Path(local_programs) / "Programs" / "Python")
    for ver in ("3.12", "3.11"):
        if launcher:
            probe = subprocess.run(
                [launcher, f"-{ver}", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0:
                path = probe.stdout.strip().splitlines()[0]
                if path:
                    return path
        for base in dirs:
            py = base / f"Python{ver.replace('.', '')}" / "python.exe"
            if py.is_file():
                return str(py)
    return None


def find_host_python() -> tuple[str | None, str]:
    """Find a Python suitable for the training stack (3.11/3.12 preferred)."""
    if os.name == "nt":
        found = _locate_windows_python()
        if found:
            return found, f"Найден Python: {found}"
    if not getattr(sys, "frozen", False) and sys.version_info[:2] <= (3, 12):
        return (
            sys.executable,
            f"Используется текущий Python {sys.version_info[0]}.{sys.version_info[1]}.",
        )
    return (
        None,
        "Для обучения нужен Python 3.11 или 3.12 (torch/unsloth). "
        "Его можно установить автоматически (winget, нужны права администратора) "
        "или вручную с python.org.",
    )


def autoinstall_python(
    log: Callable[[str], None],
    stop_requested: Callable[[], bool],
) -> str:
    """Install Python 3.12 machine-wide via winget (triggers UAC prompt).

    Returns the freshly installed interpreter path. Raises TrainingError
    when winget is unavailable or the interpreter cannot be located after.
    """
    import shutil

    if os.name != "nt":
        raise TrainingError("Автоматическая установка Python поддерживается только в Windows.")
    winget = shutil.which("winget")
    if not winget:
        raise TrainingError(
            "winget не найден. Установите Python 3.12 вручную с python.org "
            "и повторите установку окружения."
        )
    log("Python 3.12 не найден. Установка через winget — подтвердите запрос")
    log("прав администратора (UAC), потребуется подключение к интернету.")
    _run_cmd(
        [
            winget,
            "install",
            "-e",
            "--id",
            "Python.Python.3.12",
            "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ],
        log,
        stop_requested,
    )
    py = _locate_windows_python()
    if not py:
        raise TrainingError(
            "Python установлен, но интерпретатор не найден. "
            "Перезапустите приложение и повторите установку окружения."
        )
    log(f"Python установлен: {py}")
    return py


def _run_cmd(
    cmd: list[str],
    log: Callable[[str], None],
    stop_requested: Callable[[], bool],
    extra_env: dict[str, str] | None = None,
) -> None:
    """Run a subprocess, streaming merged output to *log*; stop-aware."""
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            log(line.rstrip("\n"))
            if stop_requested():
                raise TrainingError("Остановлено пользователем")
    except TrainingError:
        proc.terminate()
        try:
            proc.wait(5000)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise
    returncode = proc.wait()
    if returncode != 0:
        head = " ".join(str(part) for part in cmd[:4])
        raise TrainingError(f"Команда завершилась с кодом {returncode}: {head}")


@dataclass
class TrainingParams:
    dataset: str = ""
    preset: str = "low"
    epochs: int = 3
    max_samples: int = 0
    base_model: str = "Qwen/Qwen2.5-Coder-7B-Instruct"
    adapter_name: str = "fluxion-adapter"
    description: str = ""
    ollama_model_name: str = "fluxion-coder-python"
    export_gguf: bool = True


def install_training_environment(
    env_dir: str | Path,
    *,
    log: Callable[[str], None],
    stop_requested: Callable[[], bool],
    host_python: str | None = None,
    auto_install_python: bool = False,
    offline_wheels: str | Path | None = None,
) -> str:
    """Create a dedicated venv and pip-install the training stack.

    When *host_python* is missing and *auto_install_python* is set, Python
    3.12 is installed first via winget (UAC confirmation required). When
    *offline_wheels* points at the shipped wheel pack (Full artifact),
    every pip install runs with --no-index --find-links and never touches
    PyPI. Returns the venv interpreter path. Raises TrainingError on
    failure or when stopped by the user.
    """
    env_dir = Path(env_dir)
    env_dir.mkdir(parents=True, exist_ok=True)
    py = env_python_path(env_dir)

    offline_args: list[str] = []
    if offline_wheels is not None:
        wheels = Path(offline_wheels)
        if not wheels.is_dir():
            raise TrainingError(
                f"Офлайн-пак не найден: {wheels}. Переустановите Full-версию "
                "или повторите установку с подключением к интернету."
            )
        offline_args = ["--no-index", "--find-links", str(wheels)]
        log(f"Офлайн-установка ML-стека из пака: {wheels}")

    if not host_python:
        host_python, note = find_host_python()
        log(note)
        if host_python is None and auto_install_python:
            host_python = autoinstall_python(log, stop_requested)
        if host_python is None:
            raise TrainingError(note)

    log(f"Создание venv: {env_dir}")
    _run_cmd([host_python, "-m", "venv", "--clear", str(env_dir)], log, stop_requested)
    if offline_args:
        log("Офлайн-режим: обновление pip пропущено (используется pip из venv).")
    else:
        log("Обновление pip...")
        _run_cmd(
            [str(py), "-m", "pip", "install", "--upgrade", "pip", "wheel"],
            log,
            stop_requested,
        )
    log("Установка PyTorch (может занять длительное время)...")
    torch_cmd = [str(py), "-m", "pip", "install", TORCH_PIN]
    if offline_args:
        torch_cmd += offline_args
    else:
        torch_index = os.environ.get("FLUXION_TORCH_INDEX", TORCH_INDEX_URL)
        torch_cmd += ["--index-url", torch_index]
    _run_cmd(torch_cmd, log, stop_requested)
    log("Установка ML-стека (unsloth, peft, trl, ...)...")
    _run_cmd(
        [str(py), "-m", "pip", "install", *TRAINING_PACKAGES, *offline_args],
        log,
        stop_requested,
    )
    _run_cmd(
        [str(py), "-m", "pip", "install", "torch", "--index-url", torch_index],
        log,
        stop_requested,
    )
    log("Установка ML-стека (unsloth, peft, trl, ...)...")
    _run_cmd(
        [str(py), "-m", "pip", "install", *TRAINING_PACKAGES],
        log,
        stop_requested,
    )
    (env_dir / ENV_MARKER).write_text(
        json.dumps({"python": str(py)}), encoding="utf-8"
    )
    log("Окружение обучения готово.")
    return str(py)


def _detect_trainer(log: Callable[[str], None], env_python: str | None = None):
    """Pick the trainer: env venv subprocess > in-process unsloth > HF peft."""
    if env_python:
        return "env", f"venv ({env_python})", env_python
    if importlib.util.find_spec("unsloth") is not None:
        from finetune.train_unsloth import train

        return train, "unsloth", None
    if importlib.util.find_spec("peft") is not None:
        from finetune.train_hf import train

        return train, "hf (peft+trl)", None
    return None, "", None


class _SignalLogHandler(logging.Handler):
    """Forwards finetune log records to the GUI log callback."""

    def __init__(self, log: Callable[[str], None]):
        super().__init__(level=logging.INFO)
        self._log = log
        self.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._log(self.format(record))
        except Exception:
            pass


def _absolutize_from(modelfile_path: Path, gguf_path: str) -> str:
    """Rewrite a relative `FROM ./x.gguf` line to an absolute path."""
    lines = []
    for line in modelfile_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FROM"):
            parts = stripped.split(None, 1)
            arg = parts[1].strip() if len(parts) > 1 else ""
            if arg.startswith("./") or arg.startswith("../"):
                line = f"FROM {(modelfile_path.parent / arg).resolve()}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def run_pipeline(
    params: TrainingParams,
    *,
    log: Callable[[str], None],
    stop_requested: Callable[[], bool],
    data_dir: str,
    client=None,
) -> dict:
    """Run the full training pipeline. Raises TrainingError on failure."""
    from licensing import ProRequiredError, ensure_pro

    try:
        ensure_pro("qlora")
    except ProRequiredError as exc:
        raise TrainingError(str(exc)) from exc

    if not Path(params.dataset).is_file():
        raise TrainingError(f"Датасет не найден: {params.dataset}")

    settings = QLoRASettings.from_preset(VRAMPreset(params.preset))
    settings.base_model = params.base_model
    settings.num_train_epochs = params.epochs
    settings.ollama_model_name = params.ollama_model_name

    trainer, backend_name, env_py = _detect_trainer(
        log, detect_training_env(Path(data_dir) / ENV_DIR_NAME)
    )
    if trainer is None:
        raise TrainingError(
            "Окружение обучения не найдено: нужен unsloth (6 ГБ VRAM) или "
            "torch+peft+trl (8–12 ГБ). Установите его кнопкой "
            "«Установить окружение обучения» на странице «Обучение»."
        )

    if env_py:
        subprocess_env = {"PYTHONPATH": str(_source_root())}

        def trainer(settings, dataset, max_samples):  # type: ignore[misc]
            module = (
                "finetune.train_unsloth"
                if params.preset == "low"
                else "finetune.train_hf"
            )
            cmd = [
                env_py,
                "-m",
                module,
                "--dataset",
                str(dataset),
                "--preset",
                params.preset,
                "--epochs",
                str(settings.num_train_epochs),
                "--base-model",
                settings.base_model,
            ]
            if max_samples:
                cmd += ["--max-samples", str(max_samples)]
            _run_cmd(cmd, log, stop_requested, extra_env=subprocess_env)
            return str(Path(settings.output_dir) / "adapter")

    def merge_runner(adapter_path: str, settings: QLoRASettings) -> str:
        if env_py:
            _run_cmd(
                [
                    env_py,
                    "-m",
                    "finetune.merge",
                    "--adapter",
                    adapter_path,
                    "--base-model",
                    settings.base_model,
                ],
                log,
                stop_requested,
                extra_env=subprocess_env,
            )
            return settings.merged_dir
        from finetune.merge import merge_lora

        return merge_lora(adapter_path, settings)

    def gguf_runner(merged_dir: str, settings: QLoRASettings, llama_dir: str) -> str:
        if env_py:
            _run_cmd(
                [
                    env_py,
                    "-m",
                    "finetune.export_gguf",
                    "--model",
                    str(merged_dir),
                    "--llama-cpp-dir",
                    llama_dir,
                ],
                log,
                stop_requested,
                extra_env=subprocess_env,
            )
            name = Path(merged_dir).name
            quant = settings.gguf_quantize
            for candidate in (
                Path(settings.gguf_dir) / f"{name}.{quant}.gguf",
                Path(settings.gguf_dir) / f"{name}.f16.gguf",
            ):
                if candidate.is_file():
                    return str(candidate)
            raise TrainingError(f"GGUF-файл не найден после экспорта в {settings.gguf_dir}")
        from finetune.export_gguf import export_gguf

        return export_gguf(str(merged_dir), settings, llama_cpp_dir=llama_dir)

    def stage(title: str) -> bool:
        if stop_requested():
            return False
        log(f"— {title} —")
        return True

    result: dict = {
        "adapter": "",
        "merged": "",
        "gguf": "",
        "ollama_model": "",
        "registered": False,
        "created": False,
        "cancelled": False,
        "trainer": backend_name,
    }

    log(
        f"Тренер: {backend_name} | база: {settings.base_model} | "
        f"датасет: {params.dataset}"
    )
    handler = _SignalLogHandler(log)
    logging.getLogger().addHandler(handler)
    try:
        if not stage("Обучение (QLoRA)"):
            result["cancelled"] = True
            return result
        try:
            adapter_path = str(trainer(settings, params.dataset, params.max_samples or None))
        except SystemExit as exc:
            raise TrainingError(f"Обучение прервано (exit {exc.code})") from exc
        result["adapter"] = adapter_path
        log(f"Адаптер сохранён: {adapter_path}")

        from finetune.merge import merge_lora

        if not stage("Merge адаптера с базовой моделью"):
            result["cancelled"] = True
            return result
        try:
            merged_dir = merge_runner(adapter_path, settings)
        except SystemExit as exc:
            raise TrainingError(f"Merge прерван (exit {exc.code})") from exc
        result["merged"] = str(merged_dir)

        if params.export_gguf:
            llama_dir = os.environ.get("LLAMA_CPP_DIR", "")
            if llama_dir and Path(llama_dir).is_dir():
                from finetune.export_gguf import export_gguf

                if not stage("Экспорт GGUF + квантизация"):
                    result["cancelled"] = True
                    return result
                try:
                    gguf_path = gguf_runner(str(merged_dir), settings, llama_dir)
                except SystemExit as exc:
                    raise TrainingError(f"Экспорт GGUF прерван (exit {exc.code})") from exc
                result["gguf"] = str(gguf_path)

                modelfile = Path(settings.gguf_dir) / "Modelfile"
                if client is not None and modelfile.is_file():
                    if not stage("Ollama create"):
                        result["cancelled"] = True
                        return result
                    try:
                        client.create(
                            params.ollama_model_name,
                            _absolutize_from(modelfile, str(gguf_path)),
                        )
                        result["created"] = True
                        log(f"Модель Ollama создана: {params.ollama_model_name}")
                    except Exception as exc:
                        log(f"ollama create не удался: {exc}")
            else:
                log(
                    "LLAMA_CPP_DIR не задан — экспорт GGUF и ollama create "
                    "пропущены (адаптер и merged-модель сохранены)."
                )
        else:
            log("Экспорт GGUF отключён пользователем.")

        if not stage("Регистрация адаптера"):
            result["cancelled"] = True
            return result
        registry = AdapterRegistry(Path(data_dir) / "lora_registry")
        registry.register(
            AdapterInfo.from_training(settings, params.adapter_name, params.description),
            exist_ok=True,
        )
        result["registered"] = True
        result["ollama_model"] = params.ollama_model_name
        log(f"Адаптер '{params.adapter_name}' зарегистрирован.")
        return result
    finally:
        logging.getLogger().removeHandler(handler)
