# Fluxion — Roadmap v2.0: Polish & Launch

Author: Djonros `<djonros@gmail.com>`
Status: Draft v2.0 — 2026-09-09
Supersedes: ROADMAP.md v1.0 (2026-08-06)
Goal: take the rewritten Fluxion desktop app (external review: 8/10) to public
launch — kill silent degradation, split the distribution, ship docs & video,
launch the site with fanfare.

## What changed since v1.0 (the rewrite)

- Fluxion is now a **desktop app** (Windows, single exe, full GUI) — the rich REPL retired with honors.
- Absorbed from v1: Phase 8 (write/edit tools), Phase 11 (git tools + auto-checkpoint
  before first write), part of Phase 12 (model selection via Ollama),
  Phase 9 (open-core monetization: agent writes gated by Pro license).
- Phase 10 (VS Code extension) superseded by the native GUI — moved to post-launch backlog.
- New capabilities not present in v1: built-in browser (web_search results open in-app),
  chat memory with session restore, QLoRA training with auto-installed dependencies,
  644 automated tests (incl. offscreen GUI + exe smoke).

## Current state (post-rewrite baseline)

| Capability | Status |
| --- | --- |
| Desktop app, Windows single exe, full GUI | ✅ Shipped |
| Chat with LLM via Ollama (model picker in GUI) | ✅ Shipped |
| ReAct agent: read / grep / edit_file (exact match), run tests, git commit | ✅ Shipped |
| Auto git-checkpoint before first agent write | ✅ Shipped |
| RAG project indexing (Chroma) | ✅ Shipped |
| Web search (own SearXNG container) + built-in browser | ✅ Shipped |
| QLoRA training, deps auto-installed, custom dataset | ✅ Shipped |
| Chat memory (sessions persisted, last chat restored on startup) | ✅ Shipped |
| 644 automated tests (offscreen GUI, exe smoke) | ✅ Shipped |
| External review: 8/10 | ✅ Received |
| Onboarding & dependency status UI | ✅ Shipped |
| Lightweight distribution (Lite without torch) | ✅ Shipped — installer 130 MB |
| Docs & quick-start video | ❌ Missing — important |
| Launch website | ❌ Missing — important |

## Phase 14 — Onboarding & "Zero Silence"

Priority: P0 — the #1 issue from the review ("features silently degrade").
Estimated effort: 2–3 days

### 14.1 Dependency status dashboard
- At startup and in the status bar: Ollama / model / Docker / SearXNG / project index.
- States ✅ ⚠️  + "Fix" button (step-by-step instructions or auto-fix).

### 14.2 The Golden Rule of Silence
- Every unavailable feature shows an in-chat banner with reason and remedy.
- Silence is banned at code-review level.

### 14.3 First-run wizard
- Dependency check → model pull with progress bar → test prompt at the end.

### 14.4 Crash logs
- Local crash log + opt-in "send report" button.

