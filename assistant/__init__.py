"""«Личный помощник» (Pro): Fluxion watches what the user allows on this computer.

Connectors (``connector.Connector``) read one source each — folders, the active window,
later mail, meetings and MCP servers — and put short events into the local journal
(``journal.Journal``). Rules (``rules``) answer events without a model: a notice in the
tray, a message to the bots. The chat and the bots ask the journal (``context``).

Everything is off until the user turns it on, connector by connector; nothing leaves
the computer (``config``: the cloud model sees a connector's events only when allowed).
"""
from __future__ import annotations

PRO_FEATURE = "personal_assistant"
