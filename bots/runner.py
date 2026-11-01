"""The bots at work while Fluxion is open: a thread per messenger waits for messages
(long polling — no server, no open port), each message is answered in a thread of its
own (at most WORKERS at once; daemons — a long job never holds Fluxion from closing),
one message of a user at a time.

``state`` tells the window how each bot is: "работает @fluxion_bot", "нет сети —
повторю", "токен не принят". A rejected token stops that bot; a lost network is
retried with a growing pause.
"""
from __future__ import annotations

import threading
import time

from . import config
from .base import BotError

WORKERS = 4
POLL_TIMEOUT = 25
RETRY = (5, 15, 30, 60, 120)


def adapter_for(platform: str, token: str, proxy: str = "", session=None):
    if platform == "telegram":
        from .telegram import Telegram

        return Telegram(token, proxy, session)
    if platform == "max":
        from .maxbot import Max

        return Max(token, proxy, session)
    if platform == "vk":
        from .vk import Vk

        return Vk(token, proxy, session)
    raise ValueError(platform)


class Runner:
    def __init__(self, brain, state=None, make_adapter=adapter_for, load=config.load):
        self.brain = brain
        self.state = state or (lambda platform, text, ok: None)
        self.make_adapter = make_adapter
        self.load = load
        self.adapters: dict[str, object] = {}
        self.threads: dict[str, threading.Thread] = {}
        self.stop_event = threading.Event()
        self.slots = threading.BoundedSemaphore(WORKERS)

    def start(self) -> list[str]:
        """Start the enabled bots that have a token; the platforms started."""
        data = self.load()
        self.stop_event.clear()
        started = []
        for platform in config.PLATFORMS:
            settings = data[platform]
            if not (settings["enabled"] and settings["token"]) or platform in self.threads:
                continue
            try:
                adapter = self.make_adapter(platform, settings["token"], settings["proxy"])
            except BotError as exc:
                self.state(platform, str(exc), False)
                continue
            self.adapters[platform] = adapter
            thread = threading.Thread(target=self._loop, args=(platform, adapter), daemon=True,
                                      name=f"fluxion-bot-{platform}")
            self.threads[platform] = thread
            thread.start()
            started.append(platform)
        return started

    def running(self) -> list[str]:
        return [p for p, t in self.threads.items() if t.is_alive()]

    def stop(self, wait: float = 0.0) -> None:
        """Stop polling; a poll in progress ends by its timeout (the threads are daemons)."""
        self.stop_event.set()
        for thread in list(self.threads.values()):
            if wait:
                thread.join(wait)
        self.threads.clear()
        self.adapters.clear()

    def _loop(self, platform: str, adapter) -> None:
        failures = 0
        try:
            name = adapter.me()
        except BotError as exc:
            self.state(platform, str(exc), False)
            self.threads.pop(platform, None)
            return
        self.state(platform, f"работает: {name}", True)
        while not self.stop_event.is_set():
            try:
                messages = adapter.poll(POLL_TIMEOUT)
            except BotError as exc:
                if "токен не принят" in str(exc):
                    self.state(platform, str(exc), False)
                    break
                pause = RETRY[min(failures, len(RETRY) - 1)]
                failures += 1
                self.state(platform, f"{exc} — повторю через {pause} с", False)
                if self.stop_event.wait(pause):
                    break
                continue
            if failures:
                failures = 0
                self.state(platform, f"работает: {name}", True)
            for msg in messages:
                if self.stop_event.is_set():
                    break
                threading.Thread(target=self._answer, args=(adapter, msg), daemon=True,
                                 name=f"fluxion-bot-{platform}-answer").start()
        self.threads.pop(platform, None)
        if self.stop_event.is_set():
            self.state(platform, "остановлен", False)

    def _answer(self, adapter, msg) -> None:
        with self.slots:
            try:
                self.brain.handle(adapter, msg)
            except BotError as exc:  # could not even answer: the window shows it
                self.state(adapter.platform, str(exc), False)

    def notify(self, text: str) -> int:
        """Tell the users who asked for it that a job in the window is over; how many were told."""
        told = 0
        for platform, chat in self.brain.subscribers(self.load()):
            adapter = self.adapters.get(platform)
            if adapter is None:
                continue
            try:
                adapter.send_text(chat, text)
                told += 1
            except BotError:
                pass
        return told
