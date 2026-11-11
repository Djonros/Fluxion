"""Коннектор «Почта»: новые письма любого ящика в журнале, поиск, чтение и черновики ответов.

    settings = {"accounts": [
        {"id": "work", "address": "ivanov@zavod.ru", "kind": "imap", "login": "ivanov@zavod.ru",
         "imap_host": "imap.zavod.ru", "imap_port": 993},
        {"id": "outlook", "address": "", "kind": "outlook"}],      # the Outlook of this computer
     "interval": 120}                                               # seconds between the checks

IMAP is what almost any mail service speaks (Яндекс, Mail.ru, Gmail with an app password,
a company server); the password is kept in the Windows Credential Manager (``secrets``).
``outlook`` reads the Outlook installed here (Exchange, Microsoft 365) through COM, without
a password of its own. The mailbox is opened read-only: letters stay unread.

The journal gets the sender, the subject and the time of a new letter — never its text.
The assistant reads a letter only when asked (``mail_read``) and writes only drafts
(``mail_draft``, after the user's «Да»): Fluxion never sends a letter itself.
"""
from __future__ import annotations

import email
import email.policy
import html
import imaplib
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path

from . import secrets
from .apps import AppTool
from .connector import Connector, Event

INTERVAL = 120
SEARCH_LIMIT = 300   # letters looked through by a search
READ_LIMIT = 8000    # characters of a letter given to the model
FIRST_LOOK = 20      # letters of the last days written to the journal at the first start

KNOWN = {  # domain → (IMAP host, port, SMTP host, port)
    "yandex.ru": ("imap.yandex.ru", 993, "smtp.yandex.ru", 465),
    "ya.ru": ("imap.yandex.ru", 993, "smtp.yandex.ru", 465),
    "yandex.com": ("imap.yandex.com", 993, "smtp.yandex.com", 465),
    "mail.ru": ("imap.mail.ru", 993, "smtp.mail.ru", 465),
    "bk.ru": ("imap.mail.ru", 993, "smtp.mail.ru", 465),
    "inbox.ru": ("imap.mail.ru", 993, "smtp.mail.ru", 465),
    "list.ru": ("imap.mail.ru", 993, "smtp.mail.ru", 465),
    "internet.ru": ("imap.mail.ru", 993, "smtp.mail.ru", 465),
    "rambler.ru": ("imap.rambler.ru", 993, "smtp.rambler.ru", 465),
    "gmail.com": ("imap.gmail.com", 993, "smtp.gmail.com", 465),
    "googlemail.com": ("imap.gmail.com", 993, "smtp.gmail.com", 465),
    "outlook.com": ("outlook.office365.com", 993, "smtp.office365.com", 587),
    "hotmail.com": ("outlook.office365.com", 993, "smtp.office365.com", 587),
    "live.com": ("outlook.office365.com", 993, "smtp.office365.com", 587),
    "icloud.com": ("imap.mail.me.com", 993, "smtp.mail.me.com", 587),
    "me.com": ("imap.mail.me.com", 993, "smtp.mail.me.com", 587),
    "yahoo.com": ("imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465),
    "gmx.com": ("imap.gmx.com", 993, "mail.gmx.com", 465),
    "gmx.de": ("imap.gmx.net", 993, "mail.gmx.net", 465),
    "zoho.com": ("imap.zoho.com", 993, "smtp.zoho.com", 465),
    "ukr.net": ("imap.ukr.net", 993, "smtp.ukr.net", 465),
}
HINTS = {
    "gmail.com": "Gmail пускает по IMAP только с паролем приложения: myaccount.google.com → Безопасность → "
                 "Двухэтапная аутентификация → Пароли приложений.",
    "yandex.ru": "В Яндекс Почте: Все настройки → Почтовые программы → разрешить IMAP; пароль — пароль приложения "
                 "(id.yandex.ru → Безопасность → Пароли приложений).",
    "mail.ru": "В Mail.ru нужен пароль для внешнего приложения: Настройки → Безопасность → Пароли для внешних "
               "приложений.",
    "outlook.com": "Outlook.com и Microsoft 365 закрыли вход по паролю: подключите ящик в Outlook на компьютере и "
                   "выберите тип «Outlook на этом компьютере».",
}
for _d in ("ya.ru", "yandex.com"):
    HINTS[_d] = HINTS["yandex.ru"]
for _d in ("bk.ru", "inbox.ru", "list.ru", "internet.ru"):
    HINTS[_d] = HINTS["mail.ru"]
for _d in ("hotmail.com", "live.com"):
    HINTS[_d] = HINTS["outlook.com"]
HINTS["googlemail.com"] = HINTS["gmail.com"]


class MailError(RuntimeError):
    """Said to the user as it is."""


@dataclass
class Letter:
    uid: str
    sender: str
    subject: str
    time: float
    attachments: bool = False
    message_id: str = ""
    to: str = ""


def _text(value) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value)))).strip()
    except Exception:
        return str(value).strip()


