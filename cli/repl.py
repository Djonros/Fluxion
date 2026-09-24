"""Interactive REPL built on prompt_toolkit with streaming responses."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit import HTML, PromptSession
from prompt_toolkit.history import FileHistory

from .commands import CommandRegistry
from .render import Renderer

if TYPE_CHECKING:
    from orchestrator import Assistant

SYSTEM_PROMPT = (
    "You are Fluxion, an expert Python programming assistant running fully "
    "on the user's local machine.\n"
    "Give concise, correct, well-structured answers with runnable code examples.\n"
    "Use fenced code blocks with the correct language tag for all code.\n"
    "Always reply in the same language the user writes in."
)


class REPL:
    def __init__(
        self,
        backend,
        renderer: Renderer,
        registry: CommandRegistry,
        history_path: str | None = None,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.backend = backend
        self.renderer = renderer
        self.registry = registry
        self.system_prompt = system_prompt
        self.messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        self._running = True
        self.rag_enabled = False
        self.rag_service = None
        self.web_search = None
        self.assistant: "Assistant | None" = None

        session_kwargs: dict = {}
        if history_path:
            Path(history_path).parent.mkdir(parents=True, exist_ok=True)
            session_kwargs["history"] = FileHistory(history_path)
        self.session: PromptSession = PromptSession(**session_kwargs)

    # -- public API -------------------------------------------------------

    def stop(self) -> None:
        self._running = False

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.system_prompt}]
        if self.assistant:
            self.assistant.reset()
        self.renderer.info("Conversation cleared.")

    def run(self) -> None:
        self.renderer.banner("Fluxion  —  local Python AI assistant")
        self.renderer.info("Type a question, or /help for commands. /exit to quit.\n")
        while self._running:
            try:
                user_text = self.session.prompt(
                self._prompt_text(), style=self.renderer.pt_style
            ).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not user_text:
                continue
            if user_text.startswith("/"):
                self._dispatch_command(user_text)
            else:
                self._handle_query(user_text)
        self.renderer.info("\nBye!")

    # -- internals --------------------------------------------------------

    def _prompt_text(self):
        tag = " [rag] " if self.rag_enabled else ""
        return HTML(f'<accent>{tag}</accent><arrow>❯</arrow> ')

    def _dispatch_command(self, raw: str) -> None:
        parts = raw[1:].split(None, 1)
        name = parts[0]
        args = parts[1].split() if len(parts) > 1 else []
        cmd = self.registry.get(name)
        if cmd is None and name in ("exit", "quit"):
            self.stop()
            return
        if cmd is None:
            self.renderer.error(f"Unknown command: /{name}. Try /help.")
            return
        try:
            cmd.handler(args)
        except Exception as exc:  # noqa: BLE001
            self.renderer.error(str(exc))

    def _handle_query(self, user_text: str) -> None:
        if self.assistant is not None:
            self._handle_via_assistant(user_text)
        else:
            self._handle_direct(user_text)

    def _handle_via_assistant(self, user_text: str) -> None:
        from orchestrator import Strategy

        force = Strategy.RAG if self.rag_enabled else None
        try:
            result, stream = self.assistant.ask_stream(
                user_text,
                history=self._chat_history(),
                force_strategy=force,
            )
        except Exception as exc:  # noqa: BLE001
            self.renderer.error(f"Routing failed: {exc}")
            return

        self._show_strategy(result)

        try:
            reply = self.renderer.stream_markdown(stream)
        except Exception as exc:  # noqa: BLE001
            self.renderer.error(f"Generation failed: {exc}")
            return

        self.messages.append({"role": "user", "content": user_text})
        if reply.strip():
            self.messages.append({"role": "assistant", "content": reply})
        self.renderer.console.print()

    def _handle_direct(self, user_text: str) -> None:
        augmented = user_text
        if self.rag_enabled and self.rag_service is not None:
            augmented = self._augment_with_rag(user_text)
        self.messages.append({"role": "user", "content": augmented})
        try:
            tokens = self.backend.stream(self.messages)
            reply = self.renderer.stream_markdown(tokens)
        except Exception as exc:  # noqa: BLE001
            self.renderer.error(f"Generation failed: {exc}")
            if self.messages and self.messages[-1]["role"] == "user":
                self.messages.pop()
            return
        if reply.strip():
            self.messages.append({"role": "assistant", "content": reply})
        self.renderer.console.print()

    def _chat_history(self) -> list[dict[str, str]]:
        return [
            m for m in self.messages if m["role"] in ("user", "assistant")
        ]

    def _show_strategy(self, result) -> None:
        parts = [f"strategy={result.strategy.value}"]
        if result.rag_used:
            src_count = len(result.rag_sources)
            parts.append(f"rag({src_count} sources)")
        if result.web_used:
            src_count = len(result.web_sources)
            parts.append(f"web({src_count} sources)")
        self.renderer.info(f"[{' | '.join(parts)}]")

    def _augment_with_rag(self, user_text: str) -> str:
        from rag.service import RAGService

        assert isinstance(self.rag_service, RAGService)
        try:
            results = self.rag_service.search(user_text, top_k=5)
        except Exception as exc:  # noqa: BLE001
            self.renderer.warn(f"RAG retrieval failed: {exc}")
            return user_text
        if not results:
            return user_text
        context = RAGService.build_context(results)
        return f"{context}\n\nQuestion: {user_text}"
