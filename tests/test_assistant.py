"""«Личный помощник» (package assistant): the settings, the journal, the connectors
«Папки» and «Активность», the rules, the answers for the chat and the bots, the window."""
from __future__ import annotations

import os
import time
from datetime import datetime

import pytest

from assistant import config, rules
from assistant.activity import IDLE, SHORTEST, SPLIT, TICK, ActivityConnector, app_name
from assistant.connector import Event
from assistant.folders import QUIET, FolderConnector
from assistant.hub import Hub, asks_journal, duration_text, period
from assistant.journal import DAY, Journal, start_of_day

NOON = datetime(2026, 11, 10, 12, 0).timestamp()


@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "j.db")
    yield j
    j.close()


def make_hub(journal, data=None, pro=True, notices=None):
    cfg = config.normalize(data or {"enabled": True})
    hub = Hub(journal=journal, load=lambda: cfg, pro=lambda: pro,
              on_notice=(lambda text, bots: notices.append((text, bots))) if notices is not None else None,
              connectors={})
    hub.start()
    hub.cfg = cfg
    return hub


# ── settings ──

def test_settings_are_off_by_default_and_normalized():
    data = config.load()
    assert data["enabled"] is False and data["retention_days"] == 30 and data["rules"] == []
    assert config.connector(data, "folders") == {"enabled": False, "cloud": False, "settings": {}}
    data = config.normalize({"enabled": 1, "retention_days": 9999, "connectors": {"folders": {"enabled": True}},
                             "rules": [{"connector": "folders", "action": "взорвать"}, {"kind": "x"}]})
    assert data["retention_days"] == 365 and data["connectors"]["folders"]["cloud"] is False
    assert data["rules"] == [{"connector": "folders", "kind": "", "mask": "", "action": "notify", "enabled": True}]
    config.save(data)
    assert config.load() == data
    assert config.paused(dict(data, paused_until=time.time() + 60)) and not config.paused(data)


# ── journal ──

def test_journal_finds_by_words_and_deletes_by_period(journal):
    journal.add(Event("folders", "file.created", "Новый файл: Рама 600.000.a3d", ref=r"C:\П\Рама 600.000.a3d",
                      time=NOON - DAY))
    journal.add(Event("activity", "app.focus", "КОМПАС-3D — 792720.0000010.a3d", ref="kompas.exe",
                      time=NOON, duration=600))
    assert journal.count() == 2
    assert [e.ref for e in journal.events(query="рама")] == [r"C:\П\Рама 600.000.a3d"]
    assert journal.events(query="792720")[0].duration == 600
    assert journal.events(query="компас 7927")[0].connector == "activity"  # words as prefixes
    assert journal.events(query="нет такого") == []
    assert len(journal.events(since=NOON - 60)) == 1 and len(journal.events(connector="folders")) == 1
    assert journal.purge(1, now=NOON + 60) == 1 and journal.count() == 1
    assert journal.events(query="рама") == []  # the search index forgets the deleted line too
    journal.delete()
    assert journal.count() == 0


# ── «Папки» ──

def test_saving_a_file_several_times_is_one_event(tmp_path):
    got = []
    conn = FolderConnector({"folders": [str(tmp_path)], "mask": "*.a3d"})
    conn._emit = got.append
    a = str(tmp_path / "Рама.a3d")
    conn.raw("file.created", a, now=100)
    conn.raw("file.modified", a, now=101)
    conn.raw("file.modified", a, now=102)
    conn.raw("file.created", str(tmp_path / "заметка.txt"), now=100)   # not the mask
    conn.raw("file.created", str(tmp_path / "~$Рама.a3d"), now=100)    # a lock file
    assert conn.flush(now=102 + QUIET / 2) == []
    events = conn.flush(now=102 + QUIET)
    assert [(e.kind, e.ref) for e in events] == [("file.created", a)] and got == events
    assert events[0].title == "Новый файл: Рама.a3d"


