"""Entry point: assemble Settings + backend + REPL and run."""
from __future__ import annotations

import sys
from pathlib import Path

from rich.markup import escape
from rich.panel import Panel

from cli.commands import CommandRegistry
from cli.render import Renderer
from cli.repl import REPL
from cli.theme import THEMES, load_theme, save_theme
from core.config import Settings
from core.inference import OllamaBackend
from licensing import feature_enabled
from orchestrator import Assistant, CodingAgent, Strategy
from rag.service import RAGConfig, RAGService
from web.pipeline import WebSearch


def build_registry(repl: REPL, backend: OllamaBackend, settings: Settings) -> CommandRegistry:
    reg = CommandRegistry()

    rag_cfg = RAGConfig.from_settings(settings.rag, ollama_host=settings.ollama_host)
    rag_service = RAGService(rag_cfg)
    repl.rag_service = rag_service

    web_search = WebSearch.from_settings(settings.web)
    repl.web_search = web_search

    assistant = Assistant(
        backend, settings,
        rag_service=rag_service,
        web_search=web_search,
    )
    repl.assistant = assistant

    # -- basic commands ---------------------------------------------------

    def _help(_args):
        from rich.markup import escape

        repl.renderer.console.print(escape(reg.help_text()))

    def _clear(_args):
        repl.reset()

    def _model(_args):
        repl.renderer.info(f"Model:   {settings.model}")
        repl.renderer.info(f"Ollama:  {settings.ollama_host}")
        avail = backend.is_available()
        (repl.renderer.success if avail else repl.renderer.warn)(
            f"available={'yes' if avail else 'no'}"
        )

    def _status(_args):
        alive = backend.client.is_alive()
        present = backend.client.exists()
        repl.renderer.info(f"Server alive: {alive}")
        repl.renderer.info(f"Model loaded: {present}")
        if alive:
            models = backend.client.list_models()
            repl.renderer.info(f"Installed models: {', '.join(models) or '(none)'}")
        chunk_count = _safe_count(rag_service)
        repl.renderer.info(f"RAG chunks indexed: {chunk_count}")
        repl.renderer.info(f"RAG override: {'ON (force)' if repl.rag_enabled else 'auto-route'}")
        searxng_alive = web_search.client.is_alive()
        repl.renderer.info(f"SearXNG: {searxng_alive} ({settings.web.searxng_url})")
        repl.renderer.info(f"Web cached pages: {web_search.cache.count()}")

    # -- RAG commands -----------------------------------------------------

    def _index(args):
        target = " ".join(args).strip() if args else str(settings.project_root())
        repl.renderer.info(f"Indexing: {target}")
        repl.renderer.info("Loading embedding model (first run may take a while)...")
        count = rag_service.index(target)
        repl.renderer.success(f"Indexed {count} chunks from {target}")
        repl.renderer.info("Auto-routing will now use RAG for code questions.")

    def _search(args):
        if not args:
            repl.renderer.error("Usage: /search <query>")
            return
        query = " ".join(args)
        if not _safe_count(rag_service):
            repl.renderer.warn("Index is empty. Run /index <path> first.")
            return
        results = rag_service.search(query, top_k=5)
        if not results:
            repl.renderer.info("No results found.")
            return
        for i, r in enumerate(results, 1):
            name = f"  [{r.name}]" if r.name else ""
            repl.renderer.info(
                f"[{i}] {r.file_path}{name}  "
                f"L{r.start_line}-{r.end_line}  "
                f"score={r.score:.3f}"
            )
            repl.renderer.console.print(r.content[:300], style="dim")
            if len(r.content) > 300:
                repl.renderer.console.print("...", style="dim")

    def _rag(args):
        if not args:
            repl.renderer.info(f"RAG override is {'ON (force RAG)' if repl.rag_enabled else 'OFF (auto-route)'}")
            repl.renderer.info(f"Chunks indexed: {_safe_count(rag_service)}")
            return
        action = args[0].lower()
        if action == "on":
            if not _safe_count(rag_service):
                repl.renderer.warn("Index is empty. Run /index <path> first.")
                return
            repl.rag_enabled = True
            repl.renderer.success("RAG override ON — all queries will use RAG context.")
        elif action == "off":
            repl.rag_enabled = False
            repl.renderer.info("RAG override OFF — auto-routing enabled.")
        elif action == "clear":
            rag_service.clear()
            repl.renderer.info("RAG index cleared.")
        else:
            repl.renderer.error("Usage: /rag [on|off|clear]")

    def _route(args):
        if not args:
            repl.renderer.error("Usage: /route <query>")
            return
        query = " ".join(args)
        sig = assistant.classify(query)
        repl.renderer.info(f"Query: {query}")
        repl.renderer.info(f"Strategy: {sig.strategy.value}  (confidence={sig.confidence:.2f})")
        repl.renderer.info(f"Scores: rag={sig.rag_score:.1f}  web={sig.web_score:.1f}  direct={sig.direct_score:.1f}")
        if sig.file_paths:
            repl.renderer.info(f"Files: {', '.join(sig.file_paths)}")
        if sig.identifiers:
            repl.renderer.info(f"Identifiers: {', '.join(sig.identifiers)}")

    def _web(args):
        if not args:
            repl.renderer.error("Usage: /web <query>")
            return
        query = " ".join(args)
        if not web_search.client.is_alive():
            repl.renderer.warn(
                f"SearXNG not reachable at {settings.web.searxng_url}.\n"
                "  Start it with:  powershell -File scripts/setup_searxng.ps1"
            )
            return
        repl.renderer.info(f"Searching web: {query}")
        contexts = web_search.run(query)
        if not contexts:
            repl.renderer.info("No web results found.")
            return
        for ctx in contexts:
            repl.renderer.info(
                f"[{ctx.citation_index}] {ctx.title}  {ctx.url}"
            )
            if ctx.content:
                preview = ctx.content[:200].replace("\n", " ")
                repl.renderer.console.print(f"    {preview}...", style="dim")
        repl.renderer.info(
            f"Use /web_ask or ask normally to get an answer with web context."
        )

    def _web_clear(args):
        count = web_search.cache.count()
        web_search.cache.clear()
        repl.renderer.info(f"Cleared {count} cached web pages.")

    # -- agent command ----------------------------------------------------

    def _agent(args):
        if not args:
            repl.renderer.error("Usage: /agent [--write] [--iter N] <task description>")
            return

        allow_write = False
        max_iter = 50

        while args and args[0].startswith("--"):
            if args[0] == "--write":
                allow_write = True
                args = args[1:]
            elif args[0] == "--iter" and len(args) > 1:
                try:
                    max_iter = int(args[1])
                except ValueError:
                    repl.renderer.error("--iter expects a number, e.g. --iter 15")
                    return
                args = args[2:]
            else:
                break

        if not args:
            repl.renderer.error("Usage: /agent [--write] [--iter N] <task description>")
            return

        if allow_write and not feature_enabled("agent_write"):
            repl.renderer.error(
                "Agent write mode is a Pro feature.\n"
                "  Activate a license with: /license activate <key>"
            )
            return

        task = " ".join(args)
        project_root = str(settings.project_root())

        repl.renderer.info(f"[agent] task: {task}")
        repl.renderer.info(f"[agent] project root: {project_root}")
        repl.renderer.info(f"[agent] max iterations: {max_iter}")
        if allow_write:
            repl.renderer.warn("[agent] WRITE MODE ENABLED — agent can modify files.")

        agent = CodingAgent(
            backend=backend,
            project_root=project_root,
            rag_service=rag_service,
            web_search=web_search,
            max_iterations=max_iter,
            allow_write=allow_write,
        )

        for step in agent.run_iter(task):
            repl.renderer.info(
                f"[agent] iter {step.iteration}  "
                f"thought: {step.thought[:120]}"
            )
            if step.action:
                repl.renderer.info(
                    f"[agent]   -> {step.tool_name} {step.tool_args}"
                )
            if step.observation:
                preview = escape(step.observation[:500])
                repl.renderer.console.print(
                    Panel(
                        preview,
                        title=f"Observation (iter {step.iteration})",
                        border_style="dim",
                    )
                )

        result = agent.last_result
        if result.success:
            repl.renderer.success(f"[agent] completed in {result.iterations_used} iterations.")
        else:
            repl.renderer.warn(f"[agent] did not complete ({result.iterations_used} iterations used).")

        if result.final_answer:
            repl.renderer.console.print()
            repl.renderer.console.print(
                Panel(
                    escape(result.final_answer),
                    title="Agent Result",
                    border_style="cyan",
                )
            )

    # -- adapter marketplace ----------------------------------------------

    def _adapters(args):
        from finetune import QLoRASettings
        from finetune.marketplace import (
            AdapterNotFoundError,
            AdapterRegistry,
            DuplicateAdapterError,
            InvalidAdapterError,
        )

        market = AdapterRegistry(f"{settings.paths.data_dir}/lora_registry")
        args = [a for a in args if a]

        if not args or args[0].lower() in ("list", "ls"):
            adapters = market.list()
            if not adapters:
                repl.renderer.info(f"No adapters registered (index: {market.index_path}).")
                repl.renderer.info("Register one after training: /adapters register <name>")
                return
            for a in adapters:
                marker = "*" if a.ollama_model == settings.model else " "
                repl.renderer.info(
                    f"{marker} {a.name:<24} -> {a.ollama_model}  "
                    f"[{a.quantize}]  base: {a.base_model}"
                )
                if a.description:
                    repl.renderer.console.print(f"    {a.description}", style="dim")
            repl.renderer.info(f"(* = active model: {settings.model})")
            return

        action = args[0].lower()

        if action == "info" and len(args) > 1:
            adapter = market.get(args[1])
            if adapter is None:
                repl.renderer.error(f"Adapter '{args[1]}' not found. See /adapters")
                return
            repl.renderer.info(f"Name:        {adapter.name}")
            repl.renderer.info(f"Base model:  {adapter.base_model}")
            repl.renderer.info(f"LoRA path:   {adapter.path}")
            repl.renderer.info(f"Ollama:      {adapter.ollama_model}  [{adapter.quantize}]")
            repl.renderer.info(f"Created:     {adapter.created_at}")
            if adapter.description:
                repl.renderer.console.print(escape(adapter.description), style="dim")
            return

        if action == "register" and len(args) > 1:
            name = args[1]
            rest = args[2:]
            ollama_model = ""
            path = ""
            quantize = ""
            while rest and rest[0].startswith("--"):
                flag = rest[0].lower()
                if flag in ("--model", "--path", "--quant") and len(rest) > 1:
                    if flag == "--model":
                        ollama_model = rest[1]
                    elif flag == "--path":
                        path = rest[1]
                    else:
                        quantize = rest[1]
                    rest = rest[2:]
                else:
                    break
            adapter = AdapterInfo.from_training(
                QLoRASettings(), name, " ".join(rest).strip()
            )
            if ollama_model:
                adapter.ollama_model = ollama_model
            if path:
                adapter.path = path
            if quantize:
                adapter.quantize = quantize
            try:
                market.register(adapter)
            except (DuplicateAdapterError, InvalidAdapterError) as exc:
                repl.renderer.error(str(exc))
                return
            repl.renderer.success(f"Registered adapter '{adapter.name}' -> {adapter.ollama_model}")
            if not Path(adapter.path).exists():
                repl.renderer.warn(f"LoRA path does not exist yet: {adapter.path}")
            return

        if action == "delete" and len(args) > 1:
            remove_files = "--files" in args
            if market.delete(args[1], remove_files=remove_files):
                repl.renderer.info(f"Deleted adapter '{args[1]}'.")
            else:
                repl.renderer.error(f"Adapter '{args[1]}' not found. See /adapters")
            return

        if action == "switch" and len(args) > 1:
            try:
                adapter = market.switch(args[1], settings, backend)
            except AdapterNotFoundError:
                repl.renderer.error(f"Adapter '{args[1]}' not found. See /adapters")
                return
            repl.renderer.success(
                f"Switched model to '{adapter.ollama_model}' (adapter '{adapter.name}')."
            )
            if backend.client.is_alive() and not backend.client.exists():
                repl.renderer.warn(
                    f"Model '{adapter.ollama_model}' is not in Ollama yet.\n"
                    f"  Create it with:  ollama create {adapter.ollama_model} -f data/gguf/Modelfile"
                )
            return

        repl.renderer.error(
            "Usage: /adapters [list|info <name>|register <name>|delete <name>|switch <name>]"
        )

    # -- license -----------------------------------------------------------

    def _license(args):
        from licensing import LicenseError
        from licensing.store import activate, clear_activation, load_activation

        action = args[0].lower() if args else "status"

        if action == "activate" and len(args) > 1:
            key = " ".join(args[1:]).strip()
            try:
                lic = activate(key)
            except LicenseError as exc:
                repl.renderer.error(f"Activation failed: {exc}")
                return
            repl.renderer.success(f"Activated {lic.plan.upper()} license for {lic.email}")
            if lic.expires_at is not None:
                repl.renderer.info(f"Expires: {lic.expires_at:%Y-%m-%d %H:%M %Z}")
            else:
                repl.renderer.info("Expires: never (perpetual)")
            return

        if action == "status":
            lic = load_activation()
            if lic is None:
                repl.renderer.info("License: free (no Pro activation)")
                repl.renderer.info("Activate with: /license activate <key>")
            else:
                repl.renderer.info(f"License: {lic.plan.upper()}")
                repl.renderer.info(f"Email:   {lic.email}")
                repl.renderer.info(
                    f"Expires: {lic.expires_at:%Y-%m-%d %H:%M %Z}" if lic.expires_at is not None else "Expires: never (perpetual)"
                )
                from licensing.fingerprint import device_code
                from licensing.models import normalize_device

                if lic.device is not None:
                    bound = lic.device == normalize_device(device_code())
                    repl.renderer.info(
                        f"Device:  bound ({'this machine' if bound else 'ANOTHER machine'})"
                    )
                else:
                    repl.renderer.info("Device:  not bound (portable key)")
            return

        if action == "deactivate":
            if clear_activation():
                repl.renderer.success("License deactivated (back to free).")
            else:
                repl.renderer.info("No activation to remove.")
            return

        repl.renderer.error("Usage: /license [status|activate <key>|deactivate]")

    # -- theme -------------------------------------------------------------

    def _theme(args):
        if not args:
            repl.renderer.info(f"Current theme: {repl.renderer.theme.name}")
            return
        name = args[0].lower()
        if name not in THEMES:
            repl.renderer.error(f"Unknown theme: {name}. Choose dark or light.")
            return
        new_theme = THEMES[name]
        repl.renderer.theme = new_theme
        save_theme(settings, new_theme)
        repl.renderer.success(f"Theme switched to {name}.")

    # -- register ---------------------------------------------------------

    reg.register("help", "Show this help", _help)
    reg.register("clear", "Clear conversation history", _clear)
    reg.register("model", "Show current model info", _model)
    reg.register("status", "Check server, model, RAG & web status", _status)
    reg.register("index", "Index a directory [/index <path>]", _index)
    reg.register("search", "Search indexed code [/search <query>]", _search)
    reg.register("rag", "Toggle RAG override [/rag on|off|clear]", _rag)
    reg.register("route", "Preview routing [/route <query>]", _route)
    reg.register("web", "Search the web [/web <query>]", _web)
    reg.register("webclear", "Clear web cache", _web_clear)
    reg.register("agent", "Run autonomous agent [/agent <task>]", _agent)
    reg.register("adapters", "LoRA adapter marketplace [/adapters list|info|register|delete|switch]", _adapters)
    reg.register("license", "License plan & activation [/license status|activate <key>|deactivate]", _license)
    reg.register("theme", "Switch UI theme [/theme dark|light]", _theme)
    return reg


