"""What a bot does with a message — the same for Telegram, MAX and ВКонтакте.

A user not in the allow-list gets a six-digit code and nothing else. Then:

    a question                  the chat of Fluxion (its model, the project index)
    a voice message             recognised by the speech model of Fluxion, then as text
    /расчёт …                   the engineering calculator (questions about what is missing)
    /компас …                   a part built in KOMPAS-3D by description, the .m3d sent back
    /архив …                    the design archive (Pro)
    /отчёт, /смета …            the assembly report in Excel (Pro)
    /лазер …                    «Пакет на лазер»: DXF by thickness in a zip (Pro)
    /сравнить A ; B             «Сравнить версии сборки» (Pro)
    /заявка A × 2 ; B × 5       «Заявка на закупку» (Pro)
    /файл …                     a file from the folders open to the bot
    /статус, /новый, /уведомления, /отмена, /помощь

The texts are Russian, as the reports and calculations of Fluxion are. ``host`` is
the window (or a fake in tests): ``ask(text, history, archive)``, ``backend()``,
``transcribe(path)``, ``save_chat(session)``, ``pending(platform, name, code)``,
``status()``, ``busy()``.
"""
from __future__ import annotations

import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import config, jobs
from .base import Adapter, BotError, Incoming

HISTORY = 20  # messages of a bot chat given to the model
SEND_LIMIT = 45 * 1024 * 1024  # Telegram takes documents up to 50 MB
REPORT_FEATURE = "assembly_report"
LASER_FEATURE = "laser_dxf"
ARCHIVE_FEATURE = "design_archive"

COMMANDS = {
    "help": ("/помощь", "/help", "/start"),
    "status": ("/статус", "/status"),
    "new": ("/новый", "/new"),
    "cancel": ("/отмена", "/cancel"),
    "notify": ("/уведомления", "/notify"),
    "calc": ("/расчёт", "/расчет", "/calc"),
    "cad": ("/компас", "/kompas"),
    "archive": ("/архив", "/archive"),
    "report": ("/отчёт", "/отчет", "/report"),
    "estimate": ("/смета", "/estimate"),
    "laser": ("/лазер", "/laser"),
    "compare": ("/сравнить", "/compare"),
    "purchase": ("/заявка", "/purchase"),
    "file": ("/файл", "/file"),
}

HELP = """Fluxion на вашем компьютере. Пишите вопрос — отвечу, как в чате программы; можно голосом.
/расчёт шов катет 6 длиной 200, сила 30 кН — инженерный расчёт
/компас втулка 30 на 20 длиной 50, бронза — деталь в КОМПАС-3D
/архив где рама 600.000 — поиск по архиву КД (Pro)
/отчёт Рама 600.000 — ведомость и металлоёмкость сборки в Excel (Pro)
/смета Рама 600.000 — себестоимость по нормам завода (Pro)
/лазер Рама 600.000 — DXF развёрток по толщинам, архивом (Pro)
/сравнить Рама изм1 ; Рама изм2 — что изменилось (Pro)
/заявка Рама × 2 ; Стол × 5 — что закупить (Pro)
/файл Рама.pdf — прислать файл из папок бота
/статус — что с программой; /новый — новый разговор; /уведомления — сообщать о концах заданий
Сборку можно указать путём или именем файла: ищу в папках, открытых для бота."""

PRO_TEXT = ("«{title}» — функция Pro. Вопросы, расчёты и детали в КОМПАС — бесплатно. "
            "Активировать: в программе меню «Помощь» → «Лицензия…».")

_PRODUCT_RE = re.compile(r"^(.*?)(?:\s*[×xх*]\s*(\d+)\s*(?:шт\.?)?)?\s*$", re.IGNORECASE)