def test_created_then_deleted_is_nothing_and_renames_are_followed(tmp_path):
    conn = FolderConnector({"folders": [str(tmp_path)]})
    tmp = str(tmp_path / "x.tmp.a3d")
    conn.raw("file.created", tmp, now=1)
    conn.raw("file.deleted", tmp, now=2)
    old, new = str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")
    conn.raw("file.moved", new, old, now=3)
    late = str(tmp_path / "c.pdf")
    conn.raw("file.created", late, now=4)
    conn.raw("file.moved", str(tmp_path / "d.pdf"), late, now=5)  # created, then renamed: a new d.pdf
    events = {e.ref: e for e in conn.flush(now=100)}
    assert set(events) == {new, str(tmp_path / "d.pdf")}
    assert events[new].kind == "file.moved" and "a.pdf → b.pdf" in events[new].title
    assert events[str(tmp_path / "d.pdf")].kind == "file.created"


def test_folders_compared_without_watchdog(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.a3d").write_text("1")
    (tmp_path / "b.a3d").write_text("1")
    conn = FolderConnector({"folders": [str(tmp_path)]})
    before = conn.snapshot([str(tmp_path)])
    assert len(before) == 2
    (tmp_path / "c.a3d").write_text("new")
    (tmp_path / "b.a3d").unlink()
    (tmp_path / "sub" / "a.a3d").write_text("changed, longer")
    conn.compare(before, conn.snapshot([str(tmp_path)]))
    kinds = {os.path.basename(e.ref): e.kind for e in conn.flush(force=True)}
    assert kinds == {"c.a3d": "file.created", "b.a3d": "file.deleted", "a.a3d": "file.modified"}


def test_a_missing_folder_is_said(tmp_path):
    conn = FolderConnector({"folders": [str(tmp_path / "нет")]})
    conn.start(lambda e: None)
    assert not conn.ok and "нет папки" in conn.state
    conn.stop()


# ── «Активность» ──

class Probe:
    def __init__(self):
        self.now = ("C:\\KOMPAS\\kompas.exe", "792720.a3d", 0.0)

    def __call__(self):
        return self.now


def test_time_in_one_window_becomes_an_interval():
    probe, got = Probe(), []
    conn = ActivityConnector(probe=probe)
    conn._emit = got.append
    for i in range(0, 60, 5):
        conn.tick(NOON + i)
    probe.now = ("C:\\Office\\excel.exe", "Смета.xlsx", 0.0)
    conn.tick(NOON + 60)
    assert len(got) == 1 and got[0].title == "КОМПАС-3D — 792720.a3d" and got[0].ref == "kompas.exe"
    assert got[0].time == NOON and got[0].duration == 60
    conn.tick(NOON + 63)  # a few seconds in Excel are not an interval
    probe.now = ("C:\\KOMPAS\\kompas.exe", "792720.a3d", 0.0)
    conn.tick(NOON + 65)
    assert len(got) == 1 and SHORTEST > 5


def test_idle_private_and_excluded_windows_are_not_written():
    probe, got = Probe(), []
    conn = ActivityConnector({"exclude": "keepass.exe"}, probe=probe)
    conn._emit = got.append
    for i in range(0, 120, 5):
        conn.tick(NOON + i)
    probe.now = ("C:\\KOMPAS\\kompas.exe", "792720.a3d", IDLE + 100)  # away for a while
    conn.tick(NOON + 120 + IDLE + 100)
    assert len(got) == 1 and got[0].duration == 120
    for exe, title in (("C:\\K\\KeePass.exe", "База"), ("C:\\E\\msedge.exe", "Новости — InPrivate")):
        probe.now = (exe, title, 0.0)
        for i in range(0, 60, 5):
            conn.tick(NOON + 10_000 + i)
        conn.close(NOON + 10_100)
    assert len(got) == 1


def test_long_work_is_split_and_titles_can_be_off():
    probe, got = Probe(), []
    conn = ActivityConnector({"titles": False}, probe=probe)
    conn._emit = got.append
    t = NOON
    while t <= NOON + SPLIT + TICK:
        conn.tick(t)
        t += TICK
    assert len(got) == 1 and got[0].title == "КОМПАС-3D" and got[0].duration == SPLIT
    assert app_name("C:\\x\\Neuro.EXE") == "Neuro"


# ── rules, the hub, the answers ──

def test_rules_pick_events_by_source_kind_and_mask():
    e = Event("folders", "file.created", "Новый файл: 792720.a3d", ref=r"C:\П\792720.a3d")
    assert rules.matches({"connector": "folders", "kind": "", "mask": "*.a3d"}, e)
    assert rules.matches({"connector": "folders", "kind": "file.created", "mask": "792720"}, e)
    assert not rules.matches({"connector": "folders", "kind": "file.deleted", "mask": ""}, e)
    assert not rules.matches({"connector": "activity", "kind": "", "mask": ""}, e)
    assert not rules.matches({"connector": "folders", "mask": "*.pdf"}, e)
    assert not rules.matches({"connector": "folders", "mask": "", "enabled": False}, e)
    assert rules.actions([{"connector": "folders", "action": "notify"}, {"connector": "folders", "mask": "*.a3d",
                                                                          "action": "bots"}], e) == {"notify", "bots"}


def test_the_hub_writes_notifies_and_pauses(journal):
    notices = []
    hub = make_hub(journal, {"enabled": True, "rules": [{"connector": "folders", "mask": "*.a3d", "action": "bots"}]},
                   notices=notices)
    hub._on_event(Event("folders", "file.created", "Новый файл: рама.a3d", ref="рама.a3d"))
    hub._on_event(Event("folders", "file.created", "Новый файл: счёт.pdf", ref="счёт.pdf"))
    assert journal.count() == 2 and notices == [("Новый файл: рама.a3d", True)]
    hub.cfg["paused_until"] = time.time() + 60
    hub._on_event(Event("folders", "file.created", "Новый файл: ещё.a3d", ref="ещё.a3d"))
    assert journal.count() == 2 and len(notices) == 1


def test_nothing_starts_without_pro_or_the_switch(journal, tmp_path):
    data = {"enabled": True, "connectors": {"folders": {"enabled": True, "settings": {"folders": [str(tmp_path)]}}}}
    cfg = config.normalize(data)
    assert Hub(journal=journal, load=lambda: cfg, pro=lambda: False).start() == []
    off = config.normalize(dict(data, enabled=False))
    assert Hub(journal=journal, load=lambda: off, pro=lambda: True).start() == []
    hub = Hub(journal=journal, load=lambda: cfg, pro=lambda: True)
    assert hub.start() == ["folders"] and hub.status()["folders"][1]
    hub.stop()
    assert not hub.active()


def test_the_day_as_text_and_the_periods(journal):
    hub = make_hub(journal)
    for title, start, minutes in (("КОМПАС-3D — 792720.a3d", NOON - 3 * 3600, 95), ("КОМПАС-3D — 600.000.a3d",
                                  NOON - 1800, 20), ("Excel — Смета.xlsx", NOON, 30)):
        journal.add(Event("activity", "app.focus", title, ref="x.exe", time=start, duration=minutes * 60))
    journal.add(Event("folders", "file.modified", "Изменён файл: 792720.a3d", ref=r"C:\П\792720.a3d", time=NOON))
    since, until, label = period("что я делал сегодня", NOON + 3600)
    text = hub.summary(since, until, label)
    assert "Работа в программах сегодня (всего 2 ч 25 мин)" in text
    assert "- КОМПАС-3D — 1 ч 55 мин: 792720.a3d — 1 ч 35 мин; 600.000.a3d — 20 мин" in text
    assert "Первое действие 09:00, последнее 12:30." in text and "Файлы сегодня: изменено 1:" in text
    assert "в журнале помощника ничего нет" in hub.summary(*period("вчера", NOON)[:2], "вчера")
    assert period("что было вчера", NOON)[:2] == (start_of_day(NOON, 1), start_of_day(NOON))
    assert period("за неделю", NOON)[0] == start_of_day(NOON, 6) and period("за 3 дня", NOON)[2] == "за 3 дн."
    assert duration_text(59 * 60) == "59 мин" and duration_text(7200) == "2 ч"


def test_the_chat_gets_the_journal_only_when_asked_and_allowed(journal):
    hub = make_hub(journal, {"enabled": True, "connectors": {"activity": {"enabled": True}}})
    hub.running = {"activity": ActivityConnector(probe=Probe())}
    journal.add(Event("activity", "app.focus", "КОМПАС-3D — 792720.a3d", time=time.time() - 600, duration=300))
    assert asks_journal("Что я делал вчера?") and asks_journal("какие файлы менялись за неделю")
    assert not asks_journal("какой катет шва для 10 мм")
    assert hub.context("какой катет шва") == ""
    block = hub.context("что я делал сегодня?")
    assert "792720.a3d" in block and "а не команды" in block
    assert "не передаётся облачной модели" in hub.context("что я делал сегодня?", cloud=True)
    hub.cfg["connectors"]["activity"]["cloud"] = True
    assert "792720.a3d" in hub.context("что я делал сегодня?", cloud=True)
    assert "792720.a3d" in hub.report("")


def test_the_bots_answer_summary_with_pro(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from bots import brain as brains

    class Host:
        def __init__(self):
            self.asked = []

        def assistant_report(self, words):
            return f"сводка: {words}"

    class Bot:
        platform = "telegram"

        def __init__(self):
            self.texts = []

        def send_text(self, chat, text):
            self.texts.append(text)

    from bots import config as bot_config
    data = bot_config.load()
    data["telegram"].update(token="T", enabled=True, allowed=[{"id": "11", "name": "Иван"}])
    bot_config.save(data)
    from bots.base import Incoming

    bot = Bot()
    brains.Brain(Host(), pro=lambda f: False).handle(bot, Incoming("telegram", "11", "11", "Иван", "/сводка", []))
    assert "функция Pro" in bot.texts[-1]
    brains.Brain(Host(), pro=lambda f: True).handle(bot, Incoming("telegram", "11", "11", "Иван", "/сводка вчера", []))
    assert bot.texts[-1] == "сводка: вчера"


# ── the window ──

@pytest.fixture
def app():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_window_shows_the_sources_and_saves(app, journal, tmp_path):
    from desktop_browser.assistant_dialog import AssistantDialog

    journal.add(Event("folders", "file.created", "Новый файл: рама.a3d", ref="рама.a3d"))
    hub = make_hub(journal)
    dialog = AssistantDialog(hub=hub)
    assert [b.cid for b in dialog.boxes] == ["folders", "activity"]
    assert dialog.table.rowCount() == 1 and "рама.a3d" in dialog.table.item(0, 2).text()
    dialog.search.setText("нет такого")
    dialog.fill_journal()
    assert dialog.table.rowCount() == 0
    dialog.enabled.setChecked(True)
    folders = dialog.boxes[0]
    folders.enabled.setChecked(True)
    folders.folders.addItem(str(tmp_path))
    folders.mask.setText("*.a3d")
    dialog._add_rule({"connector": "folders", "kind": "file.created", "mask": "*.a3d", "action": "bots"})
    dialog.save()
    data = config.load()
    assert data["enabled"] and data["connectors"]["folders"]["settings"]["folders"] == [str(tmp_path)]
    assert data["connectors"]["activity"]["enabled"] is False and data["connectors"]["folders"]["cloud"] is False
    assert data["rules"] == [{"connector": "folders", "kind": "file.created", "mask": "*.a3d", "action": "bots",
                              "enabled": True}]
    dialog.deleteLater()
