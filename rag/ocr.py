"""Text of scans for the design archive: the OCR built into Windows 10/11.

Windows.Media.Ocr recognises printed text offline, in the languages installed in
Windows (Russian: «Параметры → Время и язык → Язык», the Russian language pack).
It adds nothing to the size of Fluxion. It is reached through PowerShell, one
process per batch of images; the pictures are prepared with Pillow first (a page
of a scanned PDF, a multipage TIFF, a too large scan scaled down).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
MAX_SIDE = 4000  # px: enough for text of a scan, inside the 10000 px limit of the OCR
MAX_SCAN_PIXELS = 300_000_000  # an A0 sheet at 400 dpi; a bigger picture is skipped
MIN_PAGE_CHARS = 40  # a PDF page with less text is taken for a scan
BATCH = 40  # images per PowerShell process
TIMEOUT_PER_IMAGE = 30  # s

_SCRIPT = r"""
param([string]$ListFile, [string]$OutFile, [string]$Lang)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
[void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
[void][Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($Lang))
$out = @{}
if ($engine -eq $null) { $out['__error__'] = "no OCR language $Lang" }
else {
  foreach ($path in [IO.File]::ReadAllLines($ListFile, [Text.Encoding]::UTF8)) {
    if (-not $path) { continue }
    try {
      $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
      $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
      $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
      $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
      $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
      $out[$path] = (($result.Lines | ForEach-Object { $_.Text }) -join "`n")
      $stream.Dispose()
    } catch { $out[$path] = '' }
  }
}
[IO.File]::WriteAllText($OutFile, ($out | ConvertTo-Json -Compress), [Text.Encoding]::UTF8)
"""


class OcrError(RuntimeError):
    """The OCR of Windows is not there (not Windows, no language pack)."""


def _powershell(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
                          capture_output=True, timeout=timeout, creationflags=flags)


@lru_cache(maxsize=4)
def available(lang: str = "ru") -> bool:
    """Can Windows recognise text in *lang* here?"""
    if sys.platform != "win32":
        return False
    probe = ("[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime];"
             "[Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag }")
    try:
        done = _powershell(["-Command", probe], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    tags = done.stdout.decode("ascii", "ignore").split()
    return any(t.lower().split("-")[0] == lang for t in tags)


def recognize(images: list[Path], lang: str = "ru") -> dict[str, str]:
    """{str(image): text} for prepared images (PNG/JPEG within the size limit)."""
    out: dict[str, str] = {}
    script_dir = Path(tempfile.mkdtemp(prefix="fluxion-ocr-"))
    try:
        script = script_dir / "ocr.ps1"
        script.write_text(_SCRIPT, encoding="utf-8-sig")
        for start in range(0, len(images), BATCH):
            batch = images[start: start + BATCH]
            listing, result = script_dir / "list.txt", script_dir / "out.json"
            listing.write_text("\n".join(str(p) for p in batch), encoding="utf-8")
            try:
                _powershell(["-File", str(script), str(listing), str(result), lang],
                            timeout=60 + TIMEOUT_PER_IMAGE * len(batch))
                data = json.loads(result.read_text(encoding="utf-8-sig"))
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                raise OcrError(f"распознавание текста Windows не сработало: {exc}") from exc
            if isinstance(data, dict) and data.get("__error__"):
                raise OcrError(f"в Windows нет распознавания для языка «{lang}»")
            out.update({k: v or "" for k, v in (data or {}).items()})
    finally:
        for f in script_dir.glob("*"):
            f.unlink(missing_ok=True)
        script_dir.rmdir()
    return out


def _prepared(image, folder: Path, name: str) -> Path:
    """A picture as the OCR wants it: grayscale PNG, the long side at most MAX_SIDE."""
    image = image.convert("L")
    if max(image.size) > MAX_SIDE:
        scale = MAX_SIDE / max(image.size)
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
    path = folder / f"{name}.png"
    image.save(path)
    return path


def image_pages(path: Path, folder: Path) -> list[Path]:
    """The pages of an image file (a multipage TIFF has several) ready for the OCR."""
    from PIL import Image, ImageSequence

    pages = []
    limit, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, MAX_SCAN_PIXELS  # an A0 scan is big, not a bomb
    try:
        with Image.open(path) as image:
            image.draft("L", (MAX_SIDE, MAX_SIDE))  # a JPEG is decoded already scaled down
            for i, frame in enumerate(ImageSequence.Iterator(image), 1):
                pages.append(_prepared(frame.copy(), folder, f"p{i}"))
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise OcrError(f"{path.name}: картинка не открывается ({exc})") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = limit
    return pages


def pdf_scan_pages(path: Path, folder: Path) -> dict[int, Path]:
    """{page number: picture} for the pages of a PDF that have no text but a picture (a scan)."""
    from pypdf import PdfReader

    out = {}
    reader = PdfReader(str(path))
    for number, page in enumerate(reader.pages, 1):
        try:
            if len((page.extract_text() or "").strip()) >= MIN_PAGE_CHARS:
                continue
            images = list(page.images)
        except Exception:
            continue
        if images:
            biggest = max(images, key=lambda im: im.image.width * im.image.height if im.image else 0)
            if biggest.image is not None:
                out[number] = _prepared(biggest.image, folder, f"p{number}")
    return out


def text_of_image(path: Path, lang: str = "ru") -> str:
    """The text of a scan saved as a picture, "[Страница N]" before every page of a TIFF."""
    folder = Path(tempfile.mkdtemp(prefix="fluxion-scan-"))
    try:
        pages = image_pages(path, folder)
        texts = recognize(pages, lang)
        if len(pages) == 1:
            return texts.get(str(pages[0]), "")
        return "\n\n".join(f"[Страница {i}]\n{texts.get(str(p), '')}" for i, p in enumerate(pages, 1))
    finally:
        _clean(folder)


def text_of_pdf_scans(path: Path, text: str, lang: str = "ru") -> str:
    """The text of a PDF with its scanned pages read by the OCR: the page marks of
    rag/documents.py ("[Страница N]") get the recognised text."""
    import re

    folder = Path(tempfile.mkdtemp(prefix="fluxion-scan-"))
    try:
        scans = pdf_scan_pages(path, folder)
        if not scans:
            return text
        texts = recognize(list(scans.values()), lang)
        pages: dict[int, str] = {}
        marks = list(re.finditer(r"\[Страница (\d+)\]\n?", text))
        for i, mark in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            pages[int(mark.group(1))] = text[mark.end():end].strip()
        for number, picture in scans.items():
            recognised = texts.get(str(picture), "").strip()
            if recognised:
                pages[number] = f"{pages.get(number, '')}\n{recognised}".strip()
        return "\n\n".join(f"[Страница {n}]\n{t}" for n, t in sorted(pages.items()) if t)
    finally:
        _clean(folder)


def _clean(folder: Path) -> None:
    for f in folder.glob("*"):
        try:
            f.unlink()
        except OSError:
            pass
    try:
        os.rmdir(folder)
    except OSError:
        pass
