"""The website is generated from the app's catalog and preset files."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.model_manager import MODEL_CATALOG, hf_download_url
from finetune.presets import load_preset

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(tmp_path_factory.mktemp("site") / "out")


def test_page_is_complete(site):
    page = (site / "index.html").read_text(encoding="utf-8")
    assert "{{" not in page and "}}" not in page
    for name in ("styles.css", "site.js", "favicon.png", "og-image.png", ".nojekyll"):
        assert (site / name).exists(), name
    assert 'lang="ru"' in page


def test_every_catalog_model_is_listed(site):
    """Free models link to the file; Pro models point to the app (licence)."""
    page = (site / "index.html").read_text(encoding="utf-8")
    for model in MODEL_CATALOG:
        url = hf_download_url(model.repo_id, model.filename)
        if model.pro:
            assert url not in page
            assert model.name.split(" — ")[0] in page
        else:
            assert url in page and model.filename in page
    assert page.count('class="pro"') == sum(1 for m in MODEL_CATALOG if m.pro)


def test_every_preset_is_published_and_valid(site):
    page = (site / "index.html").read_text(encoding="utf-8")
    sources = sorted((ROOT / "presets" / "training").glob("*.json"))
    assert sources
    for source in sources:
        published = site / "presets" / source.name
        assert f'href="presets/{source.name}"' in page
        assert load_preset(published) == load_preset(source)


def test_internal_links_resolve(site):
    page = (site / "index.html").read_text(encoding="utf-8")
    for href in re.findall(r'(?:href|src)="([^"]+)"', page):
        if href.startswith(("http", "#", "docs/", "mailto:")) or not href:
            continue
        assert (site / href).exists(), href


def test_catalog_json(site):
    data = json.loads((site / "data" / "catalog.json").read_text(encoding="utf-8"))
    assert {m["id"] for m in data["models"]} == {m.model_id for m in MODEL_CATALOG}
    assert data["presets"] and all((site / p["url"]).is_file() for p in data["presets"])


def test_site_repo_matches_update_check():
    """Download buttons and the in-app update check must use the same repo."""
    from core.update import DEFAULT_RELEASES_URL

    repo = json.loads((ROOT / "website" / "site.json").read_text(encoding="utf-8"))["repo"]
    assert f"/repos/{repo}/releases" in DEFAULT_RELEASES_URL


def test_lemniscate_path():
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_site2", ROOT / "scripts" / "build_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = module.lemniscate_path()
    points = re.findall(r"[ML]([\d.]+) ([\d.]+)", path)
    xs = [float(x) for x, _ in points]
    assert path.startswith("M") and path.endswith("Z") and len(points) > 100
    assert abs(min(xs) - 50) < 1 and abs(max(xs) - 550) < 1


def test_page_text_escaped(site, monkeypatch):
    """Catalog text goes through html.escape."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_site3", ROOT / "scripts" / "build_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fake = type("M", (), dict(name="<b>x</b>", task="chat", default=False, vram_gb=0, pro=False,
                              summary="<i>y</i>", size_bytes=1, repo_id="a/b", filename="f.gguf"))
    rows = module.model_rows([fake])
    assert "<b>x</b>" not in rows and "&lt;b&gt;x&lt;/b&gt;" in rows
    assert "<i>y</i>" not in rows
