# Changelog

All notable changes to Fluxion will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed — Default backend
- Without an explicit `backend`, the embedded llama.cpp engine is selected when llama-cpp-python is installed and a local GGUF model exists (`gguf_path`, `FLUXION_GGUF_PATH` or the chat model installed via the "Models" page); previously the default was always Ollama, contradicting the docs ("llama.cpp by default in builds"), so the app failed to pick up a downloaded model. Explicit `backend:`/`FLUXION_BACKEND` still wins. Factory tests made hermetic against models on the developer machine.
- `RELEASE_NOTES_RU.md` — all changes of this release in plain Russian.

### Fixed — Agent benchmark model lookup
- The benchmark resolves the model like the desktop app (llama.cpp + model installed via the "Models" page, no `gguf_path` needed), prints the engine and model in use, and explains how to configure the backend instead of a traceback.

### Fixed — Checkpoint snapshot race (found by fluxion-test.bat on Windows)
- The snapshot index was a copy with a fresh mtime, which disabled git's "racy clean" re-check: a same-size edit made in the same second as the index write was read from the stale stat cache, so a checkpoint could record old content and rollback could silently skip a file. The copy now keeps the original mtime (`shutil.copy2`). Deterministic regression test added.

### Added — Test runner
- `fluxion-test.bat` — menu and CLI modes (`quick`, `groups`, `ci`, `smoke`, `bench`): tests run in groups (agent, core, server, desktop, licensing, training) with a pre-check of required packages, so a missing PySide6/FastAPI shows as "skipped" instead of import errors; summary table, JUnit XML in `logs/tests/`, exit code for automation; smoke and full agent benchmark on the configured model with resume.
- `logs/` and `results/` are git-ignored.

### Added — Updater
- `fluxion-update.bat` — updates the program from a release zip: code backup (models/data/venv excluded), extraction, copy-over that keeps `config.yaml`/`repos.yaml`/`continue_config.json`, post-update validation and tests, one-step rollback (`--rollback`).

### Added — Agent benchmark
- `eval/agent_bench/` — 36 realistic agent tasks (qa, chat, create, fix, refactor, safety) with automatic post-run checks, anti-cheat (tests must stay unchanged) and a mutation check for test writing. Modes: `baseline` (frozen pre-fix agent), `text`, `json`. Resumable runs, `report.md` with per-mode/category/task results and pairwise comparison. `--validate` proves each task is solvable and its checks reject the untouched fixture; `tests/test_agent_bench.py` runs an oracle through the real agent loop in all modes.

### Fixed — Agent reliability (production-readiness review)
- Final answer was cut to its first line (`Action: finish` parsed without DOTALL); multi-line answers with code are now kept intact.
- Failed tools (e.g. failing `run_tests`) returned `Error: ` with no text — the model never saw the pytest output. Observations now include error + output (tail of pytest output preserved).
- Repeat-guard blocked re-running tests / re-reading a file after `edit_file`; it now resets after every successful write.
- No stop sequences were sent, so the model invented its own `Observation:` and it was written into files. All backends now pass `stop`; the parser also cuts hallucinated observations. Markdown fences are stripped from `write_file`/`edit_file` content (DOGFOOD 18.8, t3).
- Missing pytest was reported as "no tests → PASSED". New verification status `syntax_only` is shown honestly in the UI.
- `edit_file` failed on CRLF files (Windows) and on trailing-whitespace differences; both are handled, line endings preserved.
- `LlamaCppBackend`/`APIBackend` ignored `generation` settings from config.yaml.
- Backups in `.fluxion-backup/` keep the pre-agent version and are excluded from git.

### Added — Structured tool calls
- JSON-schema constrained actions: `TOOL_SPECS` drives both the schema and the prompt; llama.cpp (`response_format` → GBNF grammar) and Ollama (`format`) enforce it, so small models cannot emit malformed actions. Default `auto` for these backends; `FLUXION_AGENT_FORMAT=json|text` overrides; `FLUXION_API_STRUCTURED=1` opts the API backend in.
- Automatic fallback to text ReAct in the same run when a backend rejects the schema; both reply formats are always understood.
- Cut-off JSON (max_tokens hit during a large write) is reported to the model instead of being accepted as a final answer.
- `search_code <query>` tool — semantic search over the existing RAG index (previously `rag_service` was passed to the agent but never used). Results are restricted to the current project, deduplicated and point to `read_file` ranges.
- Tools offered to the model follow the configuration (no write tools in read-only, no `search_code` without RAG, no `web_search` when not configured).

### Added — Agent
- `list_files [dir|glob]` tool; `read_file <path> <a>-<b>` paging for long files; `grep` searches all text files (not only `*.py`), skipping vendored/build dirs, binaries and secrets.
- History compaction when the conversation exceeds the context budget.
- `/rollback` CLI command.
- `allow_exec` flag (disables `run_tests`), used by the hosted server.

