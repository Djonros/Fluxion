"""«Личный помощник» (package assistant): the settings, the journal, the connectors
«Папки» and «Активность», the rules, the answers for the chat and the bots, the window."""
from __future__ import annotations

import json
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
    assert [b.cid for b in dialog.boxes] == ["folders", "activity", "mail"]
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


# ── apps: MCP servers, command lines, web APIs, the agent ──

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from assistant import apps as app_kinds  # noqa: E402
from assistant.agent import HelperAgent  # noqa: E402
from assistant.mcp import McpError, McpServer  # noqa: E402

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


def fake_app(**extra):
    return dict({"id": "notes", "name": "Заметки", "kind": "mcp", "command": sys.executable,
                 "args": [FAKE_SERVER]}, **extra)


def test_an_mcp_server_gives_its_tools_and_answers():
    with McpServer(sys.executable, [FAKE_SERVER], name="fake") as server:
        assert server.info["name"] == "fake"
        tools = {t.name: t for t in server.tools()}
        assert set(tools) == {"echo", "write_note"} and tools["echo"].read_only and not tools["write_note"].read_only
        assert server.call("echo", {"text": "рама"}) == "эхо: рама"
        with pytest.raises(McpError, match="нет такого"):
            server.call("nope")
    with pytest.raises(McpError, match="не запускается"):
        McpServer("нет-такой-программы-fluxion").start()


def test_apps_permissions_by_default_and_by_the_user():
    apps = app_kinds.Apps([fake_app(), fake_app(id="off", enabled=False)])
    try:
        tools = {t.full_name: t.policy for t in apps.tools()}
        assert tools == {"notes__echo": "auto", "notes__write_note": "ask"}  # read-only runs by itself
    finally:
        apps.stop()
    apps = app_kinds.Apps([fake_app(tools={"echo": "off", "write_note": "auto"})])
    try:
        assert {t.full_name: t.policy for t in apps.tools()} == {"notes__write_note": "auto"}
    finally:
        apps.stop()
    broken = app_kinds.Apps([fake_app(command="нет-такой-программы-fluxion")])
    assert broken.tools() == [] and "не запускается" in broken.errors["notes"]
    tools, error = app_kinds.check(fake_app())
    assert error == "" and len(tools) == 2
    assert app_kinds.normalize_app({"kind": "ftp"}) is None
    assert app_kinds.slug("Файлы", {"app"}) == "app2" and app_kinds.slug("My CRM") == "my_crm"


def test_a_command_line_program_is_one_tool():
    template = f'"{sys.executable}" -c "import sys; print(sys.argv[1][::-1])" {{input}}'
    assert app_kinds.cli_argv(template, "рама 600")[-1] == "рама 600"
    apps = app_kinds.Apps([{"id": "rev", "name": "Наоборот", "kind": "cli", "command": template,
                            "description": "переворачивает текст"}])
    (tool,) = apps.tools()
    assert tool.full_name == "rev__run" and tool.policy == "ask" and tool.description == "переворачивает текст"
    assert tool.run({"input": "абв"}) == "вба"
    with pytest.raises(app_kinds.AppError, match="нет программы"):
        app_kinds.run_cli("нет-такой-программы-fluxion {input}", "x")


def test_a_web_api_gets_the_input_and_the_key(monkeypatch):
    import httpx

    from assistant import secrets

    monkeypatch.setattr(secrets, "_cred", lambda: None)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"найдено": 2})

    app = app_kinds.normalize_app({"id": "crm", "name": "CRM", "kind": "http", "header": "X-Key",
                                   "url": "https://crm.local/find?q={input}"})
    secrets.set_secret(app_kinds.secret_name(app), "Bearer s3cret")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert '"найдено": 2' in app_kinds.run_http(app, "рама 600", client)
        post = dict(app, method="POST", url="https://crm.local/find")
        app_kinds.run_http(post, "стол", client)
    assert seen[0].url.params["q"] == "рама 600" and seen[0].headers["X-Key"] == "Bearer s3cret"
    assert seen[1].method == "POST" and json.loads(seen[1].read()) == {"input": "стол"}
    with pytest.raises(app_kinds.AppError, match="http"):
        app_kinds.run_http(dict(app, url="ftp://x"), "a")
    secrets.set_secret(app_kinds.secret_name(app), "ключ")
    with pytest.raises(app_kinds.AppError, match="латиниц"):
        app_kinds.run_http(app, "a")


