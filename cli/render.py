"""Rich-based rendering: streaming markdown, code highlight, status lines."""
from __future__ import annotations

from collections.abc import Iterable

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from .theme import DARK, Theme


class Renderer:
    def __init__(self, theme: Theme = DARK) -> None:
        self.theme = theme
        self.console = Console()

    def _logo(self) -> Text:
        left = Text("∞ ", style=f"bold {self.theme.accent}")
        word = Text("Fluxion", style=f"bold {self.theme.fg}")
        right = Text(" ∞", style=f"bold {self.theme.accent2}")
        return Text.assemble(left, word, right)

    def banner(self, text: str) -> None:
        content = Text.assemble(
            self._logo(),
            Text(f"\n{text}", style=f"dim {self.theme.dim}"),
        )
        self.console.print(
            Panel(
                content,
                border_style=self.theme.panel_border,
                padding=(1, 2),
            )
        )

    def info(self, msg: str) -> None:
        self.console.print(escape(msg), style=self.theme.dim)

    def success(self, msg: str) -> None:
        self.console.print(
            f"[bold {self.theme.success}]OK[/bold {self.theme.success}] {escape(msg)}"
        )

    def warn(self, msg: str) -> None:
        self.console.print(
            f"[bold {self.theme.warn}]![/bold {self.theme.warn}] {escape(msg)}"
        )

    def error(self, msg: str) -> None:
        self.console.print(
            f"[bold {self.theme.error}]error:[/bold {self.theme.error}] {escape(msg)}"
        )

    @property
    def pt_style(self):
        from prompt_toolkit.styles import Style

        return Style.from_dict(
            {
                "accent": f"bold {self.theme.accent}",
                "arrow": f"bold {self.theme.accent2}",
            }
        )

    def stream_markdown(self, tokens: Iterable[str]) -> str:
        """Render streaming tokens as live-updating markdown; return full text."""
        buf: list[str] = []
        with Live(
            "",
            console=self.console,
            refresh_per_second=15,
            vertical_overflow="visible",
        ) as live:
            for tok in tokens:
                buf.append(tok)
                live.update(Markdown("".join(buf)))
        return "".join(buf)
