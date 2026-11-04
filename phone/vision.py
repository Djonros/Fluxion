"""The vision model of the PC: what answers a phone's question with a photo.

Two engines, tried in this order:

- **embedded** — Qwen2.5-VL 7B (Apache 2.0) in llama-cpp-python: the model GGUF and its
  image part (mmproj) in the models folder, downloaded from «Связь с телефоном»;
  needs llama-cpp-python with ``Qwen25VLChatHandler`` (0.3.9 and newer);
- **ollama** — the ``qwen2.5vl`` model of a running Ollama, through its OpenAI API.

The embedded model is loaded on the first photo and unloaded after ``IDLE_UNLOAD``
seconds without photos, so it does not hold the video memory the chat model needs.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ID = "ggml-org/Qwen2.5-VL-7B-Instruct-GGUF"
MODEL_FILE = "Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf"
MMPROJ_FILE = "mmproj-Qwen2.5-VL-7B-Instruct-Q8_0.gguf"
MODEL_SIZE = 4_700_000_000
MMPROJ_SIZE = 850_000_000
DOWNLOAD_GB = round((MODEL_SIZE + MMPROJ_SIZE) / 1e9, 1)
IDLE_UNLOAD = 10 * 60
# what the model asks of the PC: about 7 GB of memory for the 7B model with its image part,
# plus Windows and the chat model; a video card makes a photo seconds instead of minutes
RAM_GB_GOOD = 16  # below it the model runs, but slowly
RAM_GB_LOW = 12  # below it the model most likely does not start
VRAM_GB_GOOD = 8
N_CTX = 8192


class VisionUnavailable(RuntimeError):
    """No vision model on this PC: said in words the phone shows to the user."""


def models_dir() -> Path:
    from core.model_manager import ModelManager

    return Path(ModelManager().models_dir)


def files(cfg: dict, folder: Path | None = None) -> tuple[Path, Path]:
    """The model and mmproj files: the user's own ones, else the downloaded ones."""
    folder = folder if folder is not None else models_dir()
    model = Path(cfg.get("vision_model") or folder / MODEL_FILE)
    mmproj = Path(cfg.get("vision_mmproj") or folder / MMPROJ_FILE)
    return model, mmproj


def installed(cfg: dict, folder: Path | None = None) -> bool:
    model, mmproj = files(cfg, folder)
    return model.is_file() and mmproj.is_file()


def handler_class():
    """``Qwen25VLChatHandler`` of llama-cpp-python, or None when it is too old or missing."""
    try:
        from llama_cpp import llama_chat_format
    except Exception:
        return None
    return getattr(llama_chat_format, "Qwen25VLChatHandler", None)


def download(on_progress=None, manager=None) -> tuple[Path, Path]:
    """Download the model and its mmproj into the models folder (resumes a broken download)."""
    from core.model_manager import ModelInfo, ModelManager

    manager = manager or ModelManager()
    out = []
    for filename, size in ((MMPROJ_FILE, MMPROJ_SIZE), (MODEL_FILE, MODEL_SIZE)):
        entry = ModelInfo(model_id=f"vision:{filename}", name=filename, repo_id=REPO_ID, filename=filename,
                          size_bytes=size, task="chat")
        out.append(manager._download_entry(entry, filename, on_progress, 60.0))
    return out[1], out[0]