def sender_name(raw: str) -> str:
    name, addr = parseaddr(_text(raw))
    return f"{name} <{addr}>" if name and addr else (addr or name or "?")


def letter_from_headers(uid: str, raw: bytes) -> Letter:
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    try:
        when = parsedate_to_datetime(msg.get("Date")).timestamp()
    except Exception:
        when = time.time()
    ctype = (msg.get_content_type() or "").lower()
    return Letter(uid, sender_name(msg.get("From", "")), _text(msg.get("Subject")) or "(без темы)", when,
                  ctype == "multipart/mixed", _text(msg.get("Message-ID")), _text(msg.get("To")))


def html_to_text(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def message_text(raw: bytes) -> tuple[str, list[str]]:
    """(the text of a letter, the names of its attachments)."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    attachments = [_text(p.get_filename()) for p in msg.iter_attachments() if p.get_filename()]
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        try:
            text = body.get_content()
        except Exception:
            text = body.get_payload(decode=True).decode("utf-8", "replace") if body.get_payload(decode=True) else ""
        if body.get_content_type() == "text/html":
            text = html_to_text(text)
    return text.strip(), attachments


def autoconfig(address: str, fetch=None) -> dict:
    """The servers of *address*: known services, then the Mozilla ISPDB (only the domain is sent),
    then a guess ``imap.<domain>``. ``{"imap_host", "imap_port", "smtp_host", "smtp_port", "hint", "source"}``."""
    domain = address.rpartition("@")[2].strip().lower() if "@" in address else ""
    if not domain or "." not in domain:
        raise MailError("нет домена в адресе")
    hint = HINTS.get(domain, "")
    if domain in KNOWN:
        ih, ip, sh, sp = KNOWN[domain]
        return {"imap_host": ih, "imap_port": ip, "smtp_host": sh, "smtp_port": sp, "hint": hint, "source": "known"}
    try:
        xml = (fetch or _fetch_ispdb)(domain)
    except Exception:
        xml = ""
    found = _parse_ispdb(xml, address) if xml else None
    if found:
        return dict(found, hint=hint, source="ispdb")
    return {"imap_host": f"imap.{domain}", "imap_port": 993, "smtp_host": f"smtp.{domain}", "smtp_port": 465,
            "hint": "Настройки угаданы: если не подключится, возьмите адрес сервера у администратора почты.",
            "source": "guess"}


def _fetch_ispdb(domain: str) -> str:
    import httpx

    resp = httpx.get(f"https://autoconfig.thunderbird.net/v1.1/{domain}", timeout=10)
    return resp.text if resp.status_code == 200 else ""


def _parse_ispdb(xml: str, address: str) -> dict | None:
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    out = {}
    for tag, kind in (("incomingServer", "imap"), ("outgoingServer", "smtp")):
        for server in root.iter(tag):
            if server.get("type") != kind:
                continue
            host, port = server.findtext("hostname", ""), server.findtext("port", "")
            if host and port.isdigit():
                out[f"{kind}_host"] = host.replace("%EMAILDOMAIN%", address.rpartition("@")[2])
                out[f"{kind}_port"] = int(port)
                break
    return out if "imap_host" in out else None


# ── an IMAP mailbox ──

class ImapBox:
    def __init__(self, account: dict, password: str | None = None, factory=None):
        self.account = account
        self.password = secrets.get_secret(secret_name(account)) if password is None else password
        self.factory = factory
        self.conn = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def open(self) -> None:
        host, port = self.account.get("imap_host", ""), int(self.account.get("imap_port") or 993)
        if not host:
            raise MailError("не указан сервер IMAP")
        if not self.password:
            raise MailError("не задан пароль ящика")
        try:
            if self.factory is not None:
                self.conn = self.factory(host, port)
            elif port == 143:
                self.conn = imaplib.IMAP4(host, port, timeout=30)
                self.conn.starttls()
            else:
                self.conn = imaplib.IMAP4_SSL(host, port, timeout=30)
            self.conn.login(self.account.get("login") or self.account.get("address", ""), self.password)
        except imaplib.IMAP4.error as exc:
            raise MailError(f"сервер не пустил: {_decoded(exc)}") from exc
        except OSError as exc:
            raise MailError(f"сервер {host}:{port} не отвечает: {exc}") from exc

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.logout()
            except Exception:
                pass
            self.conn = None

    def _select(self, folder: str = "INBOX") -> str:
        typ, data = self.conn.select(folder, readonly=True)  # read-only: letters stay unread
        if typ != "OK":
            raise MailError(f"нет папки {folder}")
        _typ, validity = self.conn.response("UIDVALIDITY")
        return (validity[0] or b"").decode() if validity and validity[0] else ""

    def _uids(self, *criteria: str) -> list[int]:
        typ, data = self.conn.uid("SEARCH", None, *criteria)
        if typ != "OK" or not data or not data[0]:
            return []
        return sorted(int(x) for x in data[0].split())

    def _headers(self, uids: list[int]) -> list[Letter]:
        if not uids:
            return []
        typ, data = self.conn.uid("FETCH", ",".join(map(str, uids)), "(UID BODY.PEEK[HEADER])")
        out = []
        for item in data or []:
            if isinstance(item, tuple) and len(item) >= 2:
                m = re.search(rb"UID (\d+)", item[0])
                if m:
                    out.append(letter_from_headers(m.group(1).decode(), item[1]))
        return sorted(out, key=lambda l: l.time)

    def unseen(self) -> int:
        self._select()
        return len(self._uids("UNSEEN"))

    def new_letters(self, state: dict) -> list[Letter]:
        """Letters after ``state["last"]``; *state* is updated. The first time: the last few days."""
        validity = self._select()
        if state.get("validity") != validity or "last" not in state:
            since = (datetime.now() - timedelta(days=2)).strftime("%d-%b-%Y")
            uids = self._uids("SINCE", since)[-FIRST_LOOK:]
            state["validity"] = validity
            state["last"] = max(self._uids("ALL")[-1:] or [0])
            return self._headers(uids)
        uids = [u for u in self._uids("UID", f"{state['last'] + 1}:*") if u > state["last"]]
        if uids:
            state["last"] = max(uids)
        return self._headers(uids)

    def search(self, query: str, days: int = 30) -> list[Letter]:
        self._select()
        since = (datetime.now() - timedelta(days=max(days, 1))).strftime("%d-%b-%Y")
        letters = self._headers(self._uids("SINCE", since)[-SEARCH_LIMIT:])
        return filter_letters(letters, query)

    def read(self, uid: str) -> str:
        self._select()
        typ, data = self.conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        raw = next((item[1] for item in data or [] if isinstance(item, tuple) and len(item) >= 2), None)
        if raw is None:
            raise MailError(f"нет письма {uid}")
        head = letter_from_headers(str(uid), raw)
        text, files = message_text(raw)
        return letter_text(head, text, files)

    def original(self, uid: str) -> Letter | None:
        self._select()
        found = self._headers([int(uid)]) if str(uid).isdigit() else []
        return found[0] if found else None

    def drafts_folder(self) -> str:
        typ, data = self.conn.list()
        names = []
        for line in data or []:
            text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
            m = re.match(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', text)
            if not m:
                continue
            name = m.group("name").strip()
            if "\\Drafts" in m.group("flags"):
                return name
            names.append(name)
        for name in names:
            if name.strip('"').split("/")[-1].split(".")[-1].lower() in ("drafts", "draft", '&bccenqrabd0epgqybdgeogq4-'):
                return name
        return "Drafts"

    def draft(self, message: EmailMessage) -> str:
        folder = self.drafts_folder()
        typ, data = self.conn.append(folder, "\\Draft", imaplib.Time2Internaldate(time.time()), message.as_bytes())
        if typ != "OK":
            raise MailError(f"черновик не сохранился: {data}")
        return folder.strip('"')


def _decoded(exc) -> str:
    text = str(exc)
    m = re.match(r"^b['\"](.*)['\"]$", text)
    return m.group(1) if m else text


def filter_letters(letters: list[Letter], query: str) -> list[Letter]:
    words = [w for w in re.split(r"\W+", (query or "").lower()) if w]
    if not words:
        return letters[-50:]
    return [l for l in letters if all(w in f"{l.sender} {l.subject}".lower() for w in words)][-50:]


def letter_text(head: Letter, text: str, files: list[str]) -> str:
    out = [f"От: {head.sender}", f"Кому: {head.to}" if head.to else "",
           f"Дата: {datetime.fromtimestamp(head.time):%d.%m.%Y %H:%M}", f"Тема: {head.subject}"]
    if files:
        out.append("Вложения: " + ", ".join(files))
    body = text if len(text) <= READ_LIMIT else text[:READ_LIMIT] + "\n… (обрезано)"
    return "\n".join(x for x in out if x) + "\n\n" + body


def build_draft(account: dict, to: str, subject: str, body: str, original: Letter | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = account.get("address", "")
    if original is not None:
        to = to or parseaddr(original.sender)[1]
        subject = subject or (original.subject if original.subject.lower().startswith(("re:", "на:"))
                              else f"Re: {original.subject}")
        if original.message_id:
            msg["In-Reply-To"] = original.message_id
            msg["References"] = original.message_id
    if not to:
        raise MailError("не указан адрес получателя")
    msg["To"] = ", ".join(a for _n, a in getaddresses([to]) if a) or to
    msg["Subject"] = subject or "(без темы)"
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg.set_content(body or "")
    return msg


# ── the Outlook of this computer ──

class OutlookBox:
    """The same as ``ImapBox`` through the Outlook installed here (COM)."""

    def __init__(self, account: dict, app=None):
        self.account = account
        self.app = app
        self.ns = None
        self._com = False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def open(self) -> None:
        if self.app is None:
            try:
                import pythoncom
                import win32com.client
            except ImportError as exc:
                raise MailError("Outlook доступен только в Windows") from exc
            pythoncom.CoInitialize()
            self._com = True
            try:
                self.app = win32com.client.Dispatch("Outlook.Application")
            except Exception as exc:
                raise MailError(f"Outlook не запускается: {exc}") from exc
        self.ns = self.app.GetNamespace("MAPI")

    def close(self) -> None:
        self.ns = None
        if self._com:
            import pythoncom

            pythoncom.CoUninitialize()
            self._com = False

    def _inbox(self):
        return self.ns.GetDefaultFolder(6)  # olFolderInbox

    @staticmethod
    def _letter(item) -> Letter:
        try:
            when = item.ReceivedTime.timestamp()
        except Exception:
            when = time.time()
        sender = item.SenderName or ""
        address = getattr(item, "SenderEmailAddress", "") or ""
        if address and "@" in address:
            sender = f"{sender} <{address}>" if sender else address
        return Letter(str(item.EntryID), sender or "?", item.Subject or "(без темы)", when,
                      bool(item.Attachments.Count), "", getattr(item, "To", "") or "")

    def _recent(self, days: float, limit: int):
        items = self._inbox().Items
        items.Sort("[ReceivedTime]", True)
        border = datetime.now() - timedelta(days=days)
        out = []
        for item in items:
            if len(out) >= limit:
                break
            if getattr(item, "Class", 43) != 43:  # olMail; meeting requests and reports are skipped
                continue
            try:
                if item.ReceivedTime.replace(tzinfo=None) < border:
                    break
            except Exception:
                pass
            out.append(self._letter(item))
        return sorted(out, key=lambda l: l.time)

    def unseen(self) -> int:
        return int(self._inbox().UnReadItemCount)

    def new_letters(self, state: dict) -> list[Letter]:
        seen = set(state.get("seen") or [])
        first = "seen" not in state
        letters = self._recent(2 if first else 1, FIRST_LOOK if first else 200)
        fresh = [l for l in letters if l.uid not in seen]
        state["seen"] = [l.uid for l in letters][-500:]
        return fresh

    def search(self, query: str, days: int = 30) -> list[Letter]:
        return filter_letters(self._recent(days, SEARCH_LIMIT), query)

    def read(self, uid: str) -> str:
        try:
            item = self.ns.GetItemFromID(uid)
        except Exception as exc:
            raise MailError(f"нет письма {uid}") from exc
        files = [item.Attachments.Item(i + 1).FileName for i in range(item.Attachments.Count)]
        return letter_text(self._letter(item), item.Body or "", files)

    def original(self, uid: str) -> Letter | None:
        try:
            return self._letter(self.ns.GetItemFromID(uid))
        except Exception:
            return None

    def draft(self, message: EmailMessage, reply_uid: str = "") -> str:
        body = message.get_content()
        if reply_uid:
            item = self.ns.GetItemFromID(reply_uid).Reply()
            item.Body = body + "\n\n" + (item.Body or "")
        else:
            item = self.app.CreateItem(0)  # olMailItem
            item.To = message["To"]
            item.Subject = message["Subject"]
            item.Body = body
        item.Save()
        return "Черновики Outlook"


def secret_name(account: dict) -> str:
    return f"mail:{account.get('id') or account.get('address')}"


def open_box(account: dict):
    return OutlookBox(account) if account.get("kind") == "outlook" else ImapBox(account)


def account_title(account: dict) -> str:
    return account.get("address") or ("Outlook" if account.get("kind") == "outlook" else account.get("id", "?"))


def state_path() -> Path:
    from .journal import default_path

    return default_path().with_name("mail_state.json")


# ── the connector ──

class MailConnector(Connector):
    id = "mail"
    title = "Почта"
    description = "Новые письма любого ящика: сводка «что пришло», поиск, чтение и черновики ответов по просьбе."
    reads = ("отправителя, тему и время новых писем; текст — только когда вы просите помощника",)
    records = ("отправитель, тема, время, есть ли вложения",)
    never = ("не отправляет письма — только черновики после вашего «Да»", "не отмечает письма прочитанными",
             "ничего не удаляет")

    def __init__(self, settings: dict | None = None, opener=open_box, state_file: Path | None = None):
        super().__init__(settings)
        self.accounts = [a for a in self.settings.get("accounts") or [] if isinstance(a, dict)]
        self.interval = max(int(self.settings.get("interval") or INTERVAL), 30)
        self.opener = opener
        self.state_file = state_file
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()  # one conversation with a mailbox at a time
        self.errors: dict[str, str] = {}

    def _state(self) -> dict:
        try:
            return json.loads((self.state_file or state_path()).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: dict) -> None:
        path = self.state_file or state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state), encoding="utf-8")
        except OSError:
            pass

    def poll(self) -> list[Event]:
        events = []
        state = self._state()
        for account in self.accounts:
            key = account.get("id") or account.get("address", "")
            box_state = state.setdefault(key, {})
            try:
                with self._lock, self.opener(account) as box:
                    letters = box.new_letters(box_state)
                self.errors.pop(key, None)
            except Exception as exc:
                self.errors[key] = str(exc)
                continue
            for l in letters:
                event = Event(self.id, "mail.new", f"Письмо от {l.sender}: {l.subject}",
                              ref=f"{key}:{l.uid}", detail=account_title(account) + ("; есть вложения"
                                                                                       if l.attachments else ""),
                              time=l.time)
                events.append(event)
                self.emit(event)
        self._save_state(state)
        if self.errors:
            self.fail("; ".join(f"{k}: {v}" for k, v in self.errors.items()))
        elif self._emit is not None:
            self.state, self.ok = "работает", True
        return events

    def start(self, emit) -> None:
        super().start(emit)
        if not self.accounts:
            self.fail("не добавлено ни одного ящика")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="fluxion-assistant-mail")
        self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                self.poll()
            except Exception as exc:
                self.fail(str(exc))
            if self._stop.wait(self.interval):
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None
        super().stop()

    # ── the tools of the assistant ──
    def _account(self, key: str) -> dict:
        for a in self.accounts:
            if key in (a.get("id"), a.get("address")):
                return a
        if len(self.accounts) == 1 or not key:
            return self.accounts[0]
        raise MailError(f"нет ящика «{key}»; есть: {', '.join(account_title(a) for a in self.accounts)}")

    def _split(self, ref: str) -> tuple[dict, str]:
        key, _, uid = str(ref).rpartition(":")
        return self._account(key), uid

    def tool_search(self, args: dict) -> str:
        days = int(args.get("days") or 7)
        lines = []
        for account in self.accounts if not args.get("account") else [self._account(str(args["account"]))]:
            key = account.get("id") or account.get("address", "")
            with self._lock, self.opener(account) as box:
                for l in box.search(str(args.get("query") or ""), days):
                    lines.append(f"[{key}:{l.uid}] {datetime.fromtimestamp(l.time):%d.%m %H:%M} {l.sender} — "
                                 f"{l.subject}" + (" (вложения)" if l.attachments else ""))
        return "\n".join(lines) or "писем не найдено"

    def tool_read(self, args: dict) -> str:
        account, uid = self._split(str(args.get("id") or ""))
        with self._lock, self.opener(account) as box:
            return box.read(uid)

    def tool_draft(self, args: dict) -> str:
        reply = str(args.get("reply_to") or "")
        account, uid = self._split(reply) if reply else (self._account(str(args.get("account") or "")), "")
        with self._lock, self.opener(account) as box:
            original = box.original(uid) if uid else None
            message = build_draft(account, str(args.get("to") or ""), str(args.get("subject") or ""),
                                  str(args.get("body") or ""), original)
            folder = box.draft(message, uid) if isinstance(box, OutlookBox) else box.draft(message)
        return f"Черновик сохранён в «{folder}» ящика {account_title(account)}: кому {message['To']}, " \
               f"тема «{message['Subject']}». Отправит пользователь сам."

    def tools(self, cloud: bool = False) -> list[AppTool]:
        if not self.accounts:
            return []
        boxes = ", ".join(a.get("id") or a.get("address", "") for a in self.accounts)
        return [
            AppTool("mail", "Почта", "search", f"найти письма по отправителю и теме (ящики: {boxes})",
                    self.tool_search, {"type": "object", "properties": {
                        "query": {"type": "string", "description": "слова из имени отправителя или темы; пусто — все"},
                        "days": {"type": "integer", "description": "за сколько дней, по умолчанию 7"},
                        "account": {"type": "string", "description": "ящик, если нужен один"}}}, "auto", cloud),
            AppTool("mail", "Почта", "read", "прочитать письмо по его номеру [ящик:номер] из результата поиска",
                    self.tool_read, {"type": "object", "properties": {
                        "id": {"type": "string", "description": "номер вида work:1234"}}, "required": ["id"]},
                    "auto", cloud),
            AppTool("mail", "Почта", "draft", "сохранить черновик письма или ответа (не отправляет)",
                    self.tool_draft, {"type": "object", "properties": {
                        "reply_to": {"type": "string", "description": "номер письма, на которое ответ"},
                        "to": {"type": "string"}, "subject": {"type": "string"},
                        "body": {"type": "string", "description": "текст письма"},
                        "account": {"type": "string"}}, "required": ["body"]}, "ask", cloud),
        ]
