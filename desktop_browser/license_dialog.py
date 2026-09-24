"""License activation dialog for the Fluxion desktop app."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from licensing import LicenseError
from licensing.store import activate, clear_activation, load_activation


class LicenseDialog(QDialog):
    """Offline license activation: status, activate, deactivate."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Лицензия Fluxion")
        self.setMinimumWidth(460)

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
            "<b>FREE:</b> чат, агент (чтение файлов), веб-поиск, RAG-индекс, мастер настройки<br>"
            "<b>PRO:</b> запись файлов агентом, QLoRA-обучение, облачные API-бэкенды"
        )
        plan_info.setObjectName("licenseMessage")
        plan_info.setWordWrap(True)
        layout.addWidget(plan_info)

        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("Вставьте лицензионный ключ (email-ключ Pro)")
        layout.addWidget(self.key_input)

        buttons = QHBoxLayout()
        self.activate_button = QPushButton("Активировать")
        self.activate_button.setObjectName("sendButton")
        self.activate_button.clicked.connect(self._activate)
        self.deactivate_button = QPushButton("Деактивировать")
        self.deactivate_button.setObjectName("stopButton")
        self.deactivate_button.clicked.connect(self._deactivate)
        close_button = QPushButton("Закрыть")
        close_button.setObjectName("themeButton")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(self.activate_button)
        buttons.addWidget(self.deactivate_button)
        buttons.addStretch()
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.message_label = QLabel()
        self.message_label.setObjectName("licenseMessage")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        self._refresh()

    def _refresh(self) -> None:
        lic = load_activation()
        if lic is None:
            self.status_label.setText("Текущий план: FREE")
            self.email_label.setText("Почта: —")
            self.expires_label.setText("Срок: —")
            self.deactivate_button.setEnabled(False)
        else:
            plan = "PRO" if lic.is_pro else f"{lic.plan.upper()} (истекла)"
            self.status_label.setText(f"Текущий план: {plan}")
            self.email_label.setText(f"Почта: {lic.email}")
            expires = lic.expires_at.strftime("%Y-%m-%d") if lic.expires_at else "бессрочная"
            self.expires_label.setText(f"Срок: {expires}")
            device = "привязка к этому ПК" if lic.device else "без привязки"
            self.expires_label.setToolTip(f"Ключ: {device}")
            self.deactivate_button.setEnabled(True)
        self.status_label.setProperty("plan", "pro" if lic is not None and lic.is_pro else "free")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _activate(self) -> None:
        key = self.key_input.text().strip()
        if not key:
            self.message_label.setText("Вставьте ключ активации.")
            return
        try:
            lic = activate(key)
        except LicenseError as exc:
            self.message_label.setText(f"Ошибка активации: {exc}")
            return
        self.message_label.setText(
            f"Активирована лицензия {lic.plan.upper()} для {lic.email}."
        )
        self.key_input.clear()
        self._refresh()

    def _deactivate(self) -> None:
        if clear_activation():
            self.message_label.setText("Лицензия деактивирована (план FREE).")
        else:
            self.message_label.setText("Активной лицензии нет.")
        self._refresh()
