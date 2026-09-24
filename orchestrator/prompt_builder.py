"""PromptBuilder: assembles messages within a token budget.

Budget allocation (from num_ctx - max_predict):
  system:     fixed  600 tokens
  rag_context:  cap 6000 tokens
  web_context:   cap 4000 tokens
  history:       remaining (after query)
  query:         uncapped (but typically short)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.tokenizer_util import count_tokens, truncate_to_tokens

SYSTEM_BASE = (
    "You are Fluxion, an expert Python programming assistant running fully "
    "on the user's local machine.\n"
    "Give concise, correct, well-structured answers with runnable code examples.\n"
    "Use fenced code blocks with the correct language tag for all code.\n"
    "When code snippets are provided as context, reference them as [1], [2], etc.\n"
    "Always reply in the same language the user writes in."
)

# Token budgets per section
SYSTEM_BUDGET = 600
RAG_BUDGET = 6000
WEB_BUDGET = 4000
QUERY_BUDGET = 2000
HISTORY_MIN_BUDGET = 1024  # at least this much for history when possible


@dataclass
class PromptContext:
    """Context blocks to inject into the prompt."""
    rag_snippets: list[tuple[str, str]] = field(default_factory=list)  # (citation_tag, text)
    web_snippets: list[tuple[str, str]] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    query: str = ""


class PromptBuilder:
    """Builds the final messages list within the model's context budget."""

    def __init__(
        self,
        num_ctx: int = 32768,
        max_predict: int = 2048,
        system_prompt: str = SYSTEM_BASE,
    ):
        self.num_ctx = num_ctx
        self.max_predict = max_predict
        self.system_prompt = system_prompt
        self.budget = num_ctx - max_predict

    def build(self, ctx: PromptContext) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": self.system_prompt}]

        used = count_tokens(self.system_prompt)

        # ── RAG context ──
        if ctx.rag_snippets:
            rag_text, rag_used = self._build_cited_block(
                ctx.rag_snippets,
                "relevant code from the indexed codebase",
                min(RAG_BUDGET, self.budget - used - QUERY_BUDGET - HISTORY_MIN_BUDGET),
            )
            if rag_text:
                messages.append({"role": "user", "content": rag_text})
                used += rag_used

        # ── Web context ──
        if ctx.web_snippets:
            remaining = self.budget - used - QUERY_BUDGET - HISTORY_MIN_BUDGET
            web_text, web_used = self._build_cited_block(
                ctx.web_snippets,
                "relevant information from the web",
                min(WEB_BUDGET, max(0, remaining)),
            )
            if web_text:
                messages.append({"role": "user", "content": web_text})
                used += web_used

        # ── History ──
        remaining_for_history = self.budget - used - count_tokens(ctx.query) - 200
        if remaining_for_history > 0 and ctx.history:
            history_msgs = self._fit_history(ctx.history, remaining_for_history)
            messages.extend(history_msgs)

        # ── Query ──
        query_text = ctx.query
        if count_tokens(query_text) > QUERY_BUDGET:
            query_text = truncate_to_tokens(query_text, QUERY_BUDGET)
        messages.append({"role": "user", "content": query_text})

        return messages

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _build_cited_block(
        snippets: list[tuple[str, str]],
        header_desc: str,
        max_tokens: int,
    ) -> tuple[str, int]:
        """Build a cited context block within token budget. Returns (text, tokens_used)."""
        if max_tokens <= 0:
            return "", 0

        header = f"Here is {header_desc}. Reference these as [1], [2], etc.:\n\n"
        header_tokens = count_tokens(header)

        body_parts: list[str] = []
        used = header_tokens

        for tag, text in snippets:
            entry = f"[{tag}]\n{text}\n\n---\n"
            entry_tokens = count_tokens(entry)
            if used + entry_tokens > max_tokens:
                # truncate this entry
                remaining = max_tokens - used
                if remaining > 50:
                    truncated = truncate_to_tokens(entry, remaining)
                    body_parts.append(truncated)
                    used += count_tokens(truncated)
                break
            body_parts.append(entry)
            used += entry_tokens

        if not body_parts:
            return "", 0

        full = header + "".join(body_parts)
        return full, used

    @staticmethod
    def _fit_history(
        history: list[dict[str, str]],
        max_tokens: int,
    ) -> list[dict[str, str]]:
        """Keep the most recent history messages that fit within max_tokens."""
        result: list[dict[str, str]] = []
        used = 0
        for msg in reversed(history):
            msg_tokens = count_tokens(msg["content"])
            if used + msg_tokens > max_tokens:
                break
            result.insert(0, msg)
            used += msg_tokens
        return result
