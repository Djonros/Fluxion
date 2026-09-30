// Fluxion website: the moving point on the lemniscate, and download links
// pointing at the files of the latest GitHub release.
(() => {
  "use strict";

  // ── the fluxion: a point on Bernoulli's lemniscate and its tangent ──────
  const point = document.getElementById("point");
  const tangent = document.getElementById("tangent");
  const label = document.getElementById("tangent-label");
  const CX = 300, CY = 180, A = 250, ARROW = 62, LOOP_MS = 14000, T0 = 0.55;

  const at = (t) => {
    const d = 1 + Math.sin(t) ** 2;
    return [CX + (A * Math.cos(t)) / d, CY + (A * Math.sin(t) * Math.cos(t)) / d];
  };

  const place = (t) => {
    const [x, y] = at(t);
    const [x1, y1] = at(t - 1e-3);
    const [x2, y2] = at(t + 1e-3);
    const n = Math.hypot(x2 - x1, y2 - y1) || 1;
    const dx = (x2 - x1) / n, dy = (y2 - y1) / n;
    point.setAttribute("cx", x.toFixed(1));
    point.setAttribute("cy", y.toFixed(1));
    tangent.setAttribute("x1", x.toFixed(1));
    tangent.setAttribute("y1", y.toFixed(1));
    tangent.setAttribute("x2", (x + dx * ARROW).toFixed(1));
    tangent.setAttribute("y2", (y + dy * ARROW).toFixed(1));
    label.setAttribute("x", (x + dx * (ARROW + 20) - 8).toFixed(1));
    label.setAttribute("y", (y + dy * (ARROW + 20) + 10).toFixed(1));
  };

  if (point && tangent && label) {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0, start = 0;
    const tick = (now) => {
      if (!start) start = now;
      place(T0 + ((now - start) / LOOP_MS) * 2 * Math.PI);
      frame = requestAnimationFrame(tick);
    };
    const apply = () => {
      cancelAnimationFrame(frame);
      start = 0;
      if (reduced.matches) place(T0);
      else frame = requestAnimationFrame(tick);
    };
    reduced.addEventListener?.("change", apply);
    apply();
  }

  // ── downloads from the latest release ───────────────────────────────────
  const body = document.body;
  const repo = body.dataset.repo;
  if (!repo || !window.fetch) return;

  const size = (bytes) => {
    if (!bytes) return "";
    if (bytes < 1e9) return `${Math.round(bytes / 1e6)} МБ`;
    return `${(bytes / 1e9).toFixed(1).replace(".", ",")} ГБ`;
  };

  const newest = (assets, kind) => {
    const re = new RegExp(`^FluxionBrowser-${kind}-.*\\.7z$`, "i");
    return assets
      .filter((a) => re.test(a.name))
      .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0];
  };

  const setBuild = (kind, href, sizeText) => {
    document.querySelectorAll(`[data-asset="${kind}"]`).forEach((a) => { a.href = href; });
    const s = document.querySelector(`[data-size-for="${kind}"]`);
    if (s) s.textContent = sizeText;
  };

  const applyRelease = (release) => {
    const assets = Array.isArray(release.assets) ? release.assets : [];
    const version = String(release.tag_name || "").replace(/^v/i, "");
    const lite = newest(assets, "Lite");
    const full = newest(assets, "Full");

    if (lite) {
      setBuild("Lite", lite.browser_download_url, size(lite.size));
      const primary = document.getElementById("dl-primary");
      if (primary) primary.href = lite.browser_download_url;
      const note = document.getElementById("dl-primary-note");
      if (note) {
        note.textContent = `Версия ${version}, ${size(lite.size)}. Windows 10 и 11, 64 бит`;
      }
    }

    if (full) {
      setBuild("Full", full.browser_download_url, size(full.size));
    } else if (body.dataset.fullUrl) {
      setBuild("Full", body.dataset.fullUrl, body.dataset.fullSize || "");
    } else {
      // Full is larger than GitHub's 2 GB limit and may be hosted elsewhere.
      const button = document.querySelector('[data-asset="Full"]');
      if (button) {
        button.textContent = "Страница выпуска";
        button.href = release.html_url || body.dataset.releases;
      }
    }
  };

  fetch(`https://api.github.com/repos/${repo}/releases/latest`, {
    headers: { Accept: "application/vnd.github+json" },
  })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then(applyRelease)
    .catch(() => {
      // Links already point at the latest release page: nothing to do.
    });
})();
