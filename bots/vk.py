"""ВКонтакте: the bot of a community, Bots Long Poll API — https://dev.vk.com/ru/api/bots/getting-started.

The token is the access key of the community (Управление → Работа с API) with the rights
to messages and documents; in its settings Long Poll API is on with the event
«Входящее сообщение» and «Сообщения сообщества» are allowed.

    groups.getById                          the community (its id)
    groups.getLongPollServer                server, key, ts — then GET server?act=a_check&key&ts&wait
    messages.send(peer_id, message, random_id, attachment)
    docs.getMessagesUploadServer(peer_id) → upload → docs.save(file) → attachment «doc<owner>_<id>»
"""
from __future__ import annotations

import random
from pathlib import Path

from .base import Adapter, Attachment, BotError, Incoming, check

API = "https://api.vk.com/method"
VERSION = "5.199"


class Vk(Adapter):
    platform = "vk"
    MAX_TEXT = 4000  # VK: 4096

    def __init__(self, token: str, proxy: str = "", session=None):
        super().__init__(token, proxy, session)
        self.group_id = ""
        self.server = self.key = self.ts = ""

    def _call(self, method: str, wait: float = 30, **params) -> dict:
        params.update(access_token=self.token, v=VERSION)
        try:
            response = self.session.post(f"{API}/{method}", data=params, timeout=wait)
        except Exception as exc:
            raise BotError(self.clean(f"ВКонтакте недоступен: {exc}")) from exc
        return check(response, "ВКонтакте").get("response")

    def me(self) -> str:
        groups = self._call("groups.getById")
        group = (groups.get("groups") if isinstance(groups, dict) else groups)[0]
        self.group_id = str(group["id"])
        return str(group.get("name") or f"club{self.group_id}")

    def _connect(self) -> None:
        if not self.group_id:
            self.me()
        server = self._call("groups.getLongPollServer", group_id=self.group_id)
        self.server, self.key, self.ts = server["server"], server["key"], str(server["ts"])

    def poll(self, timeout: int = 25) -> list[Incoming]:
        if not self.server:
            self._connect()
        try:
            response = self.session.get(self.server, params={"act": "a_check", "key": self.key, "ts": self.ts,
                                                             "wait": timeout}, timeout=timeout + 10)
            data = response.json()
        except Exception as exc:
            raise BotError(self.clean(f"ВКонтакте недоступен: {exc}")) from exc
        failed = data.get("failed")
        if failed == 1:  # the history is lost: go on from the new ts
            self.ts = str(data.get("ts") or self.ts)
            return []
        if failed in (2, 3):  # the key is old: a new server
            self.server = ""
            return []
        self.ts = str(data.get("ts") or self.ts)
        out = []
        for update in data.get("updates") or []:
            if update.get("type") != "message_new":
                continue
            message = (update.get("object") or {}).get("message") or update.get("object") or {}
            sender = message.get("from_id")
            if not sender or int(sender) < 0:  # a community writes, not a person
                continue
            item = Incoming(self.platform, str(sender), str(message.get("peer_id") or sender), str(sender),
                            message.get("text") or "")
            for attachment in message.get("attachments") or []:
                kind = attachment.get("type")
                if kind == "audio_message":
                    audio = attachment["audio_message"]
                    url = audio.get("link_ogg") or audio.get("link_mp3")
                    if url:
                        item.attachments.append(Attachment("voice", url, "voice.ogg" if audio.get("link_ogg")
                                                           else "voice.mp3"))
                elif kind == "doc" and attachment["doc"].get("url"):
                    doc = attachment["doc"]
                    item.attachments.append(Attachment("file", doc["url"], doc.get("title") or "file"))
            out.append(item)
        return out

    def send_text(self, chat_id: str, text: str) -> None:
        for part in self.parts(text):
            self._call("messages.send", peer_id=chat_id, message=part, random_id=random.randint(1, 2 ** 31 - 1),
                       dont_parse_links=1)

    def send_file(self, chat_id: str, path: Path, caption: str = "") -> None:
        path = Path(path)
        server = self._call("docs.getMessagesUploadServer", type="doc", peer_id=chat_id)
        try:
            with path.open("rb") as handle:
                uploaded = self.session.post(server["upload_url"], files={"file": (path.name, handle)}, timeout=300)
            uploaded = uploaded.json()
        except Exception as exc:
            raise BotError(self.clean(f"ВКонтакте: файл не загружен: {exc}")) from exc
        if "file" not in uploaded:
            raise BotError(f"ВКонтакте: файл не загружен ({uploaded.get('error') or uploaded})")
        saved = self._call("docs.save", file=uploaded["file"], title=path.name)
        doc = saved.get("doc") if isinstance(saved, dict) else saved[0]
        self._call("messages.send", peer_id=chat_id, message=caption[:4000], random_id=random.randint(1, 2 ** 31 - 1),
                   attachment=f"doc{doc['owner_id']}_{doc['id']}")

    def download(self, attachment: Attachment, folder: Path) -> Path:
        try:
            response = self.session.get(attachment.ref, timeout=120)
        except Exception as exc:
            raise BotError(self.clean(f"ВКонтакте: файл не скачан: {exc}")) from exc
        if response.status_code >= 400:
            raise BotError(f"ВКонтакте: файл не скачан ({response.status_code})")
        target = Path(folder) / (attachment.name or "file")
        target.write_bytes(response.content)
        return target