def command(text: str) -> tuple[str, str]:
    """("report", "Рама 600.000") for "/отчёт Рама 600.000"; ("", text) for a question."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return "", stripped
    head, _, rest = stripped.partition(" ")
    head = head.split("@", 1)[0].lower()  # Telegram groups: /report@fluxion_bot
    for name, words in COMMANDS.items():
        if head in words:
            return name, rest.strip()
    return "unknown", stripped


def split_list(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;\n]|\s+→\s+|\s+->\s+", text) if p.strip()]


def products(text: str) -> list[tuple[str, int]]:
    """"Рама × 2; Стол х 5" -> [("Рама", 2), ("Стол", 5)]."""
    out = []
    for part in split_list(text):
        match = _PRODUCT_RE.match(part)
        name, qty = match.group(1).strip(), int(match.group(2) or 1)
        if name:
            out.append((name, max(qty, 1)))
    return out


def inbox() -> Path:
    """Where the files sent to a bot are kept."""
    return Path.home() / "Documents" / "Fluxion" / "Из мессенджеров"


@dataclass
class Dialog:
    platform: str
    user_id: str
    name: str
    session: dict = field(default_factory=dict)
    calc: object = None  # calc.chat.Conversation waiting for an answer
    cad: object = None  # cad.text2cad.Conversation waiting for an answer
    lock: threading.Lock = field(default_factory=threading.Lock)

    def new_session(self) -> None:
        now = time.time()
        self.session = {"id": uuid.uuid4().hex, "title": "", "created": now, "updated": now, "messages": []}

    def history(self) -> list[dict]:
        return [dict(m) for m in self.session.get("messages", [])[-HISTORY:]]


class Brain:
    def __init__(self, host, pro=None, load=config.load, save=config.save, job_runner=jobs):
        self.host = host
        self._pro = pro
        self.load = load
        self.save = save
        self.jobs = job_runner
        self._dialogs: dict[tuple[str, str], Dialog] = {}
        self._lock = threading.Lock()

    def pro(self, feature: str) -> bool:
        if self._pro is not None:
            return bool(self._pro(feature))
        from licensing import feature_enabled

        return bool(feature_enabled(feature))

    def dialog(self, msg: Incoming) -> Dialog:
        with self._lock:
            key = (msg.platform, msg.user_id)
            if key not in self._dialogs:
                self._dialogs[key] = Dialog(msg.platform, msg.user_id, msg.name)
                self._dialogs[key].new_session()
            return self._dialogs[key]

    # ── a message ────────────────────────────────────────────────────────────

    def handle(self, adapter: Adapter, msg: Incoming) -> None:
        """Answer *msg* through *adapter*; never raises (a failure is said to the user)."""
        data = self.load()
        if not config.is_allowed(data, msg.platform, msg.user_id):
            code = config.new_code(msg.platform, msg.user_id, msg.name)
            self.host.pending(msg.platform, msg.name, code)
            self._send(adapter, msg, f"Это бот Fluxion. Вас ещё нет в списке разрешённых.\n"
                                     f"Код подтверждения: {code}\nВладелец компьютера вводит его в Fluxion: "
                                     "Инструменты → «Боты в мессенджерах…» → «Подтвердить». Код действует 15 минут.")
            return
        dialog = self.dialog(msg)
        with dialog.lock:  # one message of a user at a time, in order
            try:
                self._handle(adapter, msg, dialog, data)
            except jobs.JobError as exc:
                self._reply(adapter, msg, dialog, str(exc))
            except BotError:
                raise
            except Exception as exc:  # the user hears what went wrong; the bot goes on
                self._reply(adapter, msg, dialog, f"Не получилось: {type(exc).__name__}: {exc}")

    def _handle(self, adapter: Adapter, msg: Incoming, dialog: Dialog, data: dict) -> None:
        text = msg.text.strip()
        for attachment in msg.attachments:
            if attachment.kind == "voice":
                heard = self._hear(adapter, attachment)
                if not heard:
                    self._send(adapter, msg, "Не разобрал голосовое сообщение — напишите текстом.")
                    return
                self._send(adapter, msg, f"🎙 {heard}")
                text = f"{text}\n{heard}".strip()
            elif attachment.kind == "file":
                folder = inbox()
                folder.mkdir(parents=True, exist_ok=True)
                saved = adapter.download(attachment, folder)
                self._reply(adapter, msg, dialog, f"Файл сохранён на компьютере: {saved}", asked=f"[файл {saved.name}]")
        if not text:
            return
        name, rest = command(text)
        if not name:
            if dialog.cad is not None:
                return self._cad(adapter, msg, dialog, text)
            if dialog.calc is not None:
                return self._calc(adapter, msg, dialog, text)
            return self._ask(adapter, msg, dialog, text)
        if name != "cancel":
            dialog.cad = dialog.calc = None  # a new command ends a question left open
        getattr(self, "_cmd_" + name)(adapter, msg, dialog, rest, data)

    # ── commands ─────────────────────────────────────────────────────────────

    def _cmd_unknown(self, adapter, msg, dialog, rest, data):
        self._send(adapter, msg, "Не знаю такой команды.\n\n" + HELP)

    def _cmd_help(self, adapter, msg, dialog, rest, data):
        self._send(adapter, msg, HELP)

    def _cmd_status(self, adapter, msg, dialog, rest, data):
        folders = data["folders"]
        lines = [self.host.status(),
                 "Pro: " + ("активна" if self.pro(REPORT_FEATURE) else "нет (вопросы и расчёты — бесплатно)"),
                 "КОМПАС: " + ("занят заданием" if self.host.busy() or jobs.KOMPAS_LOCK.locked() else "свободен"),
                 "Папки бота: " + ("; ".join(folders) if folders else "не заданы")]
        self._send(adapter, msg, "\n".join(line for line in lines if line))

    def _cmd_new(self, adapter, msg, dialog, rest, data):
        dialog.new_session()
        self._send(adapter, msg, "Начинаем новый разговор.")

    def _cmd_cancel(self, adapter, msg, dialog, rest, data):
        waiting = dialog.cad is not None or dialog.calc is not None
        dialog.cad = dialog.calc = None
        self._send(adapter, msg, "Отменил." if waiting else "Нечего отменять.")

    def _cmd_notify(self, adapter, msg, dialog, rest, data):
        user = next(u for u in data[msg.platform]["allowed"] if u["id"] == msg.user_id)
        user["notify"], user["chat"] = not user.get("notify"), msg.chat_id
        self.save(data)
        self._send(adapter, msg, "Буду сообщать, когда в программе закончится отчёт, пакет на лазер, сравнение "
                                 "или заявка." if user["notify"] else "Уведомления выключены.")

    def _cmd_calc(self, adapter, msg, dialog, rest, data):
        if not rest:
            return self._send(adapter, msg, "Напишите задачу: /расчёт шов катет 6 длиной 200, сила 30 кН")
        from calc.chat import Conversation

        dialog.calc = Conversation(self.host.backend())
        self._calc(adapter, msg, dialog, rest, shown=f"/расчёт {rest}")

    def _cmd_cad(self, adapter, msg, dialog, rest, data):
        if not rest:
            return self._send(adapter, msg, "Опишите деталь: /компас втулка 30 на 20 длиной 50, фаска 1, бронза")
        from cad.text2cad import Conversation

        dialog.cad = Conversation(self.host.backend())
        self._cad(adapter, msg, dialog, rest, shown=f"/компас {rest}")

    def _cmd_archive(self, adapter, msg, dialog, rest, data):
        if not self._need_pro(adapter, msg, ARCHIVE_FEATURE, "Архив КД"):
            return
        if not rest:
            return self._send(adapter, msg, "Напишите, что найти: /архив рама 600.000")
        self._ask(adapter, msg, dialog, rest, archive=True, shown=f"/архив {rest}")

    def _cmd_report(self, adapter, msg, dialog, rest, data, estimate_only=False):
        title = "Смета" if estimate_only else "Ведомость и металлоёмкость"
        if not self._need_pro(adapter, msg, REPORT_FEATURE, title):
            return
        path = self.jobs.find_model(rest, data["folders"])
        self._job(adapter, msg, dialog, f"{title}: {path.name}",
                  lambda log: self.jobs.report(path, log=log, estimate_only=estimate_only))

    def _cmd_estimate(self, adapter, msg, dialog, rest, data):
        self._cmd_report(adapter, msg, dialog, rest, data, estimate_only=True)

    def _cmd_laser(self, adapter, msg, dialog, rest, data):
        if not self._need_pro(adapter, msg, LASER_FEATURE, "Пакет на лазер"):
            return
        path = self.jobs.find_model(rest, data["folders"])
        self._job(adapter, msg, dialog, f"Пакет на лазер: {path.name}", lambda log: self.jobs.laser(path, log=log))

    def _cmd_compare(self, adapter, msg, dialog, rest, data):
        if not self._need_pro(adapter, msg, REPORT_FEATURE, "Сравнить версии сборки"):
            return
        names = split_list(rest)
        if len(names) != 2:
            return self._send(adapter, msg, "Две версии через «;»: /сравнить Рама изм1 ; Рама изм2")
        old, new = (self.jobs.find_model(n, data["folders"], jobs.VERSION_SUFFIXES) for n in names)
        self._job(adapter, msg, dialog, f"Сравнить версии: {old.name} → {new.name}",
                  lambda log: self.jobs.compare_versions(old, new, log=log))

    def _cmd_purchase(self, adapter, msg, dialog, rest, data):
        if not self._need_pro(adapter, msg, REPORT_FEATURE, "Заявка на закупку"):
            return
        wanted = products(rest)
        if not wanted:
            return self._send(adapter, msg, "Изделия и количества через «;»: /заявка Рама × 2 ; Стол × 5")
        found = [(self.jobs.find_model(n, data["folders"], jobs.VERSION_SUFFIXES), qty) for n, qty in wanted]
        self._job(adapter, msg, dialog, "Заявка на закупку: " + ", ".join(f"{p.name} × {q}" for p, q in found),
                  lambda log: self.jobs.purchase(found, log=log))

    def _cmd_file(self, adapter, msg, dialog, rest, data):
        suffixes = tuple(s for s in (Path(rest).suffix.lower(),) if s) or (
            ".pdf", ".xlsx", ".dxf", ".dwg", ".a3d", ".m3d", ".cdw", ".spw", ".docx", ".zip", ".png", ".jpg")
        path = self.jobs.find_model(rest, data["folders"], suffixes)
        if path.stat().st_size > SEND_LIMIT:
            return self._send(adapter, msg, f"{path.name} больше 45 МБ — мессенджер такой не примет.")
        adapter.send_file(msg.chat_id, path, path.name)
        self._remember(dialog, f"/файл {rest}", f"Отправлен файл: {path}")

    # ── the parts ────────────────────────────────────────────────────────────

    def _need_pro(self, adapter, msg, feature: str, title: str) -> bool:
        if self.pro(feature):
            return True
        self._send(adapter, msg, PRO_TEXT.format(title=title))
        return False

    def _ask(self, adapter, msg, dialog, text, archive=False, shown=None):
        answer = self.host.ask(text, dialog.history(), archive)
        self._reply(adapter, msg, dialog, answer or "Модель не ответила.", asked=shown or text)

    def _calc(self, adapter, msg, dialog, text, shown=None):
        from calc.chat import explain

        reply = dialog.calc.step(text)
        if reply.kind == "ask":
            return self._reply(adapter, msg, dialog, reply.text, asked=shown or text)
        dialog.calc = None
        answer = reply.text
        if reply.kind == "result":
            comment = explain(reply.result, self.host.backend())
            if comment:
                answer += "\n\nПояснение (модель): " + comment
        self._reply(adapter, msg, dialog, answer, asked=shown or text)

    def _cad(self, adapter, msg, dialog, text, shown=None):
        from cad import kompas
        from cad.text2cad import result_text

        reply = dialog.cad.step(text)
        if reply.kind != "build":
            if reply.kind != "ask":
                dialog.cad = None
            return self._reply(adapter, msg, dialog, reply.text, asked=shown or text)
        dialog.cad = None
        part = reply.part
        self._send(adapter, msg, reply.text + "\nСтрою в КОМПАС-3D…")
        with jobs.kompas(lambda line: self._send(adapter, msg, line)):
            try:
                result = kompas.build_part(part, kompas.part_path(part["name"]), log=lambda line: None)
            except kompas.KompasError as exc:
                raise jobs.JobError(str(exc)) from exc
        self._reply(adapter, msg, dialog, result_text(part, result), asked=shown or text)
        for file in (result.path, result.dxf_path):
            if file and Path(file).is_file():
                adapter.send_file(msg.chat_id, Path(file), Path(file).name)

    def _job(self, adapter, msg, dialog, title: str, run) -> None:
        if self.host.busy():
            self._send(adapter, msg, "В программе идёт задание в КОМПАС — начну, как оно закончится.")
        self._send(adapter, msg, f"Принял: {title}. Это может занять несколько минут — пришлю результат.")
        done = run(lambda line: None)
        self._reply(adapter, msg, dialog, done.text, asked=title)
        for file in done.files:
            if Path(file).stat().st_size > SEND_LIMIT:
                self._send(adapter, msg, f"{Path(file).name} больше 45 МБ — не отправляю, он на компьютере: {file}")
                continue
            adapter.send_file(msg.chat_id, Path(file), Path(file).name)

    def _hear(self, adapter, attachment) -> str:
        with tempfile.TemporaryDirectory(prefix="fluxion-bot-") as folder:
            path = adapter.download(attachment, Path(folder))
            try:
                return (self.host.transcribe(path) or "").strip()
            except Exception as exc:
                raise jobs.JobError(f"Не разобрал голосовое сообщение ({exc}) — напишите текстом.") from exc

    def _send(self, adapter, msg, text: str) -> None:
        adapter.send_text(msg.chat_id, text)

    def _reply(self, adapter, msg, dialog, text: str, asked: str | None = None) -> None:
        self._send(adapter, msg, text)
        self._remember(dialog, asked if asked is not None else msg.text, text)

    def _remember(self, dialog: Dialog, asked: str, answer: str) -> None:
        """The bot chat goes into the chat history of Fluxion, marked by its messenger."""
        session = dialog.session
        session["messages"] += [{"role": "user", "content": asked}, {"role": "assistant", "content": answer}]
        session["updated"] = time.time()
        if not session.get("title"):
            first = " ".join(asked.split())[:60]
            session["title"] = f"[{config.TITLES[dialog.platform]} · {dialog.name}] {first}"
        try:
            self.host.save_chat(dict(session, messages=list(session["messages"])))
        except Exception:  # the history is a convenience: the answer is already sent
            pass

    # ── notifications ────────────────────────────────────────────────────────

    @staticmethod
    def subscribers(data: dict) -> list[tuple[str, str]]:
        """(platform, chat) of the allowed users who asked for notifications, on enabled bots."""
        return [(p, u["chat"]) for p in config.PLATFORMS if data[p]["enabled"] and data[p]["token"]
                for u in data[p]["allowed"] if u.get("notify")]
