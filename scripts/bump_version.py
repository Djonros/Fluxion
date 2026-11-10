"""Raise the version of Fluxion for a release (fluxion-version.bat).

    python scripts/bump_version.py 0.29.0 [--date 2026-11-10] [--dry-run]

Changes, keeping the encoding and the line endings of each file:

- ``desktop_browser/__init__.py`` — ``APP_VERSION``;
- ``licensing/release.py`` — ``RELEASE_DATE`` (the update period of a licence counts from it);
- ``CHANGELOG.md`` — the section «## [Unreleased]» becomes «## [x.y.z] — date»;
- ``RELEASE_NOTES_RU.md`` — the notes of the previous version give way to a draft of this one,
  the points of the CHANGELOG to be rewritten for users; the closing part (how to update,
  how to buy Pro) stays.

No commit, no tag: the batch file shows the diff and asks.
Exit codes: 0 done, 2 a version that is not x.y.z or not greater than the current one,
3 no «## [Unreleased]» with something in it.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
APP_VERSION_RE = re.compile(r'APP_VERSION = "([^"]+)"')
RELEASE_DATE_RE = re.compile(r'RELEASE_DATE = "[^"]+"')
UNRELEASED = "## [Unreleased]"
NOTES_TAIL = "Программа одна, со всеми инструментами"


class BumpError(RuntimeError):
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


def parse(version: str) -> tuple[int, int, int]:
    match = VERSION_RE.match(version.strip())
    if not match:
        raise BumpError(f"Версия «{version}» не в виде x.y.z, например 0.29.0", 2)
    return tuple(int(p) for p in match.groups())


def _read(path: Path) -> tuple[str, str]:
    text = path.read_bytes().decode("utf-8")
    return text, ("\r\n" if "\r\n" in text else "\n")


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def current_version(root: Path = ROOT) -> str:
    text, _ = _read(root / "desktop_browser" / "__init__.py")
    match = APP_VERSION_RE.search(text)
    if not match:
        raise BumpError("В desktop_browser/__init__.py нет APP_VERSION", 2)
    return match.group(1)


def unreleased_points(changelog: str) -> list[str]:
    """The points of «## [Unreleased]», each on one line, without the leading «- »."""
    start = changelog.find(UNRELEASED)
    if start < 0:
        return []
    body = changelog[start + len(UNRELEASED):]
    end = body.find("\n## [")
    body = body if end < 0 else body[:end]
    return [line.strip()[2:].strip() for line in body.splitlines() if line.strip().startswith("- ")]


def notes_draft(version: str, points: list[str], old_notes: str, nl: str) -> str:
    """A draft of RELEASE_NOTES_RU.md: the heading, the points to rewrite, the tail of the old notes."""
    head = [f"# Fluxion {version} — заголовок выпуска", "",
            "<!-- Заготовка: перепишите пункты понятным языком для пользователей, по образцу прошлых выпусков. -->",
            ""]
    for point in points:
        head += [f"- {point}", ""]
    tail_at = old_notes.find(NOTES_TAIL)
    tail = old_notes[tail_at:] if tail_at >= 0 else ""
    return nl.join(head) + tail.replace("\r\n", "\n").replace("\n", nl)


def bump(version: str, today: str | None = None, root: Path = ROOT, dry_run: bool = False) -> list[str]:
    """Raise the version to *version*; the changed files (relative), nothing written with *dry_run*."""
    new = parse(version)
    old = current_version(root)
    if new <= parse(old):
        raise BumpError(f"Версия {version} не больше текущей {old}", 2)
    today = today or date.today().isoformat()

    changelog_path = root / "CHANGELOG.md"
    changelog, nl = _read(changelog_path)
    points = unreleased_points(changelog)
    if not points:
        raise BumpError("В CHANGELOG.md нет раздела «## [Unreleased]» с пунктами — нечего выпускать", 3)

    changes: dict[Path, str] = {}
    init_path = root / "desktop_browser" / "__init__.py"
    text, _ = _read(init_path)
    changes[init_path] = APP_VERSION_RE.sub(f'APP_VERSION = "{version}"', text, count=1)
    release_path = root / "licensing" / "release.py"
    text, _ = _read(release_path)
    changes[release_path] = RELEASE_DATE_RE.sub(f'RELEASE_DATE = "{today}"', text, count=1)
    changes[changelog_path] = changelog.replace(UNRELEASED, f"## [{version}] — {today}", 1)
    notes_path = root / "RELEASE_NOTES_RU.md"
    if notes_path.is_file():
        notes, notes_nl = _read(notes_path)
        changes[notes_path] = notes_draft(version, points, notes, notes_nl)

    if not dry_run:
        for path, text in changes.items():
            _write(path, text)
    return [path.relative_to(root).as_posix() for path in changes]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Поднять версию Fluxion")
    parser.add_argument("version", help="новая версия, например 0.29.0")
    parser.add_argument("--date", help="дата выпуска ГГГГ-ММ-ДД (по умолчанию сегодня)")
    parser.add_argument("--dry-run", action="store_true", help="только проверить, ничего не менять")
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        old = current_version(Path(args.root))
        files = bump(args.version, args.date, Path(args.root), args.dry_run)
    except BumpError as exc:
        print(f"[ОШИБКА] {exc}")
        return exc.code
    print(f"Версия {old} -> {args.version}" + (" (проверка, файлы не изменены)" if args.dry_run else ""))
    for name in files:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