### Security
- **Data loss:** the auto-checkpoint used `git stash push -u`, which removed the user's uncommitted work from disk before the agent's first write. Checkpoints are now snapshot commits under `refs/fluxion/checkpoints/` that never touch the working tree or index; `rollback` restores them (and recovers legacy `fluxion-checkpoint` stashes).
- `git_commit` no longer runs `git add -A`: it commits only files the agent wrote (or the changed files when asked explicitly) and never `.env`/keys.
- `read_file`/`grep` could read any file on disk (absolute paths, `../`). Access is confined to the project; `.env`, keys and `.git` internals are not sent to the model. Web search results are marked as untrusted.
- `run_tests` rejects pytest options/paths that load foreign code or leave the project.
- `APIBackend` no longer falls back to `HF_TOKEN` as the provider API key.
- Server: `FLUXION_SERVER_MODE=saas` (default in the Docker image) — agent read-only, no test execution, project roots confined to `FLUXION_WORKSPACES_DIR/<user_id>`, server-side RAG indexing disabled (shared index).
- Server: JWT secret — startup fails in production without a strong `FLUXION_JWT_SECRET`; the public default is never used (ephemeral random secret in dev).
- Server: CORS no longer `*` with credentials; localhost/VS Code by default, `FLUXION_CORS_ORIGINS` for production.
- Local server (`fluxion-server.bat`, `fluxion-deploy.bat`) binds to `127.0.0.1` instead of `0.0.0.0`; `docker-compose.prod.yml` requires `POSTGRES_PASSWORD`.

### Added — Desktop App (Phase 15, in progress)
- `desktop/` package — PySide6 desktop GUI:
  - `app.py` — branded window (logo palette, dark/light theme with persistence via QSettings), sidebar navigation (Чат / Проект / Агент), chat page with suggestion chips
  - Streaming chat via `Assistant.ask_stream` in a `QThread` worker (`ChatWorker`), stop button, `[strategy | rag | web]` meta line per reply
  - Agent page: `CodingAgent.run_iter` streamed in `AgentWorker` — per-iteration Thought/Action/Observation log, max-iterations spinner, stop button, `agent_write` Pro gate on the write checkbox
  - License UI: sidebar badge (FREE/PRO, email tooltip) + `desktop/license_dialog.py` dialog — offline activate/deactivate via `licensing.store`, expiry display, error messages
  - Project page: folder picker (persisted in QSettings), background RAG indexing via `IndexWorker`, live chunk-count label, friendly completion/error log
  - `StatusWorker` — background backend availability check shown in the sidebar
  - `engine.py` — reuses CLI startup (multi-model Pro gate included); returns assistant + backend + settings + rag + web for chat and agent wiring
- `fluxion-desktop.bat` — launcher with dependency check/auto-install
- 24 offscreen tests in `tests/test_desktop.py` (streaming, history, stop, meta, no-engine fallback, theme persistence, agent steps/stop/gates, license dialog/badge, project indexing)
- `PySide6>=6.10` in requirements.txt

### Added — Desktop UI Themes
- Logo-inspired dark/light CLI themes (`cli/theme.py`) with purple/cyan Fluxion palette
- Styled `∞ Fluxion ∞` banner, prompt, status, success, warning, and error output
- `/theme dark|light` command with persisted selection in `data/ui-theme.json`
- `FLUXION_THEME` environment variable for the initial theme
- 8 theme tests in `tests/test_theme.py`

### Added — Phase 14: Desktop Licensing (Pro)
- `licensing/` package — offline Ed25519 license system (no server required):
  - `models.py` — frozen `License` (email, plan, issued_at, optional expires), wire format `b64(payload).b64(sig)` with compact sorted-JSON payload
  - `verifier.py` — signature verification against embedded public key (`PUBLIC_KEY_B64`); perpetual = no `expires`
  - `issuer.py` — owner-only issuance CLI: `python -m licensing.issuer --email --plan [--expires] [--key-path]`
  - `store.py` — local activation (`activate` / `load_activation` / `clear_activation`, `data/license.key`)
  - `gate.py` — `ProRequiredError`, `current_license()`, `feature_enabled()`, `ensure_pro()`
- Pro feature gates: `PRO_FEATURES = {agent_write, qlora, multi_model}` — `/agent --write`, API/llama.cpp backends (fallback to Ollama with warning), QLoRA training scripts; unknown features always free
- CLI `/license status | activate <key> | deactivate` in `cli/app.py`
- `cryptography>=42` in requirements.txt; dev keypair in `keys/` (gitignored)
- 64 tests in `tests/test_licensing.py` (model/wire/issue/verify, store, gates, CLI)
- Docs: `docs/guides/licensing.md`, USER_GUIDE §8, OWNER_GUIDE §6