class EmbeddedVision:
    name = "embedded"

    def __init__(self, model: Path, mmproj: Path, n_gpu_layers: int = -1):
        self.model = Path(model)
        self.mmproj = Path(mmproj)
        self.n_gpu_layers = n_gpu_layers
        self.llm = None
        self.last_used = 0.0
        self.lock = threading.Lock()

    @property
    def label(self) -> str:
        return self.model.stem

    def _load(self):
        if self.llm is None:
            from llama_cpp import Llama

            handler = handler_class()
            if handler is None:
                raise VisionUnavailable("llama-cpp-python на компьютере старый: нужна версия 0.3.9 или новее")
            logger.info("Vision model loading: %s", self.model)
            self.llm = Llama(model_path=str(self.model), chat_handler=handler(clip_model_path=str(self.mmproj),
                                                                               verbose=False),
                             n_ctx=N_CTX, n_gpu_layers=self.n_gpu_layers, verbose=False)
        return self.llm

    def complete(self, messages: list[dict], temperature: float, max_tokens: int) -> str:
        with self.lock:
            llm = self._load()
            self.last_used = time.monotonic()
            result = llm.create_chat_completion(messages=messages, temperature=temperature, max_tokens=max_tokens)
            self.last_used = time.monotonic()
        return str(result["choices"][0]["message"].get("content") or "")

    def unload_if_idle(self, idle: float = IDLE_UNLOAD) -> bool:
        with self.lock:
            if self.llm is None or time.monotonic() - self.last_used < idle:
                return False
            close = getattr(self.llm, "close", None)
            if callable(close):
                close()
            self.llm = None
            logger.info("Vision model unloaded after %d s without photos", idle)
            return True


class OllamaVision:
    name = "ollama"

    def __init__(self, host: str, model: str, client=None):
        self.host = host.rstrip("/")
        self.model = model
        self.client = client

    @property
    def label(self) -> str:
        return f"Ollama {self.model}"

    def _http(self):
        if self.client is None:
            import httpx

            self.client = httpx.Client(timeout=300)
        return self.client

    def available(self) -> bool:
        try:
            response = self._http().get(f"{self.host}/api/tags", timeout=3)
            names = {m.get("name", "") for m in response.json().get("models", [])}
        except Exception:
            return False
        wanted = self.model if ":" in self.model else self.model + ":latest"
        return wanted in names

    def complete(self, messages: list[dict], temperature: float, max_tokens: int) -> str:
        response = self._http().post(f"{self.host}/v1/chat/completions", json={
            "model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
        })
        if response.status_code >= 400:
            raise VisionUnavailable(f"Ollama ответила {response.status_code}: {response.text[:200]}")
        return str(response.json()["choices"][0]["message"].get("content") or "")

    def unload_if_idle(self, idle: float = IDLE_UNLOAD) -> bool:
        return False  # Ollama unloads its models itself


def total_ram_gb() -> float:
    """Memory of the PC in GB; 0 when it cannot be read."""
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                            ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                            ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                            ("avail_extended", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return 0.0
            total = status.total_phys
        else:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return 0.0
    return round(total / 1024 ** 3, 1)


def out_of_memory(exc: BaseException) -> bool:
    """llama.cpp could not get the memory the vision model needs."""
    text = str(exc).lower()
    return isinstance(exc, MemoryError) or any(word in text for word in (
        "out of memory", "failed to allocate", "cannot allocate", "bad_alloc", "unable to allocate"))


def gpu_layers() -> int:
    """All layers on the video card when it has room for the 7B model with its mmproj, else none."""
    try:
        from core.model_manager import detect_gpu_memory_gb

        return -1 if detect_gpu_memory_gb() >= 7 else 0
    except Exception:
        return 0


def pick(cfg: dict, ollama_host: str, folder: Path | None = None, embedded=None):
    """The engine to answer with; ``embedded`` is the one already loaded, kept between photos."""
    if installed(cfg, folder) and handler_class() is not None:
        model, mmproj = files(cfg, folder)
        if embedded is not None and embedded.model == model and embedded.mmproj == mmproj:
            return embedded
        return EmbeddedVision(model, mmproj, gpu_layers())
    ollama = OllamaVision(ollama_host, cfg.get("ollama_model") or "qwen2.5vl:7b")
    if ollama.available():
        return ollama
    if installed(cfg, folder):
        raise VisionUnavailable("Модель для фото скачана, но llama-cpp-python на компьютере старый: "
                                "нужна версия 0.3.9 или новее (или Ollama с моделью qwen2.5vl)")
    raise VisionUnavailable("На компьютере нет модели для фото: в Fluxion откройте Связь → "
                            "«Связь с телефоном» и нажмите «Скачать модель для фото»")
