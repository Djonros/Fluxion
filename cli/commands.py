"""Slash-command registry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Command:
    name: str
    description: str
    handler: Callable[[list[str]], Any]


class CommandRegistry:
    def __init__(self) -> None:
        self._cmds: dict[str, Command] = {}

    def register(self, name: str, description: str, handler: Callable[[list[str]], Any]) -> None:
        self._cmds[name] = Command(name=name, description=description, handler=handler)

    def get(self, name: str) -> Command | None:
        return self._cmds.get(name)

    def names(self) -> list[str]:
        return sorted(self._cmds)

    def help_text(self) -> str:
        lines = ["Available commands:"]
        for name in self.names():
            lines.append(f"  /{name:<10} {self._cmds[name].description}")
        lines.append("  /exit       Quit the assistant")
        return "\n".join(lines)
