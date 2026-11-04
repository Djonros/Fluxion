"""Bots in messengers (package bots): Telegram, MAX and ВКонтакте on fake APIs, the
allow-list with its code, the commands, the jobs without KOMPAS, the runner and the
settings window."""
from __future__ import annotations

import json
import queue
import threading

import pytest

from bots import brain as brains
from bots import config, jobs
from bots.base import Attachment, BotError, Incoming
from bots.maxbot import Max
from bots.runner import Runner
from bots.telegram import Telegram
from bots.vk import Vk

TOKEN = "123456:SECRET-token"


class Response:
    def __init__(self, data=None, status=200, content=b""):
        self.data, self.status_code, self.content = data, status, content

    def json(self):
        if self.data is None:
            raise ValueError("no json")
        return self.data


class Session:
    """Records the requests; ``answer(verb, url, kwargs)`` gives the response."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def request(self, verb, url, **kwargs):
        self.calls.append((verb, url, kwargs))
        return self.answer(verb, url, kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


# ── Telegram ─────────────────────────────────────────────────────────────────

def telegram_api(verb, url, kw):
    method = url.rsplit("/", 1)[-1]
    if "/file/bot" in url:
        return Response(content=b"OggS")
    if method == "getMe":
        return Response({"ok": True, "result": {"username": "fluxion_bot"}})
    if method == "getUpdates":
        if kw["data"]["offset"]:
            return Response({"ok": True, "result": []})
        return Response({"ok": True, "result": [
            {"update_id": 7, "message": {"from": {"id": 11, "first_name": "Иван", "last_name": "Петров"},
                                         "chat": {"id": 11}, "text": "привет"}},
            {"update_id": 8, "message": {"from": {"id": 11, "first_name": "Иван"}, "chat": {"id": 11},
                                         "voice": {"file_id": "V1"}}},
            {"update_id": 9, "message": {"from": {"id": 12, "username": "anna"}, "chat": {"id": -5},
                                         "caption": "рама", "document": {"file_id": "D1", "file_name": "рама.pdf"}}},
            {"update_id": 10, "message": {"from": {"id": 99, "is_bot": True}, "chat": {"id": 1}, "text": "бот"}},
        ]})
    if method == "getFile":
        return Response({"ok": True, "result": {"file_path": "voice/file_1.oga"}})
    return Response({"ok": True, "result": {}})


def test_telegram_reads_messages_voice_and_documents_and_moves_the_offset(tmp_path):
    session = Session(telegram_api)
    bot = Telegram(TOKEN, session=session)
    assert bot.me() == "@fluxion_bot"
    got = bot.poll(timeout=1)
    assert [m.text for m in got] == ["привет", "", "рама"]  # the bot's own message is skipped
    assert got[0].name == "Иван Петров" and got[0].chat_id == "11"
    assert got[1].attachments[0].kind == "voice" and got[2].attachments[0].name == "рама.pdf"
    assert got[2].chat_id == "-5" and got[2].name == "anna"
    assert bot.offset == 11 and bot.poll(timeout=1) == []
    path = bot.download(got[1].attachments[0], tmp_path)
    assert path.read_bytes() == b"OggS"


def test_telegram_sends_long_text_in_parts_and_files_as_documents(tmp_path):
    session = Session(telegram_api)
    bot = Telegram(TOKEN, session=session)
    bot.send_text("11", ("строка\n" * 900).strip())
    texts = [kw["data"]["text"] for _, url, kw in session.calls if url.endswith("sendMessage")]
    assert len(texts) == 2 and all(len(t) <= Telegram.MAX_TEXT for t in texts)
    file = tmp_path / "отчёт.xlsx"
    file.write_bytes(b"xlsx")
    bot.send_file("11", file, "Отчёт")
    _, url, kw = session.calls[-1]
    assert url.endswith("sendDocument") and kw["files"]["document"][0] == "отчёт.xlsx"


def test_a_wrong_token_is_said_in_words_and_the_token_never_shows():
    bot = Telegram(TOKEN, session=Session(lambda *a: Response({"ok": False, "description": "Unauthorized"}, 401)))
    with pytest.raises(BotError, match="токен не принят"):
        bot.me()

    def offline(verb, url, kw):
        raise OSError(f"cannot connect to {url}")

    with pytest.raises(BotError) as caught:
        Telegram(TOKEN, session=Session(offline)).me()
    assert TOKEN not in str(caught.value) and "недоступен" in str(caught.value)
    with pytest.raises(BotError, match="нет токена"):
        Telegram("")


# ── MAX ──────────────────────────────────────────────────────────────────────

def test_max_long_polling_with_a_marker_and_answers_to_users_and_chats(tmp_path, monkeypatch):
    state = {"not_ready": 1}

    def api(verb, url, kw):
        assert kw.get("headers", {}).get("Authorization") == TOKEN or "upload" in url
        if url.endswith("/me"):
            return Response({"user_id": 1, "username": "fluxion_max"})
        if url.endswith("/updates"):
            if kw["params"].get("marker"):
                return Response({"updates": [], "marker": 6})
            return Response({"marker": 5, "updates": [
                {"update_type": "message_created", "message": {
                    "sender": {"user_id": 21, "name": "Ольга"}, "recipient": {"chat_type": "dialog", "chat_id": 300},
                    "body": {"text": "сколько весит рама", "attachments": [
                        {"type": "audio", "payload": {"url": "https://files/voice"}},
                        {"type": "file", "filename": "a.pdf", "payload": {"url": "https://files/a"}}]}}},
                {"update_type": "message_created", "message": {
                    "sender": {"user_id": 22, "first_name": "Пётр"}, "recipient": {"chat_type": "chat", "chat_id": 400},
                    "body": {"text": "в группе"}}},
                {"update_type": "bot_started"},
            ]})
        if url.endswith("/uploads"):
            return Response({"url": "https://upload.max/slot"})
        if url == "https://upload.max/slot":
            return Response({"token": "FILE-TOKEN"})
        if url.endswith("/messages"):
            if "attachments" in kw["json"] and state["not_ready"]:
                state["not_ready"] -= 1
                return Response({"code": "attachment.not.ready", "message": "attachment.not.ready"}, 400)
            return Response({"success": True})
        raise AssertionError(url)

    monkeypatch.setattr("bots.maxbot.time.sleep", lambda s: None)
    session = Session(api)
    bot = Max(TOKEN, session=session)
    assert bot.me() == "@fluxion_max"
    got = bot.poll(timeout=1)
    assert [(m.user_id, m.chat_id, m.name) for m in got] == [("21", "user:21", "Ольга"), ("22", "chat:400", "Пётр")]
    assert [a.kind for a in got[0].attachments] == ["voice", "file"]
    assert bot.marker == 5 and bot.poll(timeout=1) == [] and bot.marker == 6
    bot.send_text("user:21", "ответ")
    assert session.calls[-1][2]["params"]["user_id"] == "21"
    bot.send_text("chat:400", "ответ")
    assert session.calls[-1][2]["params"]["chat_id"] == "400"
    file = tmp_path / "a.xlsx"
    file.write_bytes(b"x")
    bot.send_file("user:21", file, "Отчёт")  # «not ready» once, then sent
    sent = session.calls[-1][2]["json"]
    assert sent["attachments"] == [{"type": "file", "payload": {"token": "FILE-TOKEN"}}]


# ── ВКонтакте ────────────────────────────────────────────────────────────────

def test_vk_bots_long_poll_reconnects_and_sends_documents(tmp_path):
    state = {"checks": 0}

    def api(verb, url, kw):
        if url.endswith("groups.getById"):
            return Response({"response": {"groups": [{"id": 77, "name": "Завод"}]}})
        if url.endswith("groups.getLongPollServer"):
            assert kw["data"]["group_id"] == "77"
            return Response({"response": {"server": "https://lp.vk/x", "key": "K", "ts": 10}})
        if url == "https://lp.vk/x":
            state["checks"] += 1
            if state["checks"] == 1:
                return Response({"failed": 2})
            return Response({"ts": "12", "updates": [
                {"type": "message_new", "object": {"message": {"from_id": 31, "peer_id": 31, "text": "привет",
                                                               "attachments": [{"type": "audio_message",
                                                                                "audio_message": {"link_ogg": "u"}}]}}},
                {"type": "message_new", "object": {"message": {"from_id": -77, "peer_id": 31, "text": "сам себе"}}},
            ]})
        if url.endswith("docs.getMessagesUploadServer"):
            return Response({"response": {"upload_url": "https://up.vk/doc"}})
        if url == "https://up.vk/doc":
            return Response({"file": "F"})
        if url.endswith("docs.save"):
            return Response({"response": {"type": "doc", "doc": {"owner_id": 31, "id": 5}}})
        if url.endswith("messages.send"):
            assert kw["data"]["random_id"] and kw["data"]["v"]
            return Response({"response": 1})
        raise AssertionError(url)

    session = Session(api)
    bot = Vk(TOKEN, session=session)
    assert bot.me() == "Завод"
    assert bot.poll(timeout=1) == [] and bot.server == ""  # an old key: a new server next time
    got = bot.poll(timeout=1)
    assert [(m.user_id, m.text) for m in got] == [("31", "привет")] and bot.ts == "12"
    assert got[0].attachments[0].kind == "voice"
    file = tmp_path / "a.zip"
    file.write_bytes(b"z")
    bot.send_file("31", file, "DXF")
    assert session.calls[-1][2]["data"]["attachment"] == "doc31_5"


def test_vk_error_in_the_body_is_an_error():
    bot = Vk(TOKEN, session=Session(lambda *a: Response({"error": {"error_code": 5,
                                                                    "error_msg": "User authorization failed"}})))
    with pytest.raises(BotError, match="ВКонтакте"):
        bot.me()


# ── settings and the allow-list ──────────────────────────────────────────────

def test_settings_are_saved_whole_and_a_code_adds_the_user(tmp_path):
    data = config.load()
    assert data["telegram"] == {"token": "", "enabled": False, "proxy": "", "allowed": []}
    data["telegram"].update(token=" T ", enabled=True)
    data["folders"] = [str(tmp_path)]
    config.save(data)
    assert config.load()["telegram"]["token"] == "T"
    code = config.new_code("telegram", "11", "Иван")
    assert len(code) == 6 and code.isdigit()
    assert config.new_code("telegram", "11", "Иван") == code  # asked twice: the same code
    assert config.confirm("000000" if code != "000000" else "111111") is None
    assert config.confirm(f" {code[:3]} {code[3:]} ")["name"] == "Иван"
    assert config.is_allowed(config.load(), "telegram", "11")
    assert config.confirm(code) is None  # used once
    assert config.allowed_file(tmp_path / "a" / "b.a3d", [str(tmp_path)])
    assert not config.allowed_file(tmp_path.parent / "other.a3d", [str(tmp_path)])


# ── the brain ────────────────────────────────────────────────────────────────

class Bot:
    platform = "telegram"

    def __init__(self):
        self.texts, self.files = [], []

    def send_text(self, chat, text):
        self.texts.append(text)

    def send_file(self, chat, path, caption=""):
        self.files.append(path)

    def download(self, attachment, folder):
        path = folder / attachment.name
        path.write_bytes(b"data")
        return path


class Host:
    def __init__(self):
        self.asked, self.chats, self.codes = [], [], []

    def ask(self, text, history, archive=False):
        self.asked.append((text, len(history), archive))
        return f"ответ на «{text}»"

    def backend(self):
        return None

    def transcribe(self, path):
        return "сколько весит рама"

    def save_chat(self, session):
        self.chats.append(session)

    def pending(self, platform, name, code):
        self.codes.append(code)

    def status(self):
        return "Fluxion открыт."

    def busy(self):
        return False


class Jobs:
    KOMPAS_LOCK = jobs.KOMPAS_LOCK

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.calls = []

    def find_model(self, query, folders, suffixes=jobs.MODEL_SUFFIXES):
        if query == "нет":
            raise jobs.JobError("Не нашёл «нет» в папках бота.")
        return self.tmp / f"{query}.a3d"

    def report(self, path, log=print, estimate_only=False):
        self.calls.append(("report", path.name, estimate_only))
        out = self.tmp / "отчёт.xlsx"
        out.write_bytes(b"x")
        return jobs.Result("Рама: масса 120 кг", [out])

    def compare_versions(self, old, new, log=print):
        self.calls.append(("compare", old.name, new.name))
        return jobs.Result("изменилось 2")

    def purchase(self, products, log=print):
        self.calls.append(("purchase", [(p.name, q) for p, q in products]))
        return jobs.Result("заявка")

    def laser(self, path, log=print):
        self.calls.append(("laser", path.name))
        return jobs.Result("DXF готовы")


def msg(text="", user="11", attachments=None):
    return Incoming("telegram", user, user, "Иван", text, attachments or [])


@pytest.fixture
def allowed(tmp_path):
    data = config.load()
    data["telegram"].update(token="T", enabled=True, allowed=[{"id": "11", "name": "Иван"}])
    data["folders"] = [str(tmp_path)]
    config.save(data)
    return tmp_path


def test_a_stranger_gets_only_a_code(allowed):
    host, bot = Host(), Bot()
    brains.Brain(host, pro=lambda f: True).handle(bot, msg("покажи отчёты", user="66"))
    assert host.codes and host.codes[0] in bot.texts[0] and host.asked == []


def test_a_question_goes_to_the_chat_and_into_the_history(allowed):
    host, bot = Host(), Bot()
    brain = brains.Brain(host, pro=lambda f: False)
    brain.handle(bot, msg("какой катет шва"))
    brain.handle(bot, msg("а для 10 мм"))
    assert bot.texts == ["ответ на «какой катет шва»", "ответ на «а для 10 мм»"]
    assert host.asked[1][1] == 2  # the first question and answer go along
    chat = host.chats[-1]
    assert chat["title"].startswith("[Telegram · Иван] какой катет") and len(chat["messages"]) == 4
    brain.handle(bot, msg("/новый"))
    brain.handle(bot, msg("ещё"))
    assert host.chats[-1]["id"] != chat["id"]


def test_pro_commands_say_so_without_a_licence_and_run_with_it(allowed):
    host, bot = Host(), Bot()
    fake = Jobs(allowed)
    brains.Brain(host, pro=lambda f: False, job_runner=fake).handle(bot, msg("/отчёт Рама"))
    assert "функция Pro" in bot.texts[-1] and fake.calls == []
    brain = brains.Brain(host, pro=lambda f: True, job_runner=fake)
    brain.handle(bot, msg("/report@fluxion_bot Рама"))
    assert fake.calls[-1] == ("report", "Рама.a3d", False)
    assert bot.texts[-1] == "Рама: масса 120 кг" and bot.files[-1].name == "отчёт.xlsx"
    brain.handle(bot, msg("/смета Рама"))
    assert fake.calls[-1] == ("report", "Рама.a3d", True)
    brain.handle(bot, msg("/сравнить Рама изм1 ; Рама изм2"))
    assert fake.calls[-1] == ("compare", "Рама изм1.a3d", "Рама изм2.a3d")
    brain.handle(bot, msg("/сравнить только одна"))
    assert "Две версии" in bot.texts[-1]
    brain.handle(bot, msg("/заявка Рама × 2; Стол х 5\nОпора"))
    assert fake.calls[-1] == ("purchase", [("Рама.a3d", 2), ("Стол.a3d", 5), ("Опора.a3d", 1)])
    brain.handle(bot, msg("/лазер Рама"))
    assert fake.calls[-1] == ("laser", "Рама.a3d")
    brain.handle(bot, msg("/отчёт нет"))
    assert bot.texts[-1] == "Не нашёл «нет» в папках бота."
    brain.handle(bot, msg("/непонятно"))
    assert bot.texts[-1].startswith("Не знаю такой команды")


def test_voice_is_heard_then_answered_and_files_are_kept(allowed, monkeypatch):
    monkeypatch.setattr(brains, "inbox", lambda: allowed / "inbox")
    host, bot = Host(), Bot()
    brain = brains.Brain(host, pro=lambda f: False)
    brain.handle(bot, msg(attachments=[Attachment("voice", "V1", "voice.ogg")]))
    assert bot.texts == ["🎙 сколько весит рама", "ответ на «сколько весит рама»"]
    brain.handle(bot, msg("вот чертёж", attachments=[Attachment("file", "D1", "рама.pdf")]))
    assert (allowed / "inbox" / "рама.pdf").is_file() and "сохранён" in bot.texts[-2]


def test_notifications_are_switched_by_the_user(allowed):
    host, bot = Host(), Bot()
    brain = brains.Brain(host, pro=lambda f: False)
    assert brain.subscribers(config.load()) == []
    brain.handle(bot, msg("/уведомления"))
    assert brain.subscribers(config.load()) == [("telegram", "11")]
    runner = Runner(brain, load=config.load)
    runner.adapters["telegram"] = bot
    assert runner.notify("отчёт готов") == 1 and bot.texts[-1] == "отчёт готов"
    brain.handle(bot, msg("/уведомления"))
    assert brain.subscribers(config.load()) == []


def test_commands_and_products_are_read():
    assert brains.command("/Отчёт  Рама 600") == ("report", "Рама 600")
    assert brains.command("просто вопрос") == ("", "просто вопрос")
    assert brains.products("Рама x2 ; Стол × 5 шт") == [("Рама", 2), ("Стол", 5)]


# ── jobs without KOMPAS ──────────────────────────────────────────────────────

def test_a_model_is_found_by_name_only_in_the_bot_folders(tmp_path):
    root = tmp_path / "Проекты"
    (root / "Рама").mkdir(parents=True)
    (root / "Рама" / "Рама 600.000.a3d").write_bytes(b"")
    (root / "Рама" / "Рама 600.100.a3d").write_bytes(b"")
    (root / "Стол.a3d").write_bytes(b"")
    folders = [str(root)]
    assert jobs.find_model("Стол", folders).name == "Стол.a3d"
    assert jobs.find_model("рама 600.000.a3d", folders).name == "Рама 600.000.a3d"
    with pytest.raises(jobs.JobError, match="несколько"):
        jobs.find_model("Рама", folders)
    with pytest.raises(jobs.JobError, match="вне папок"):
        jobs.find_model(str(tmp_path / "чужое.a3d"), folders)
    with pytest.raises(jobs.JobError, match="не заданы"):
        jobs.find_model("Стол", [])


class FakeLaserJob:
    def __init__(self, assembly, out_dir, auto=True):
        from pathlib import Path

        self.out_dir = Path(out_dir)
        self.queue = queue.Queue()
        self.answers = []

    def start(self):
        self.queue.put({"type": "TRAVERSAL_DONE", "parts": [{}, {}]})

    def export(self, parts):
        for msg in ({"type": "PAUSE", "title": "Деталь 3"}, {"type": "ASK_THICKNESS", "part": "Деталь 4"}):
            self.queue.put(msg)
        (self.out_dir / "5").mkdir(parents=True)
        (self.out_dir / "5" / "a.dxf").write_text("0\nEOF\n")
        self.queue.put({"type": "DONE", "text": "Экспорт завершён."})
        return 2

    def skip(self):
        self.answers.append("skip")

    def answer_thickness(self, value):
        self.answers.append(value)

    def cancel(self):
        pass


def test_the_laser_package_skips_what_needs_a_hand_and_zips_the_dxf(tmp_path, monkeypatch):
    import cad.laser

    monkeypatch.setattr(cad.laser, "default_out_dir", lambda path: tmp_path / "DXF")
    done = jobs.laser(tmp_path / "Рама.a3d", job_factory=FakeLaserJob)
    assert "5 мм — 1 DXF" in done.text and "Пропущено" in done.text and "Деталь 3" in done.text
    assert done.files[0].name == "Рама - DXF.zip"
    import zipfile

    assert zipfile.ZipFile(done.files[0]).namelist() == ["5/a.dxf"]


# ── the runner ───────────────────────────────────────────────────────────────

class PollingBot(Bot):
    def __init__(self, runner_box, rejected=False):
        super().__init__()
        self.runner_box = runner_box
        self.rejected = rejected
        self.polls = 0

    def me(self):
        if self.rejected:
            raise BotError("Telegram: токен не принят (Unauthorized)")
        return "@fluxion_bot"

    def poll(self, timeout=25):
        self.polls += 1
        if self.polls == 1:
            return [msg("привет")]
        self.runner_box[0].stop_event.wait(0.05)
        return []


def test_the_runner_answers_and_stops_a_bot_with_a_wrong_token(allowed):
    host = Host()
    states = []
    answered = threading.Event()

    class Brain(brains.Brain):
        def handle(self, adapter, message):
            super().handle(adapter, message)
            answered.set()

    box = []
    rejected = {"value": False}
    runner = Runner(Brain(host, pro=lambda f: False), state=lambda p, t, ok: states.append((p, t, ok)),
                    make_adapter=lambda p, token, proxy: PollingBot(box, rejected["value"]))
    box.append(runner)
    assert runner.start() == ["telegram"]
    assert answered.wait(5)
    runner.stop(wait=5)
    assert states[0] == ("telegram", "работает: @fluxion_bot", True)
    rejected["value"] = True
    runner.start()
    for _ in range(100):
        if runner.running() == []:
            break
        threading.Event().wait(0.05)
    assert ("telegram", "Telegram: токен не принят (Unauthorized)", False) in states


# ── the settings window ──────────────────────────────────────────────────────

def test_the_settings_window_keeps_tokens_users_and_folders(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication

        from desktop_browser.bots_dialog import BotsDialog
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    QApplication.instance() or QApplication([])
    data = config.load()
    data["vk"]["allowed"] = [{"id": "31", "name": "Ольга", "notify": True, "chat": "31"}]
    config.save(data)
    code = config.new_code("telegram", "11", "Иван")
    dialog = BotsDialog()
    assert "Иван" in dialog.confirm_label.text()
    dialog.platform_tabs["telegram"].token.setText(" 1:ABC ")
    dialog.platform_tabs["telegram"].enabled.setChecked(True)
    dialog.code.setText(code)
    dialog._confirm()
    assert "Подтверждён" in dialog.confirm_label.text()
    dialog.folders.addItem(str(tmp_path))
    dialog.save()
    saved = json.loads(config.path().read_text(encoding="utf-8"))
    assert saved["telegram"]["token"] == "1:ABC" and saved["telegram"]["enabled"]
    assert saved["telegram"]["allowed"] == [{"id": "11", "name": "Иван"}]
    assert saved["vk"]["allowed"][0]["notify"] is True  # «/уведомления» survive the window
    assert saved["folders"] == [str(tmp_path)]
    dialog.deleteLater()


def test_audio_of_a_wav_is_read_without_ffmpeg(tmp_path):
    import wave

    import numpy as np

    from bots.audio import decode

    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.zeros(1600, dtype=np.int16)).tobytes())
    assert len(decode(path)) == 1600

@pytest.mark.parametrize("fmt, subtype, rate", [("OGG", "OPUS", 48000), ("MP3", None, 44100)])
def test_voice_of_a_messenger_is_read_without_ffmpeg(tmp_path, monkeypatch, fmt, subtype, rate):
    """Telegram, MAX and VK send OGG/Opus (VK also MP3): libsndfile reads them, 16 kHz for the speech model."""
    import numpy as np

    soundfile = pytest.importorskip("soundfile")
    from bots import audio

    monkeypatch.setattr(audio.shutil, "which", lambda name: None)  # no ffmpeg on this PC
    seconds = 1.0
    t = np.arange(int(rate * seconds)) / rate
    path = tmp_path / ("voice.ogg" if fmt == "OGG" else "voice.mp3")
    soundfile.write(str(path), 0.3 * np.sin(2 * np.pi * 300 * t), rate, format=fmt, subtype=subtype)
    data = audio.decode(path)
    assert abs(len(data) - audio.RATE * seconds) < audio.RATE * 0.1
    assert data.dtype == np.float32 and 0.1 < float(np.abs(data).max()) < 0.5


def test_unknown_sound_says_what_to_send(tmp_path, monkeypatch):
    from bots import audio

    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    monkeypatch.setattr(audio, "_pyav", lambda path: None)
    path = tmp_path / "song.m4a"
    path.write_bytes(bytes([0, 0, 0, 24]) + b"ftypM4A ")
    with pytest.raises(audio.AudioError, match="голосовое сообщение"):
        audio.decode(path)

def test_proxy_text_becomes_a_url():
    from bots.base import proxy_url

    assert proxy_url("") == ""
    assert proxy_url(" 10.0.0.1:3128 ") == "http://10.0.0.1:3128"
    assert proxy_url("socks://10.0.0.1:1080") == "socks5://10.0.0.1:1080"
    assert proxy_url("socks5://user:pass@host:1080") == "socks5://user:pass@host:1080"


@pytest.mark.parametrize("proxy", ["socks5://127.0.0.1:1080", "socks5h://u:p@127.0.0.1:1080", "127.0.0.1:3128"])
def test_bot_session_goes_through_socks_or_http_proxy(proxy):
    pytest.importorskip("socksio")
    from bots.telegram import Telegram

    bot = Telegram("1:ABC", proxy)
    assert bot.session is not None
    bot.session.close()


def test_a_proxy_with_an_unknown_scheme_is_said_in_words():
    from bots.base import BotError
    from bots.telegram import Telegram

    with pytest.raises(BotError, match="socks5://"):
        Telegram("1:ABC", "ftp://host:21")

def test_the_zip_for_the_bot_leaves_the_bend_lines_out(tmp_path):
    import zipfile

    from bots.jobs import zip_folder

    (tmp_path / "pkg" / "5" / "_гибы").mkdir(parents=True)
    (tmp_path / "pkg" / "5" / "a.dxf").write_text("0", encoding="utf-8")
    (tmp_path / "pkg" / "5" / "_гибы" / "a.dxf").write_text("0", encoding="utf-8")
    target = zip_folder(tmp_path / "pkg", tmp_path / "pkg.zip")
    assert zipfile.ZipFile(target).namelist() == ["5/a.dxf"]
