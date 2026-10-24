"""Model manager (roadmap 18.2): curated catalog + GGUF download/import.

Storage: %LOCALAPPDATA%/Fluxion/models (override: FLUXION_MODELS_DIR).
Every installed model gets a .json sidecar with source metadata.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_GGUF_MAGIC = b"GGUF"
_CHUNK = 1024 * 512


def _default_models_dir() -> Path:
    override = os.environ.get("FLUXION_MODELS_DIR", "").strip()
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Fluxion" / "models"


def _ollama_models_dir() -> Path:
    override = os.environ.get("OLLAMA_MODELS", "").strip()
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "ollama" / "models"


@dataclass
class ModelInfo:
    model_id: str
    name: str
    repo_id: str  # HuggingFace repo
    filename: str
    size_bytes: int
    task: str = "chat"  # "chat" | "embedding"
    vram_gb: float = 0.0
    sha256: str = ""  # optional integrity pin
    default: bool = False
    # Pro catalog: downloading requires the "model_catalog" Pro feature.
    pro: bool = False
    summary: str = ""  # one line for the "Models" page and the website
    tags: list[str] = field(default_factory=list)  # code | chat | small | rag: filters of the page
    min_app_version: str = ""  # shown only in this app version and newer


# The catalog lives in model_catalog.json: shipped inside the app and published
# on the website (data/catalog.json), where the app picks up new models
# without a release (fetch_remote_catalog).
CATALOG_FILE = Path(__file__).with_name("model_catalog.json")
DEFAULT_CATALOG_URL = "https://djonros.github.io/Fluxion/data/catalog.json"
REMOTE_CACHE_NAME = "catalog-remote.json"

_REPO_RE = re.compile(r"[\w.-]+/[\w.-]+")
_SPLIT_GGUF_RE = re.compile(r"-\d{5}-of-\d{5}")


def entry_from_dict(data: dict) -> ModelInfo | None:
    """One catalog record, or None when it is malformed.

    Remote records come from the network: only a Hugging Face repo and a single
    plain .gguf file name are accepted, so a record cannot point the downloader
    anywhere else or at a path outside the models folder.
    """
    try:
        model_id = str(data.get("id") or data.get("model_id") or "").strip()
        repo_id = str(data.get("repo_id", "")).strip()
        filename = str(data.get("filename", "")).strip()
        task = str(data.get("task", "chat") or "chat")
        tags = data.get("tags") or []
        entry = ModelInfo(
            model_id=model_id,
            name=str(data.get("name", "")).strip(),
            repo_id=repo_id,
            filename=filename,
            size_bytes=int(data.get("size_bytes") or 0),
            task=task,
            vram_gb=float(data.get("vram_gb") or 0.0),
            sha256=str(data.get("sha256", "") or "").strip().lower(),
            default=bool(data.get("default", False)),
            pro=bool(data.get("pro", False)),
            summary=str(data.get("summary", "") or "").strip(),
            tags=[str(t).strip().lower() for t in tags if str(t).strip()] if isinstance(tags, list) else [],
            min_app_version=str(data.get("min_app_version", "") or "").strip(),
        )
    except (AttributeError, TypeError, ValueError):
        return None
    if not (entry.model_id and entry.name and entry.size_bytes > 0):
        return None
    if not _REPO_RE.fullmatch(repo_id) or ".." in repo_id:
        return None
    if (
        not filename.lower().endswith(".gguf")
        or filename != Path(filename).name
        or "\\" in filename
        or _SPLIT_GGUF_RE.search(filename)
    ):
        return None
    if task not in ("chat", "embedding"):
        return None
    if entry.sha256 and not re.fullmatch(r"[0-9a-f]{64}", entry.sha256):
        return None
    return entry


def parse_catalog(data) -> list[ModelInfo]:
    """Entries of a catalog document ({"models": [...]}); bad records are skipped."""
    records = data.get("models", []) if isinstance(data, dict) else []
    entries: list[ModelInfo] = []
    seen: set[str] = set()
    for record in records if isinstance(records, list) else []:
        entry = entry_from_dict(record) if isinstance(record, dict) else None
        if entry is None or entry.model_id in seen:
            continue
        seen.add(entry.model_id)
        entries.append(entry)
    return entries


def load_catalog_file(path: Path) -> list[ModelInfo]:
    try:
        return parse_catalog(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


MODEL_CATALOG: list[ModelInfo] = load_catalog_file(CATALOG_FILE)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version)[:3])


def _app_version() -> str:
    try:
        from desktop_browser import APP_VERSION

        return APP_VERSION
    except Exception:
        return ""


def merge_catalog(
    bundled: list[ModelInfo], remote: list[ModelInfo], app_version: str = ""
) -> list[ModelInfo]:
    """The bundled catalog updated by the remote one.

    A remote record updates the bundled model with the same id (size, summary,
    tags…) or adds a new model.  It never changes which model is the default
    and never adds a second file name, and a record needing a newer app is
    left out.
    """
    merged = {m.model_id: m for m in bundled}
    order = [m.model_id for m in bundled]
    for entry in remote:
        if entry.min_app_version and app_version and _version_key(entry.min_app_version) > _version_key(app_version):
            continue
        old = merged.get(entry.model_id)
        files = {m.filename.lower() for m in merged.values() if m is not old}
        if entry.filename.lower() in files:
            continue
        if old is not None:
            entry.default = old.default
            entry.task = old.task
        else:
            entry.default = False
            order.append(entry.model_id)
        merged[entry.model_id] = entry
    return [merged[i] for i in order]


def catalog_url() -> str:
    """Where the app looks for new models; "off" (or empty) switches it off."""
    value = os.environ.get("FLUXION_CATALOG_URL")
    if value is None:
        return DEFAULT_CATALOG_URL
    value = value.strip()
    return "" if value.lower() in ("", "off", "0", "no") else value


PRO_FEATURE = "model_catalog"


class ModelDownloadError(RuntimeError):
    """Download failed; the message is shown to the user as is."""


def _content_range_total(value: str) -> int:
    """Total size from a Content-Range header: "bytes */123" or "bytes 0-9/123"."""
    try:
        total = value.rsplit("/", 1)[1].strip()
        return int(total) if total.isdigit() else 0
    except (IndexError, ValueError):
        return 0


def _status_message(status: int, filename: str) -> str:
    if status in (401, 403):
        return f"Hugging Face не даёт скачать {filename} (код {status}). Попробуйте позже."
    if status == 404:
        return f"Файл {filename} не найден на Hugging Face (код 404)."
    if status == 429:
        return "Hugging Face ограничил число загрузок (код 429). Подождите несколько минут."
    return f"Сервер Hugging Face ответил ошибкой {status} при загрузке {filename}."


def _is_http_error(exc: BaseException) -> bool:
    error_type = getattr(httpx, "HTTPError", None)
    return isinstance(error_type, type) and isinstance(exc, error_type)


def model_allowed(entry: ModelInfo) -> bool:
    """Free models always; Pro catalog models with an active Pro licence."""
    if not entry.pro:
        return True
    try:
        from licensing import feature_enabled
    except Exception:  # licensing package missing: treat as no licence
        return False
    return feature_enabled(PRO_FEATURE)


def hf_download_url(repo_id: str, filename: str) -> str:
    return f"https://huggingface.co/{repo_id}/resolve/main/{filename}"


# ── any GGUF model from Hugging Face by link ("Скачать по ссылке…") ─────────

HF_HOSTS = ("huggingface.co", "www.huggingface.co", "hf.co")


@dataclass
class HfFile:
    """One GGUF file of a Hugging Face repository."""

    repo_id: str
    path: str  # inside the repository, may contain folders
    size_bytes: int = 0
    sha256: str = ""

    @property
    def filename(self) -> str:
        return self.path.rsplit("/", 1)[-1]


def parse_hf_link(text: str) -> tuple[str, str, str]:
    """(repo_id, file path or "", quant hint or "") from what the user pasted.

    Accepts "owner/repo", a model page, a /tree/ or /blob/ or /resolve/ link
    to a file, and the "hf.co/owner/repo:Q4_K_M" form.  Raises ValueError
    with a user-facing message for anything else.
    """
    from urllib.parse import unquote, urlparse

    value = (text or "").strip().strip("\"'")
    if not value:
        raise ValueError("Вставьте ссылку на модель с Hugging Face.")
    if "://" not in value and value.split("/", 1)[0].lower() in HF_HOSTS:
        value = "https://" + value
    if "://" in value:
        url = urlparse(value)
        if url.scheme not in ("http", "https") or (url.hostname or "").lower() not in HF_HOSTS:
            raise ValueError("Поддерживаются только ссылки на huggingface.co.")
        parts = [unquote(p) for p in url.path.split("/") if p]
    else:
        parts = [p for p in value.split("/") if p]
    if parts and parts[0] == "models":
        parts = parts[1:]
    if len(parts) < 2:
        raise ValueError("В ссылке нет модели: нужен вид «автор/модель», например bartowski/Qwen2.5-7B-Instruct-GGUF.")
    owner, repo = parts[0], parts[1]
    quant = ""
    if ":" in repo:
        repo, quant = repo.split(":", 1)
    repo_id = f"{owner}/{repo}"
    if not _REPO_RE.fullmatch(repo_id) or ".." in repo_id:
        raise ValueError(f"Непонятное имя модели: {repo_id}")
    path = ""
    if len(parts) > 4 and parts[2] in ("blob", "resolve", "tree"):
        path = "/".join(parts[4:])  # parts[3] is the branch
        if not path.lower().endswith(".gguf"):
            path = ""  # a folder link: list the files
    if path and (".." in path.split("/") or "\\" in path):
        raise ValueError("Недопустимый путь к файлу в ссылке.")
    return repo_id, path, quant


def hf_file_problem(path: str) -> str:
    """Why a repository file cannot be used, or "" when it can."""
    name = path.rsplit("/", 1)[-1]
    if not name.lower().endswith(".gguf"):
        return "не GGUF"
    if _SPLIT_GGUF_RE.search(name):
        return "модель разбита на части: такие пока не поддерживаются"
    if "mmproj" in name.lower():
        return "это не модель, а дополнение для картинок (mmproj)"
    return ""


def list_hf_gguf_files(repo_id: str, timeout: float = 20.0) -> list[HfFile]:
    """GGUF files of a Hugging Face model repository (one request to the HF API)."""
    url = f"https://huggingface.co/api/models/{repo_id}/tree/main?recursive=1"
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    except Exception as exc:
        if _is_http_error(exc):
            raise ModelDownloadError("Нет связи с Hugging Face: проверьте интернет.") from exc
        raise
    if resp.status_code in (401, 403):
        raise ModelDownloadError(
            f"Модель {repo_id} закрытая: её автор выдаёт доступ по запросу на Hugging Face. "
            "Скачайте файл в браузере и добавьте кнопкой «Импорт с диска…»."
        )
    if resp.status_code == 404:
        raise ModelDownloadError(f"Модель {repo_id} не найдена на Hugging Face.")
    if resp.status_code != 200:
        raise ModelDownloadError(f"Hugging Face ответил ошибкой {resp.status_code}.")
    try:
        items = resp.json()
    except ValueError as exc:
        raise ModelDownloadError("Hugging Face прислал непонятный ответ.") from exc
    files: list[HfFile] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        path = str(item.get("path", ""))
        if not path.lower().endswith(".gguf"):
            continue
        lfs = item.get("lfs") if isinstance(item.get("lfs"), dict) else {}
        sha = str(lfs.get("oid", "") or "").lower()
        files.append(
            HfFile(
                repo_id=repo_id,
                path=path,
                size_bytes=int(lfs.get("size") or item.get("size") or 0),
                sha256=sha if re.fullmatch(r"[0-9a-f]{64}", sha) else "",
            )
        )
    files.sort(key=lambda f: (bool(hf_file_problem(f.path)), f.size_bytes))
    return files


def detect_gpu_memory_gb() -> float:
    """Memory of the largest NVIDIA video card in GB (nvidia-smi); 0 when unknown."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return 0.0
    try:
        import subprocess

        from .proc import no_window

        out = subprocess.run(
            [exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, **no_window(),
        ).stdout
    except Exception:
        return 0.0
    values = [float(v) for v in re.findall(r"\d+(?:\.\d+)?", out or "")]
    return round(max(values) / 1024, 1) if values else 0.0  # MiB -> GB


def _entry_to_dict(entry: ModelInfo) -> dict:
    data = asdict(entry)
    data["id"] = data.pop("model_id")
    return data


@dataclass
class InstalledModel:
    filename: str
    path: Path
    size_bytes: int
    source: str = ""  # catalog model_id | "disk" | "ollama:<name>"
    task: str = "chat"


class ModelManager:
    """Downloads/imports GGUF models into the local Fluxion storage."""

    def __init__(self, models_dir: Path | str | None = None):
        self.models_dir = Path(models_dir) if models_dir else _default_models_dir()
        self.models_dir.mkdir(parents=True, exist_ok=True)

    # ── catalog / listing ─────────────────────────────────────────────

    def catalog(self) -> list[ModelInfo]:
        """Bundled catalog plus the models last fetched from the website."""
        remote = load_catalog_file(self.models_dir / REMOTE_CACHE_NAME)
        if not remote:
            return list(MODEL_CATALOG)
        return merge_catalog(list(MODEL_CATALOG), remote, _app_version())

    def catalog_entry(self, model_id: str) -> ModelInfo | None:
        return next((m for m in self.catalog() if m.model_id == model_id), None)

    def fetch_remote_catalog(self, url: str | None = None, timeout: float = 10.0) -> int:
        """Download the website catalog into the models folder.

        Returns the number of models it lists; raises RuntimeError with a
        user-facing message.  The cached copy is used until the next fetch,
        also offline.
        """
        url = catalog_url() if url is None else url
        if not url:
            raise RuntimeError("Обновление каталога выключено (FLUXION_CATALOG_URL).")
        try:
            resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        except Exception as exc:
            if _is_http_error(exc):
                raise RuntimeError("Нет связи с сайтом Fluxion: показан каталог программы.") from exc
            raise
        if resp.status_code != 200:
            raise RuntimeError(f"Сайт Fluxion ответил ошибкой {resp.status_code}: показан каталог программы.")
        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError("Каталог на сайте повреждён: показан каталог программы.") from exc
        entries = parse_catalog(data)
        if not entries:
            raise RuntimeError("Каталог на сайте пуст: показан каталог программы.")
        cache = self.models_dir / REMOTE_CACHE_NAME
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"models": [_entry_to_dict(e) for e in entries]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(cache)
        return len(entries)

    def list_installed(self) -> list[InstalledModel]:
        installed: list[InstalledModel] = []
        for gguf in sorted(self.models_dir.glob("*.gguf")):
            sidecar = self._sidecar(gguf)
            installed.append(
                InstalledModel(
                    filename=gguf.name,
                    path=gguf,
                    size_bytes=gguf.stat().st_size,
                    source=str(sidecar.get("source", "")),
                    task=str(sidecar.get("task", "chat")),
                )
            )
        return installed

    def is_installed(self, model_id: str) -> bool:
        entry = self.catalog_entry(model_id)
        return entry is not None and (self.models_dir / entry.filename).is_file()

    def path_for(self, model_id: str) -> str:
        entry = self.catalog_entry(model_id)
        if entry is None:
            return ""
        return str(self.models_dir / entry.filename)

    # ── download ──────────────────────────────────────────────────────

    def download(
        self,
        model_id: str,
        on_progress=None,
        timeout: float = 60.0,
    ) -> Path:
        """Download a catalog model with resume + optional sha256 check."""
        entry = self.catalog_entry(model_id)
        if entry is None:
            raise KeyError(f"Unknown model id: {model_id}")
        if not model_allowed(entry):
            # Checked here, not only in the UI, so no other caller skips it.
            from licensing import ProRequiredError

            raise ProRequiredError(
                f"Модель «{entry.name}» доступна в Fluxion Pro. "
                "Активируйте лицензию: меню Помощь → Лицензия…"
            )
        return self._download_entry(entry, entry.filename, on_progress, timeout)

    def download_hf_file(self, file: HfFile, on_progress=None, timeout: float = 60.0) -> Path:
        """Download any GGUF file of a Hugging Face repository (not from the catalog)."""
        problem = hf_file_problem(file.path)
        if problem or not _REPO_RE.fullmatch(file.repo_id) or ".." in file.path.split("/"):
            raise ModelDownloadError(f"Файл {file.filename} не скачать: {problem or 'недопустимое имя'}.")
        entry = ModelInfo(
            model_id=f"hf:{file.repo_id}/{file.path}",
            name=file.filename,
            repo_id=file.repo_id,
            filename=file.filename,
            size_bytes=file.size_bytes,
            sha256=file.sha256,
        )
        return self._download_entry(entry, file.path, on_progress, timeout)

    def _download_entry(self, entry: ModelInfo, repo_path: str, on_progress, timeout: float) -> Path:
        target = self.models_dir / entry.filename
        if target.is_file():
            return target
        url = hf_download_url(entry.repo_id, repo_path)
        part = target.with_suffix(target.suffix + ".part")
        try:
            self._fetch(url, part, entry, on_progress, timeout)
        except ModelDownloadError:
            raise
        except Exception as exc:
            if _is_http_error(exc):
                raise ModelDownloadError(
                    "Нет связи с Hugging Face — проверьте интернет и нажмите «Скачать» "
                    "ещё раз: загрузка продолжится с места остановки."
                ) from exc
            raise

        if not self._is_gguf(part):
            part.unlink(missing_ok=True)
            raise ModelDownloadError(
                f"Скачанный файл {entry.filename} не является моделью GGUF "
                "(сервер прислал что-то другое). Попробуйте ещё раз позже."
            )
        if entry.sha256:
            digest = _file_sha256(part)
            if digest != entry.sha256:
                part.unlink(missing_ok=True)
                raise RuntimeError(
                    f"SHA256 mismatch for {entry.filename}: expected {entry.sha256}, got {digest}"
                )
        part.replace(target)
        self._write_sidecar(
            target,
            {
                "source": entry.model_id,
                "task": entry.task,
                "repo_id": entry.repo_id,
                "url": url,
                "size_bytes": target.stat().st_size,
            },
        )
        return target

    def _fetch(self, url: str, part: Path, entry: "ModelInfo", on_progress, timeout: float) -> None:
        """Download *url* into *part*, resuming an existing partial file.

        A server answers 416 (Range Not Satisfiable) when the requested
        resume offset is not inside the file: the partial file is already
        complete (the previous run stopped right before finishing) or it is
        larger than the remote file (corrupt).  The first case finishes, the
        second restarts from scratch — previously the download failed with
        416 forever.
        """
        for _attempt in range(2):
            start = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={start}-"} if start else {}
            with httpx.stream("GET", url, headers=headers, timeout=timeout, follow_redirects=True) as resp:
                status = resp.status_code
                if status == 416 and start:
                    remote_total = _content_range_total(resp.headers.get("content-range", ""))
                    if remote_total and start == remote_total:
                        return  # already complete
                    part.unlink(missing_ok=True)  # corrupt or oversized: start over
                    continue
                if start and status != 206:
                    start = 0  # server ignored Range — restart from scratch
                    part.unlink(missing_ok=True)
                if status >= 400:
                    raise ModelDownloadError(_status_message(status, entry.filename))
                length = int(resp.headers.get("content-length", 0) or 0)
                total = length + start if length else 0
                done = start
                with open(part, "ab" if start else "wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=_CHUNK):
                        fh.write(chunk)
                        done += len(chunk)
                        if on_progress is not None:
                            try:
                                on_progress(done, total)
                            except Exception:  # progress must never break download
                                pass
                if total and done < total:
                    raise ModelDownloadError(
                        "Загрузка прервалась. Нажмите «Скачать» ещё раз — она "
                        "продолжится с места остановки."
                    )
            return
        raise ModelDownloadError(
            f"Не удалось скачать {entry.filename}: временный файл повреждён и не "
            "восстановился. Попробуйте ещё раз."
        )

    # ── import / delete ───────────────────────────────────────────────

    def import_from_disk(
        self, src: Path | str, task: str = "chat", target_name: str | None = None,
        source: str = "disk",
    ) -> InstalledModel:
        src = Path(src)
        if not src.is_file() or src.suffix.lower() != ".gguf":
            raise ValueError(f"Не GGUF-файл: {src}")
        name = Path(target_name).name if target_name else src.name
        if not name.lower().endswith(".gguf"):
            name += ".gguf"
        target = self.models_dir / name
        if target.resolve() != src.resolve():
            shutil.copy2(src, target)
        self._write_sidecar(target, {"source": source, "task": task})
        return InstalledModel(
            filename=target.name, path=target, size_bytes=target.stat().st_size,
            source=source, task=task,
        )

    def import_from_ollama(self, name_filter: str | None = None) -> list[InstalledModel]:
        """Reuse existing Ollama blobs (same GGUFs) — no re-download."""
        root = _ollama_models_dir()
        manifests = root / "manifests"
        blobs = root / "blobs"
        if not manifests.is_dir() or not blobs.is_dir():
            return []
        imported: list[InstalledModel] = []
        for manifest in sorted(p for p in manifests.rglob("*") if p.is_file()):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict) or "layers" not in data:
                continue
            model_name = self._ollama_name(manifest, manifests)
            if name_filter and model_name != name_filter:
                continue
            layer = next(
                (l for l in data.get("layers", [])
                 if str(l.get("mediaType", "")).endswith("image.model")),
                None,
            )
            if layer is None:
                continue
            digest = str(layer.get("digest", ""))
            if not digest.startswith("sha256:"):
                continue
            blob = blobs / ("sha256-" + digest.split(":", 1)[1])
            if not blob.is_file() or not self._is_gguf(blob):
                continue
            safe_name = model_name.replace(":", "_").replace("/", "_")
            target = self.models_dir / f"{safe_name}.gguf"
            if not target.exists():
                shutil.copy2(blob, target)
            self._write_sidecar(target, {"source": f"ollama:{model_name}", "task": "chat"})
            imported.append(
                InstalledModel(
                    filename=target.name, path=target, size_bytes=target.stat().st_size,
                    source=f"ollama:{model_name}", task="chat",
                )
            )
        return imported

    def delete(self, filename: str) -> bool:
        target = self.models_dir / filename
        if not target.is_file():
            return False
        target.unlink()
        self._sidecar_path(target).unlink(missing_ok=True)
        return True

    # ── internals ─────────────────────────────────────────────────────

    @staticmethod
    def _ollama_name(manifest: Path, manifests_root: Path) -> str:
        rel = manifest.relative_to(manifests_root)
        parts = list(rel.parts)
        if len(parts) >= 3 and parts[0] == "registry.ollama.ai":
            parts = parts[1:]  # drop host
        tag = parts[-1]
        stem = parts[-2] if len(parts) >= 2 else tag
        return f"{stem}:{tag}" if "." not in tag and ":" not in tag else stem

    @staticmethod
    def _is_gguf(path: Path) -> bool:
        try:
            with open(path, "rb") as fh:
                return fh.read(4) == _GGUF_MAGIC
        except OSError:
            return False

    @staticmethod
    def _sidecar_path(gguf: Path) -> Path:
        return gguf.with_suffix(gguf.suffix + ".json")

    def _sidecar(self, gguf: Path) -> dict:
        sp = self._sidecar_path(gguf)
        if not sp.is_file():
            return {}
        try:
            return json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_sidecar(self, gguf: Path, payload: dict) -> None:
        self._sidecar_path(gguf).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _file_sha256(path: Path, chunk: int = _CHUNK) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()
