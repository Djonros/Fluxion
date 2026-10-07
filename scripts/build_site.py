"""Build the Fluxion website (landing page with downloads) into a folder.

    python scripts/build_site.py                # -> _site/
    python scripts/build_site.py --out public

The model table comes from the app's own catalog (core/model_manager.py) and
the training presets from presets/training/*.json, so the site cannot drift
from the program. The documentation (mkdocs) is built separately into
<out>/docs by the Pages workflow.

No third-party packages are needed: modules are loaded by file path, and the
catalog's HTTP dependency (used only for downloads) is stubbed if missing.
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import re
import shutil
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBSITE = ROOT / "website"
PRESETS_DIR = ROOT / "presets" / "training"
DEFAULT_OUT = ROOT / "_site"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_catalog() -> list:
    try:
        import httpx  # noqa: F401
    except ImportError:  # only needed for downloading, not for the catalog
        sys.modules["httpx"] = types.ModuleType("httpx")
    return list(_load_module("_fluxion_model_manager", ROOT / "core" / "model_manager.py").MODEL_CATALOG)


def load_presets() -> list:
    presets_mod = _load_module("_fluxion_presets", ROOT / "finetune" / "presets.py")
    presets = [presets_mod.load_preset(p) for p in sorted(PRESETS_DIR.glob("*.json"))]
    order = {"economy": 0, "standard": 1, "quick-check": 2}
    return sorted(presets, key=lambda p: (order.get(p.id, 99), p.id))


def app_version() -> str:
    text = (ROOT / "desktop_browser" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text).group(1)


# ── formatting ───────────────────────────────────────────────────────────────

def gigabytes(size_bytes: int) -> str:
    return f"{size_bytes / 1_000_000_000:.1f}".replace(".", ",") + " ГБ"


def hf_url(repo_id: str, filename: str) -> str:
    return f"https://huggingface.co/{repo_id}/resolve/main/{filename}"


def model_rows(catalog: list) -> str:
    rows = []
    for m in sorted(catalog, key=lambda m: getattr(m, "pro", False)):  # free first
        name = m.name.split(" — ")[0]
        purpose = getattr(m, "summary", "") or ("Поиск по коду проекта (RAG)" if m.task == "embedding" else "Чат и агент")
        vram = f"от {m.vram_gb:g} ГБ" if m.vram_gb else "не нужна"
        if getattr(m, "pro", False):
            name_cell = f'{html.escape(name)} <span class="pro">Pro</span>'
            file_cell = '<span class="via-app">в программе, с лицензией Pro</span>'
        else:
            name_cell = html.escape(name)
            file_cell = (
                f'<a href="{html.escape(hf_url(m.repo_id, m.filename), quote=True)}" '
                f'download>{html.escape(m.filename)}</a>'
            )
        rows.append(
            "<tr>"
            f'<th scope="row">{name_cell}</th>'
            f"<td>{html.escape(purpose)}</td>"
            f'<td class="num">{gigabytes(m.size_bytes)}</td>'
            f'<td class="num">{vram}</td>'
            f"<td>{file_cell}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def preset_rows(presets: list) -> str:
    rows = []
    for p in presets:
        s = p.settings
        params = []
        if "max_seq_length" in s:
            params.append(f"контекст {s['max_seq_length']}")
        if "num_train_epochs" in s:
            epochs = s["num_train_epochs"]
            params.append(f"{epochs} {'эпоха' if epochs == 1 else 'эпохи' if epochs < 5 else 'эпох'}")
        if p.max_samples:
            params.append(f"{p.max_samples} примеров")
        if "lora_r" in s:
            params.append(f"LoRA r={s['lora_r']}")
        rows.append(
            "<tr>"
            f'<th scope="row">{html.escape(p.name)}</th>'
            f"<td>{html.escape(p.description)}</td>"
            f'<td class="num">от {p.min_vram_gb:g} ГБ</td>'
            f"<td>{html.escape(', '.join(params))}</td>"
            f'<td><a href="presets/{html.escape(p.id)}.json" download>{html.escape(p.id)}.json</a></td>'
            "</tr>"
        )
    return "\n".join(rows)


def lemniscate_path(cx: float = 300, cy: float = 180, a: float = 250, steps: int = 240) -> str:
    """Bernoulli's lemniscate (the ∞ sign) as an SVG path; the same formula
    drives the moving point in site.js."""
    import math

    points = []
    for i in range(steps + 1):
        t = 2 * math.pi * i / steps
        d = 1 + math.sin(t) ** 2
        points.append((cx + a * math.cos(t) / d, cy + a * math.sin(t) * math.cos(t) / d))
    head = f"M{points[0][0]:.1f} {points[0][1]:.1f}"
    return head + "".join(f" L{x:.1f} {y:.1f}" for x, y in points[1:]) + " Z"


def render(template: str, values: dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"template placeholder without value: {key}")
        return values[key]

    out = re.sub(r"\{\{\s*([A-Z_]+)\s*\}\}", repl, template)
    return out


# ── pricing ──────────────────────────────────────────────────────────────────

ORDER_SUBJECT = "Fluxion Pro — заявка на лицензию"
ORDER_BODY = (
    "Здравствуйте! Хочу купить лицензию Fluxion Pro.\n\n"
    "Почта для ключа: \n"
    "Способ оплаты (перевод или счёт): \n"
)


def format_price(amount: int) -> str:
    """Return a ruble price like ``1 990 ₽`` with non-breaking spaces."""
    return f"{amount:,}".replace(",", "\u00a0") + "\u00a0₽"


def order_link(email: str) -> str:
    """Return a ``mailto:`` link that opens a pre-filled order letter."""
    from urllib.parse import quote

    return f"mailto:{email}?subject={quote(ORDER_SUBJECT)}&body={quote(ORDER_BODY)}"


def pricing_values(pricing: dict) -> dict[str, str]:
    """Return the template values of the price section from ``site.json``."""
    pro = int(pricing["pro_price"])
    launch = int(pricing.get("launch_price") or 0)
    discounted = 0 < launch < pro
    if discounted:
        price_html = (
            f'<s class="price-old">{html.escape(format_price(pro))}</s> '
            f'<strong class="price">{html.escape(format_price(launch))}</strong>'
        )
        note = f"Стартовая цена: {pricing.get('launch_note', '').strip()}. Обычная цена — {format_price(pro)}."
    else:
        price_html = f'<strong class="price">{html.escape(format_price(pro))}</strong>'
        note = ""
    return {
        "PRO_PRICE_HTML": price_html,
        "PRO_PRICE_NOTE": html.escape(note),
        "RENEWAL_PRICE": html.escape(format_price(int(pricing["renewal_price"]))),
        "ORDER_URL": html.escape(order_link(pricing["contact_email"]), quote=True),
        "CONTACT_EMAIL": html.escape(pricing["contact_email"]),
    }


# ── build ────────────────────────────────────────────────────────────────────

def build(out: Path) -> Path:
    config = json.loads((WEBSITE / "site.json").read_text(encoding="utf-8"))
    catalog = load_catalog()
    presets = load_presets()
    repo = config["repo"]
    releases = f"https://github.com/{repo}/releases"

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "presets").mkdir()
    (out / "data").mkdir()

    for name in ("styles.css", "site.js"):
        shutil.copy2(WEBSITE / name, out / name)
    shutil.copy2(ROOT / "assets" / "icon-256.png", out / "favicon.png")
    shutil.copy2(ROOT / "assets" / "banner.png", out / "og-image.png")
    for preset in presets:
        (out / "presets" / f"{preset.id}.json").write_text(
            json.dumps(preset.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    catalog_data = {
        "app_version": app_version(),
        "models": [
            {
                "id": m.model_id, "name": m.name, "task": m.task, "default": m.default,
                "pro": getattr(m, "pro", False), "summary": getattr(m, "summary", ""),
                "size_bytes": m.size_bytes, "vram_gb": m.vram_gb, "filename": m.filename,
                **({} if getattr(m, "pro", False) else {"url": hf_url(m.repo_id, m.filename)}),
            }
            for m in catalog
        ],
        "presets": [
            {"id": p.id, "name": p.name, "min_vram_gb": p.min_vram_gb, "url": f"presets/{p.id}.json"}
            for p in presets
        ],
    }
    (out / "data" / "catalog.json").write_text(
        json.dumps(catalog_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    values = {
        "REPO": html.escape(repo, quote=True),
        "RELEASES_URL": html.escape(releases, quote=True),
        "LATEST_URL": html.escape(f"{releases}/latest", quote=True),
        "REPO_URL": html.escape(f"https://github.com/{repo}", quote=True),
        "FULL_URL": html.escape(config.get("full_download_url", ""), quote=True),
        "FULL_SIZE": html.escape(config.get("full_size_text", ""), quote=True),
        "DOCS_URL": html.escape(config.get("docs_url", "docs/"), quote=True),
        "APP_VERSION": html.escape(app_version()),
        "MODEL_ROWS": model_rows(catalog),
        "PRESET_ROWS": preset_rows(presets),
        "SITE_URL": html.escape(config.get("site_url", ""), quote=True),
        "LEMNISCATE_PATH": lemniscate_path(),
        **pricing_values(config["pricing"]),
    }
    template = (WEBSITE / "index.html").read_text(encoding="utf-8")
    (out / "index.html").write_text(render(template, values), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is on GitHub Pages
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    out = build(Path(args.out).resolve())
    print(f"Сайт собран: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