### Added — Phase 11: Git Integration
- `git_status` tool — show modified/untracked files
- `git_diff` tool — show unstaged changes
- `git_commit` tool — stage all changes and commit with message (RW mode only)
- `orchestrator/git_helper.py` — thin subprocess wrapper (status, diff, commit, stash, checkpoint, rollback)
- Auto-checkpoint: first `write_file`/`edit_file` in an agent session creates a `git stash` (fluxion-checkpoint)
- Rollback support via `git_helper.rollback()` — pops latest checkpoint stash
- `git_status` and `git_diff` available in read-only mode; `git_commit` requires `--write`
- 25 tests (skipped when git not installed)

### Added — Phase 12: Multi-Model Support
- `APIBackend` (`core/api_backend.py`) — OpenAI-compatible API client (OpenRouter, Groq, Together, vLLM)
  - Supports generate + streaming via SSE
  - Configurable via `FLUXION_API_KEY`, `FLUXION_API_BASE_URL`, `FLUXION_API_MODEL`
- `LlamaCppBackend` (`core/llama_cpp_backend.py`) — direct GGUF inference via llama-cpp-python
  - No server process needed, loads model in-process
  - Configurable via `FLUXION_GGUF_PATH`, `FLUXION_N_GPU_LAYERS`
- `BackendFactory` (`core/backend_factory.py`) — auto-selects backend from config/env with fallback chain
  - Detection order: explicit `FLUXION_BACKEND` env → `FLUXION_API_KEY` present → default Ollama
  - Fallback chain: primary → ollama → api → llama_cpp
- Updated `config/config.yaml` with backend selection documentation
- Updated `config/.env.example` with all backend env vars
- 20 tests covering APIBackend, BackendFactory detection/fallback, LlamaCppBackend

### Added — Phase 13: Productization (in progress)
- Updated ROADMAP.md to v2.0 — all phases 8–12 marked complete
- CI/CD pipeline via GitHub Actions
- MBPP eval benchmark support

### Added — Phase 8: Agent Write Capabilities
- `write_file` tool — create or overwrite files with full content
- `edit_file` tool — precise snippet replacement via `---OLD---` / `---NEW---` sentinels
- `allow_write` parameter on `CodingAgent` (default `False` — read-only by default)
- Path validation: rejects path traversal, blocked directories (`.git/`, `.venv/`, etc.)
- Automatic backup to `.fluxion-backup/` before any file mutation
- File size guard: rejects writes exceeding 1 MB
- Dynamic system prompt: separate RO (read-only) and RW (read-write) variants
- Multiline response parsing for `write_file` and `edit_file` actions
- CLI `/agent --write <task>` flag to enable write mode
- 29 new tests covering write/edit tools, path safety, backups, and edge cases

### Added — Phase 10: VS Code Extension & API Server
- **FastAPI server** (`server/`) — wraps existing Fluxion engine:
  - `GET /api/health` — status, model, ollama, rag, web info
  - `POST /api/chat` — streaming SSE or JSON response
  - `POST /api/agent/run` — ReAct agent execution with `allow_write` flag
  - `POST /api/rag/index` — index a directory into ChromaDB
  - CORS middleware for browser/extension access
- **16 server tests** (`tests/test_server.py`) — all endpoints with mocked backends
- **VS Code extension** (`extension/`):
  - Chat sidebar with streaming, agent mode toggle
  - Commands: Open Chat, Index Project, Server Status, Run Agent
  - Activity bar icon, webview with VS Code theme integration
  - Settings: `fluxion.serverUrl`, `fluxion.agentAllowWrite`
  - Pure JavaScript — no TypeScript, no bundler
- `fluxion-server.bat` — one-click server launch

### Changed — Phase 9: License & Publication Prep
- License changed from MIT to **Apache License 2.0**
- Added `.gitignore` for Python, IDE, Fluxion runtime, models, and secrets
- Added `CONTRIBUTING.md` with development workflow and coding standards
- Added `CHANGELOG.md` (this file)

## [0.1.0-alpha] — 2026-08-11

### Added — Phases 1–7 (Initial Release)
- **Phase 1:** Ollama integration (`OllamaClient`, `OllamaBackend`), config system, streaming chat
- **Phase 2:** RAG pipeline — Python AST chunker, ChromaDB indexer, cosine retriever, flashrank reranking
- **Phase 3:** Query router (DIRECT / RAG / WEB strategy), prompt builder with budget management, assistant facade
- **Phase 4:** Web search — SearXNG integration, content fetcher, disk cache, pipeline orchestration
- **Phase 5:** Data pipeline — code quality filters, MinHash deduplication, ChatML formatting, repo cloner
- **Phase 6:** QLoRA fine-tuning — settings presets, ChatML formatting, dataset loader, sequence packing, Modelfile generation
- **Phase 7:** ReAct agent — 5 tools (`read_file`, `grep`, `run_tests`, `web_search`, `finish`), eval framework (HumanEval pass@1 = 1.0)
- REPL with rich live-streaming output
- Full test suite: 131 tests across 7 phase files
