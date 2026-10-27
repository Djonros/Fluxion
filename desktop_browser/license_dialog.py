"""License activation dialog for the Fluxion desktop app."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from licensing import LicenseError
from licensing.store import (
    KEY_FILE_SUFFIXES,
    activate_file,
    activate_text,
    clear_activation,
    find_key_files,
    load_activation,
)
from .i18n import tr

PRO_PAGE_URL = "https://djonros.github.io/Fluxion/#pro"


class LicenseDialog(QDialog):
    """Offline license activation: status, activate, deactivate.

    A key can be pasted (line breaks and spaces are tolerated), loaded from a
    key file, dropped onto the window, or activated in one click when a valid
    key file is found in Downloads, on the Desktop or next to the program."""

    def __init__(self, parent=None, *, search_dirs=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Лицензия Fluxion"))
        self.setMinimumWidth(460)
        self.setAcceptDrops(True)
        self._search_dirs = search_dirs
        self._found_key: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        self.status_label = QLabel()
        self.status_label.setObjectName("licenseStatus")
        self.email_label = QLabel()
        self.expires_label = QLabel()
        layout.addWidget(self.status_label)
        layout.addWidget(self.email_label)
        layout.addWidget(self.expires_label)

        plan_info = QLabel(
            tr("<b>FREE:</b> чат, агент (чтение файлов), веб-поиск, RAG-индекс, мастер настройки<br>"
            "<b>PRO:</b> запись файлов агентом, QLoRA-обучение, облачные API-бэкенды, "
            "расширенный каталог моделей (7B Q8_0, 14B, 32B)")
        )
        plan_info.setObjectName("licenseMessage")
        plan_info.setWordWrap(True)
        layout.addWidget(plan_info)

        self.found_button = QPushButton()
        self.found_button.setObjectName("sendButton")
        self.found_button.setVisible(False)
        self.found_button.clicked.connect(self._activate_found)
        layout.addWidget(self.found_button)

        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText(tr("Вставьте ключ или перетащите в окно файл .key"))
        self.key_input.returnPressed.connect(self._activate)
        layout.addWidget(self.key_input)

        self.file_button = QPushButton(tr("Загрузить файл ключа…"))
        self.file_button.setObjectName("themeButton")
        self.file_button.clicked.connect(self._pick_key_file)
        layout.addWidget(self.file_button)

        buttons = QHBoxLayout()
        self.activate_button = QPushButton(tr("Активировать"))
        self.activate_button.setObjectName("sendButton")
        self.activate_button.clicked.connect(self._activate)
        self.deactivate_button = QPushButton(tr("Деактивировать"))
        self.deactivate_button.setObjectName("stopButton")
        self.deactivate_button.clicked.connect(self._deactivate)
        self.buy_button = QPushButton(tr("Купить Pro…"))
        self.buy_button.setObjectName("themeButton")
        self.buy_button.setToolTip(tr("Цены и порядок покупки на сайте Fluxion"))
        self.buy_button.clicked.connect(self._open_pro_page)
        close_button = QPushButton(tr("Закрыть"))
        close_button.setObjectName("themeButton")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(self.activate_button)
        buttons.addWidget(self.deactivate_button)
        buttons.addStretch()
        buttons.addWidget(self.buy_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.message_label = QLabel()
        self.message_label.setObjectName("licenseMessage")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        self._refresh()
        self._offer_found_key()

    def _refresh(self) -> None:
        lic = load_activation()
        if lic is None:
            self.status_label.setText(tr("Текущий план: FREE"))
            self.email_label.setText(tr("Почта: —"))
            self.expires_label.setText(tr("Срок: —"))
            self.deactivate_button.setEnabled(False)
        else:
            if lic.is_pro:
                plan = "PRO"
            elif lic.updates_lapsed and not lic.is_expired:
                plan = tr("{value} (обновления закончились)", value=lic.plan.upper())
            else:
                plan = tr("{value} (истекла)", value=lic.plan.upper())
            self.status_label.setText(tr("Текущий план: {plan}", plan=plan))
            self.email_label.setText(tr("Почта: {email}", email=lic.email))
            expires = lic.expires_at.strftime("%Y-%m-%d") if lic.expires_at else tr("бессрочная")
            updates = lic.updates_until
            covered = tr(" · обновления до {updates:%Y-%m-%d}", updates=updates) if updates is not None else ""
            self.expires_label.setText(tr("Срок: {expires}{covered}", expires=expires, covered=covered))
            if lic.updates_lapsed and not lic.is_expired:
                self.message_label.setText(
                    tr("Ключ покрывает версии, выпущенные до {updates:%Y-%m-%d}; эта версия вышла "
                    "позже. В тех версиях Pro работает как прежде. Чтобы включить Pro здесь, "
                    "продлите обновления — кнопка «Купить Pro…».", updates=updates)
                )
            device = tr("привязка к этому ПК") if lic.device else tr("без привязки")
            self.expires_label.setToolTip(tr("Ключ: {device}", device=device))
            self.deactivate_button.setEnabled(True)
        self.buy_button.setVisible(lic is None or not lic.is_pro)
        self.status_label.setProperty("plan", "pro" if lic is not None and lic.is_pro else "free")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _open_pro_page(self) -> None:
        """Open the price section of the site in the system browser."""
        QDesktopServices.openUrl(QUrl(PRO_PAGE_URL))

    def _activate(self) -> None:
        text = self.key_input.text().strip()
        if not text:
            self.message_label.setText(
                tr("Вставьте ключ, загрузите файл ключа или перетащите его в это окно.")
            )
            return
        try:
            lic = activate_text(text)
        except LicenseError as exc:
            self.message_label.setText(tr("Ошибка активации: {exc}", exc=exc))
            return
        self.key_input.clear()
        self._activated(lic)

    def _activate_path(self, path: Path | str) -> bool:
        try:
            lic = activate_file(path)
        except LicenseError as exc:
            self.message_label.setText(tr("Ошибка активации ({value}): {exc}", value=Path(path).name, exc=exc))
            return False
        self._activated(lic)
        return True

    def _activated(self, lic) -> None:
        self.message_label.setText(tr("Активирована лицензия {value} для {email}.", value=lic.plan.upper(), email=lic.email))
        self.found_button.setVisible(False)
        self._found_key = None
        self._refresh()
        parent = self.parent()
        refresh = getattr(parent, "_refresh_license_badge", None)
        if callable(refresh):
            refresh()  # Pro features (training, models) unlock immediately

    def _pick_key_file(self) -> None:
        start = str(Path.home() / "Downloads")
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Файл лицензионного ключа"), start,
            tr("Ключ Fluxion (*.key *.txt *.lic);;Все файлы (*.*)"),
        )
        if path:
            self._activate_path(path)

    def _offer_found_key(self) -> None:
        lic = load_activation()
        if lic is not None and lic.is_pro:
            return
        try:
            found = find_key_files(self._search_dirs)
        except Exception:
            found = []
        if found:
            self._found_key = found[0]
            self.found_button.setText(tr("Активировать ключ из файла {value}", value=found[0].name))
            self.found_button.setToolTip(str(found[0]))
            self.found_button.setVisible(True)

    def _activate_found(self) -> None:
        if self._found_key is not None:
            self._activate_path(self._found_key)

    # drag & drop a key file onto the dialog
    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt API)
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(u.toLocalFile().lower().endswith(KEY_FILE_SUFFIXES) for u in urls):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt API)
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local.lower().endswith(KEY_FILE_SUFFIXES):
                self._activate_path(local)
                event.acceptProposedAction()
                return
        event.ignore()

    def _deactivate(self) -> None:
        if clear_activation():
            self.message_label.setText(tr("Лицензия деактивирована (план FREE)."))
        else:
            self.message_label.setText(tr("Активной лицензии нет."))
        self._refresh()