def _safe_count(rag_service: RAGService) -> int:
    """Return chunk count without forcing model load."""
    try:
        return rag_service.count()
    except Exception:
        return 0


def _startup_backend(settings: Settings):
    """Pick the startup backend, enforcing the Pro gate for the API backend.

    Returns (backend, gated): *gated* is True when a configured api backend
    was rejected because no Pro license is active. Local Ollama and the
    embedded llama.cpp engine are free (roadmap D2).
    """
    from core.backend_factory import BackendFactory, _detect_backend_type

    backend_type = _detect_backend_type(settings)
    if backend_type == "api" and not feature_enabled("multi_model"):
        return OllamaBackend(settings), True
    if backend_type == "ollama":
        return OllamaBackend(settings), False
    return BackendFactory.create_primary(settings), False


def main() -> int:
    settings = Settings.load()
    renderer = Renderer(theme=load_theme(settings))

    backend, gated = _startup_backend(settings)
    if gated:
        renderer.error(
            "The API backend requires a Pro license.\n"
            "  Free tier: local Ollama or the embedded llama.cpp engine.\n"
            "  Activate with: /license activate <key>"
        )

    if isinstance(backend, OllamaBackend):
        if not backend.client.is_alive():
            renderer.error(
                f"Ollama server not reachable at {settings.ollama_host}.\n"
                "  Start it with:  ollama serve"
            )
            renderer.info("Continuing anyway — commands like /status will retry.\n")

        if not backend.client.exists():
            renderer.warn(
                f"Model '{settings.model}' not found locally.\n"
                f"  Pull it with:  ollama pull {settings.model}"
            )

    history_path = f"{settings.paths.data_dir}/repl_history"

    repl = REPL(
        backend=backend,
        renderer=renderer,
        registry=CommandRegistry(),  # placeholder; rebuilt below
        history_path=history_path,
    )
    repl.registry = build_registry(repl, backend, settings)
    repl.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