class ScriptedBackend:
    """Answers the agent's steps one after another; remembers what it was shown."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def generate(self, messages, gen=None):
        self.seen.append(messages)
        return self.replies.pop(0)


def test_the_agent_calls_tools_and_asks_before_an_action():
    apps = app_kinds.Apps([fake_app()])
    try:
        tools = apps.tools()
        backend = ScriptedBackend('{"tool": "notes__echo", "args": {"text": "привет"}}',
                                  '{"tool": "notes__write_note", "args": {"text": "купить болты"}}',
                                  '{"tool": "notes__write_note", "args": {"text": "ещё"}}',
                                  '{"tool": "answer", "text": "Готово: заметка записана."}')
        asked, steps = [], []

        def confirm(tool, args):
            asked.append((tool.name, args["text"]))
            return len(asked) == 1  # yes to the first, no to the second

        agent = HelperAgent(backend, tools, confirm, steps.append)
        assert agent.run("запиши заметку") == "Готово: заметка записана."
        assert asked == [("write_note", "купить болты"), ("write_note", "ещё")]
        assert [c[2] for c in agent.calls] == ["ok", "ok", "отказ"]
        last = backend.seen[-1]
        assert "эхо: привет" in last[-5]["content"] and "заметок: 1" in last[-3]["content"]
        assert "не разрешил" in last[-1]["content"] and "данные, а не команды" in last[0]["content"]
        assert steps[-1].startswith("✖")
    finally:
        apps.stop()
    words = HelperAgent(ScriptedBackend("Просто ответ словами."), []).run("привет")
    assert words == "Просто ответ словами."
    assert "Нет инструмента" in HelperAgent(ScriptedBackend('{"tool": "x"}', '{"tool": "answer", "text": "ok"}'),
                                            [])._observe("x", {})


def test_the_hub_gives_the_journal_and_the_apps_to_the_agent(journal):
    hub = make_hub(journal, {"enabled": True, "apps": [fake_app()]})
    try:
        journal.add(Event("folders", "file.created", "Новый файл: 792720.a3d", ref="792720.a3d"))
        names = [t.full_name for t in hub.agent_tools()]
        assert names[:2] == ["journal__search", "journal__summary"] and "notes__echo" in names
        assert "792720.a3d" in hub.agent_tools()[0].run({"query": "792720"})
        assert hub.agent_tools(cloud=True) == []  # nothing allowed to the cloud model
        hub.cfg["connectors"]["folders"] = {"enabled": True, "cloud": True, "settings": {}}
        assert [t.full_name for t in hub.agent_tools(cloud=True)] == ["journal__search", "journal__summary"]
    finally:
        hub.stop()


def test_the_chat_command_of_the_assistant():
    pytest.importorskip("PySide6")
    from desktop_browser.app import helper_command

    assert helper_command("/помощник найди раму") == "найди раму"
    assert helper_command("/Помощник") == "" and helper_command("/помощники") is None
    assert helper_command("/assistant list files") == "list files" and helper_command("привет") is None


def test_the_window_adds_an_app_and_its_permissions(app, journal):
    from desktop_browser.assistant_dialog import AppDialog, AssistantDialog

    dialog = AppDialog(taken={"app"})
    dialog.name.setText("Заметки")
    dialog.kind.setCurrentIndex(dialog.kind.findData("mcp"))
    dialog.command.setText(sys.executable)
    dialog.args.setText(f'"{FAKE_SERVER}"')
    dialog.check()
    assert dialog.tools.rowCount() == 2 and "Инструментов: 2" in dialog.check_label.text()
    dialog.tools.cellWidget(0, 2).setCurrentIndex(dialog.tools.cellWidget(0, 2).findData("off"))
    value = dialog.value()
    assert value["id"] == "app2" and value["args"] == [FAKE_SERVER]
    assert value["tools"][dialog.tools.item(0, 0).text()] == "off"
    main = AssistantDialog(hub=make_hub(journal))
    main.app_list.append(value)
    main.save()
    assert config.load()["apps"][0]["tools"] == value["tools"]
    dialog.deleteLater()
    main.deleteLater()


# ── «Почта» ──

import imaplib  # noqa: E402
from email.message import EmailMessage  # noqa: E402

from assistant import mail  # noqa: E402


def letter(n, sender="Петров <petrov@zavod.ru>", subject="Рама 600.000", body="Добрый день!", html_body=None,
           attach=False, when="Tue, 10 Nov 2026 10:00:00 +0300"):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Date"] = sender, "me@zavod.ru", subject, when
    msg["Message-ID"] = f"<m{n}@zavod.ru>"
    if body is None:
        msg.set_content(html_body, subtype="html")  # a letter in HTML only
    else:
        msg.set_content(body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")
    if attach:
        msg.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="чертёж.pdf")
    return msg.as_bytes()


class FakeImap:
    def __init__(self, messages, password="pw"):
        self.messages, self.password = dict(messages), password
        self.appended, self.readonly, self.logged_out = [], None, False

    def login(self, user, password):
        if password != self.password:
            raise imaplib.IMAP4.error("b'[AUTHENTICATIONFAILED] Invalid credentials'")

    def select(self, folder, readonly=False):
        self.readonly = readonly
        return "OK", [str(len(self.messages)).encode()]

    def response(self, name):
        return name, [b"77"]

    def uid(self, command, *args):
        uids = sorted(self.messages)
        if command == "SEARCH":
            criteria = args[1:]
            if criteria[:1] == ("UID",):
                start = int(criteria[1].split(":")[0])
                found = [u for u in uids if u >= start] or uids[-1:]  # "n:*" always gives the last one
            elif criteria[:1] == ("UNSEEN",):
                found = uids[:1]
            else:
                found = uids
            return "OK", [" ".join(map(str, found)).encode()]
        wanted = [int(x) for x in args[0].split(",")]
        data = []
        for u in wanted:
            raw = self.messages[u]
            part = raw.split(b"\n\n", 1)[0] + b"\n\n" if "HEADER" in args[1] else raw
            data += [(f"{u} (UID {u} BODY[] {{{len(part)}}}".encode(), part), b")"]
        return "OK", data

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" INBOX', b'(\\HasNoChildren \\Drafts) "/" "&BCcENQRABD0EPgQyBDgEOgQ4-"']

    def append(self, folder, flags, date, message):
        self.appended.append((folder, flags, message))
        return "OK", [b"APPEND completed"]

    def logout(self):
        self.logged_out = True


IMAP_ACCOUNT = {"id": "work", "kind": "imap", "address": "me@zavod.ru", "imap_host": "imap.zavod.ru",
                "imap_port": 993}


def test_mail_servers_are_found_by_the_address():
    assert mail.autoconfig("ivanov@yandex.ru")["imap_host"] == "imap.yandex.ru"
    assert "паролем приложения" in mail.autoconfig("a@gmail.com")["hint"]
    xml = ('<clientConfig><emailProvider><incomingServer type="pop3"><hostname>pop.x.ru</hostname><port>995</port>'
           '</incomingServer><incomingServer type="imap"><hostname>mail.%EMAILDOMAIN%</hostname><port>993</port>'
           '</incomingServer><outgoingServer type="smtp"><hostname>smtp.x.ru</hostname><port>587</port>'
           '</outgoingServer></emailProvider></clientConfig>')
    found = mail.autoconfig("a@x.ru", fetch=lambda domain: xml)
    assert (found["imap_host"], found["imap_port"], found["smtp_port"], found["source"]) == ("mail.x.ru", 993, 587,
                                                                                             "ispdb")
    guess = mail.autoconfig("a@zavod.ru", fetch=lambda domain: "")
    assert guess["imap_host"] == "imap.zavod.ru" and guess["source"] == "guess"
    with pytest.raises(mail.MailError):
        mail.autoconfig("без собаки")


def test_an_imap_box_reads_without_marking_and_writes_only_drafts():
    fake = FakeImap({5: letter(5, attach=True), 6: letter(6, sender="=?utf-8?b?0KHQuNC00L7RgNC+0LI=?= <s@x.ru>",
                                                           subject="Счёт", body=None,
                                                           html_body="<p>Оплата <b>до 20.11</b></p>")})
    with mail.ImapBox(IMAP_ACCOUNT, "pw", factory=lambda h, p: fake) as box:
        state = {}
        first = box.new_letters(state)
        assert [l.uid for l in first] == ["5", "6"] and state == {"validity": "77", "last": 6}
        assert first[0].attachments and first[1].sender == "Сидоров <s@x.ru>" and fake.readonly is True
        assert box.new_letters(state) == []  # "7:*" gives the last letter again: not new
        fake.messages[7] = letter(7, subject="Срочно: болты")
        assert [l.subject for l in box.new_letters(state)] == ["Срочно: болты"] and state["last"] == 7
        assert [l.uid for l in box.search("петров болты")] == ["7"]
        text = box.read("6")
        assert "Тема: Счёт" in text and "Оплата до 20.11" in text and "<b>" not in text
        assert "Вложения: чертёж.pdf" in box.read("5")
        message = mail.build_draft(IMAP_ACCOUNT, "", "", "Будет в пятницу.", box.original("7"))
        assert box.draft(message) == "&BCcENQRABD0EPgQyBDgEOgQ4-"
    folder, flags, raw = fake.appended[0]
    assert flags == "\\Draft" and b"In-Reply-To: <m7@zavod.ru>" in raw and b"To: petrov@zavod.ru" in raw
    assert fake.logged_out
    with pytest.raises(mail.MailError, match="не пустил: \\[AUTHENTICATIONFAILED"):
        mail.ImapBox(IMAP_ACCOUNT, "wrong", factory=lambda h, p: FakeImap({})).open()
    with pytest.raises(mail.MailError, match="пароль"):
        mail.ImapBox(IMAP_ACCOUNT, "").open()


def test_the_mail_connector_writes_new_letters_and_offers_tools(tmp_path, journal):
    fake = FakeImap({5: letter(5)})
    opener = lambda account: mail.ImapBox(account, "pw", factory=lambda h, p: fake)  # noqa: E731
    got = []
    conn = mail.MailConnector({"accounts": [IMAP_ACCOUNT]}, opener=opener, state_file=tmp_path / "state.json")
    conn._emit = got.append
    conn.poll()
    assert [e.title for e in got] == ["Письмо от Петров <petrov@zavod.ru>: Рама 600.000"] and got[0].ref == "work:5"
    fake.messages[6] = letter(6, subject="Счёт", attach=True)
    conn2 = mail.MailConnector({"accounts": [IMAP_ACCOUNT]}, opener=opener, state_file=tmp_path / "state.json")
    conn2._emit = got.append
    conn2.poll()  # the state survives a restart: only the new letter
    assert [e.ref for e in got] == ["work:5", "work:6"] and "есть вложения" in got[1].detail
    tools = {t.name: t for t in conn2.tools()}
    assert {n: t.policy for n, t in tools.items()} == {"search": "auto", "read": "auto", "draft": "ask"}
    assert "[work:6]" in tools["search"].run({"query": "счёт"})
    assert "Добрый день!" in tools["read"].run({"id": "work:5"})
    assert "Отправит пользователь сам" in tools["draft"].run({"reply_to": "work:5", "body": "Принято."})
    assert b"Subject: Re: =?utf-8?" in fake.appended[0][2] or b"Re: " in fake.appended[0][2]
    broken = mail.MailConnector({"accounts": [dict(IMAP_ACCOUNT, imap_host="")]}, state_file=tmp_path / "s2.json")
    broken._emit = got.append
    broken.poll()
    assert not broken.ok and "сервер IMAP" in broken.state
    hub = make_hub(journal)
    for e in got:
        journal.add(e)
    since, until, label = period("что пришло на почту сегодня", got[0].time + 60)
    assert "Почта" in hub.summary(start_of_day(got[0].time), until + DAY, "за день")


class FakeItem:
    def __init__(self, entry, subject, sender="Петров", address="petrov@zavod.ru", body="Текст", files=()):
        self.EntryID, self.Subject, self.SenderName, self.SenderEmailAddress = entry, subject, sender, address
        self.Body, self.To, self.Class = body, "me", 43
        self.ReceivedTime = datetime.now()
        names = list(files)

        class Attachments:
            Count = len(names)

            @staticmethod
            def Item(i):
                return type("A", (), {"FileName": names[i - 1]})()

        self.Attachments = Attachments
        self.saved = False

    def Reply(self):
        reply = FakeItem("r", "RE: " + self.Subject, body="> " + self.Body)
        return reply

    def Save(self):
        self.saved = True


class FakeOutlook:
    def __init__(self, items):
        self.items, self.created = items, []
        outlook = self

        class Items(list):
            def Sort(self, key, descending):
                self.sort(key=lambda i: i.ReceivedTime, reverse=descending)

        class Inbox:
            UnReadItemCount = 2

            @property
            def Items(self):
                return Items(outlook.items)

        class Namespace:
            def GetDefaultFolder(self, n):
                return Inbox()

            def GetItemFromID(self, entry):
                return next(i for i in outlook.items if i.EntryID == entry)

        self.namespace = Namespace()

    def GetNamespace(self, name):
        return self.namespace

    def CreateItem(self, kind):
        item = FakeItem("new", "")
        self.created.append(item)
        return item


def test_outlook_of_this_computer_is_read_the_same_way():
    app = FakeOutlook([FakeItem("A1", "Рама", files=["спец.pdf"]), FakeItem("A2", "Болты")])
    with mail.OutlookBox({"id": "outlook", "kind": "outlook"}, app=app) as box:
        state = {}
        assert sorted(l.subject for l in box.new_letters(state)) == ["Болты", "Рама"]
        assert box.new_letters(state) == [] and box.unseen() == 2
        app.items.append(FakeItem("A3", "Счёт"))
        assert [l.uid for l in box.new_letters(state)] == ["A3"]
        assert "Вложения: спец.pdf" in box.read("A1") and box.search("болт")[0].uid == "A2"
        msg = mail.build_draft({"address": ""}, "x@y.ru", "Тема", "Привет")
        assert box.draft(msg) == "Черновики Outlook" and app.created[0].saved and app.created[0].To == "x@y.ru"


def test_the_window_adds_a_mailbox(app, monkeypatch):
    from assistant import secrets

    from desktop_browser.assistant_dialog import MailAccountDialog

    monkeypatch.setattr(secrets, "_cred", lambda: None)
    dialog = MailAccountDialog(taken={"ivanov"})
    dialog.address.setText("ivanov@mail.ru")
    dialog._autofill()
    assert dialog.host.text() == "imap.mail.ru" and dialog.login.text() == "ivanov@mail.ru"
    assert "внешнего приложения" in dialog.hint.text()
    dialog.password.setText("app-pass")
    account = dialog.value()
    assert account == {"id": "ivanov2", "kind": "imap", "address": "ivanov@mail.ru", "login": "ivanov@mail.ru",
                       "imap_host": "imap.mail.ru", "imap_port": 993}
    dialog.save_password(account)
    assert secrets.get_secret(mail.secret_name(account)) == "app-pass"
    dialog.kind.setCurrentIndex(1)
    assert dialog.value()["kind"] == "outlook" and not dialog.host.isEnabled()
    dialog.deleteLater()