### 14.5 Tests & acceptance
- Launch without Docker → user immediately sees why web search is unavailable and what to do.
- Wizard passes on a clean machine (friend's laptop) without author's help.

## Phase 15 — Fluxion Lite & Training Pack

Priority: P1 — distribution weight is the #2 review complaint.
Estimated effort: 2–3 days
Status (2026-09-10): 15.1 ✅ (`fluxion-desktop-browser-lite.spec`, `scripts/build_lite.ps1`,
artifact 130 MB, exe smoke-tested incl. runtime RAG check in frozen exe —
`FLUXION_SMOKE_RAG` OK); 15.2 ✅ (`install_training_environment` in
`desktop_browser/training.py`); 15.3 ✅ (offline wheel-pack mode —
`--no-index --find-links` via `find_offline_wheels`/`FLUXION_OFFLINE_WHEELS`;
`scripts/build_full.ps1`: Full 7z **2824 MB** = Lite + `training_pack\wheels`
(92 wheels, torch 2.12.1+cu126 + unsloth 2026.9.4, constraint-pinned);
offline dry-run resolve validated with py3.12 (89 pkgs, CUDA torch);
site Download Lite/Full buttons; torch pin shared app↔script via test);
15.4 — weight ✅, clean-machine test pending.

### 15.1 Build split
- Core exe (Lite): chat + agent + RAG + browser. No torch.
- Training pack: torch stack downloaded on demand.

### 15.2 On-demand install UX
- "I want to train a model" button → download with progress bar → activation.
- Repeatable and recoverable after interrupted download.

### 15.3 Two artifacts
- Releases and site ship Lite and Full builds. ✅ Full 7z 2824 MB (folder 3228 MB):
  Lite + `training_pack\wheels`, torch 2.12.1+cu126 (TORCH_PIN in `training.py`),
  offline install `--no-index --find-links`, site buttons index + /install.

### 15.4 Acceptance
- Lite installs and runs on a machine without CUDA. (smoke-tested on dev machine; clean-machine test pending)
- Lite under agreed weight limit ✅ — installer 130 MB < 200 MB (installed folder 500 MB, per agreed definition).

## Phase 16 — Docs & Quick-Start Video

Priority: P1 — part of the "perfect product" perception.
Estimated effort: 3–4 days (depends on Phase 14 for troubleshooting content)

### 16.1 Docs ✅
- Quick-start; guides: chat / agent / RAG / training;
  troubleshooting (every case from 14.2); FAQ.
- Done: quick-start.md rewritten app-first; guides/chat.md created; rag/agent/
  finetune/licensing got «В приложении» sections; troubleshooting.md covers all
  14.2 health cases + training env/GGUF/crash logs; faq.md added; nav updated.

### 16.2 Video (exactly 5 minutes) ✅ (script)
- 0:00 hook: "your code never leaves your machine"
- 0:40 install + first-run wizard
- 1:30 chat as an expert on your codebase (RAG)
- 2:30 agent: edits, tests, git checkpoint
- 3:40 training on your own dataset
- 4:40 finale: logo + "The derivative of your vibe."
- Script ready: `VIDEO_SCRIPT.md` (RU voiceover + EN subtitles, shot checklist).

### 16.3 Acceptance
- A friend reproduces the whole flow from docs alone, without calling you.

## Phase 17 — Launch Site & Release

Priority: P0 for launch — the fanfare stage.
Estimated effort: 3–5 days (depends on 14, 15, 16)

### 17.1 Site structure
- Hero: animated infinity-with-integral, headline "The derivative of your vibe."
  (RU: «Производная от твоего вайба.»), sub "Method of fluxions. Version 2026.",
  buttons [Download Lite] [Full with training] [Docs].
- Social proof: review quote — "8/10. A mature tool for those who want an AI code
  assistant without cloud or subscriptions."
- Three feature blocks: 🔒 fully local / 🤖 agent with safety net (git checkpoints) /
  🎓 training on your coding style.
- Embedded video + quick-start in three commands.
- Footer: GitHub, Discord, Apache 2.0.

### 17.2 Fanfare
- Confetti on first visit; console easter egg (`fluxion --launch`); launch-day countdown.

### 17.3 Brand consistency
- Palette: #0A0E17 / gradient #7B2FFF → #00E5FF / accent #B4FF39.
- Fonts: Space Grotesk (or Unbounded) + JetBrains Mono.
- Wordmark with flux-X; splash screen = infinity with integral.

### 17.4 Release checklist
- [ ] All four release criteria green (below)
- [ ] CHANGELOG.md entry, tag `v1.0-public`
- [ ] Announcements: Discord, GitHub Discussions, HN / Reddit posts

### 17.5 Launch day
- Confetti on, countdown off, fanfare literally.

## Release criteria (definition of done)

1. Clean machine passes the first-run wizard without author's help.
2. Zero silent degradations: every unavailable feature is visible and explainable.
3. 5–10 external testers complete the "chat → agent → commit" scenario.
4. Lite build within the agreed weight limit. ✅ 130 MB < 200 MB (2026-09-09)

All four true → launch. Regardless of feelings.

## Phase 18+ — Post-launch backlog

- LoRA adapter marketplace: `fluxion adapter install fastapi-pro` (curated + community).
- Self-improvement loop: export accepted edits + prompts to jsonl → QLoRA dataset
  ("the more you use it, the smarter it gets").
- Extra backends: llama.cpp (GGUF without Ollama), OpenAI-compatible API, vLLM.
- Free-tier write trial: limited writes with per-edit confirmation (funnel to Pro).
- VS Code extension (re-evaluate after launch metrics).
- Cross-platform: macOS / Linux builds.
- Enterprise tier: SSO, team-shared RAG index, analytics.
- Eval expansion: SWE-bench-lite, MBPP, per-project evals in CI.
- Community: Discord, Discussions, "good first issue" labels.

## Timeline summary

| Phase | Focus | Duration | Depends on |
| --- | --- | --- | --- |
| 14 | Onboarding & zero silence ✅ | 2–3 days | — |
| 15 | Lite + training pack ✅ | 2–3 days | — |
| 16 | Docs & video | 3–4 days | 14 |
| 17 | Site & release | 3–5 days | 14, 15, 16 |

Phases 14 and 15 run in parallel → 16 → 17. Total: ~2–2.5 weeks to launch.

## Decision points (resolved 2026-09-09)

- Lite weight limit: **< 200 MB** ✅
- Free/Pro boundary: **Free write trial** — limited writes with per-edit confirmation (funnel to Pro) ✅
- Site domain: **TBD** — to be purchased later; site work proceeds with a placeholder, domain wired at launch ✅
- Video language: **RU first with EN subtitles** ✅
- Crash telemetry: **yes, opt-in reporter in Phase 14** ✅
