"""«Связь с телефоном» (Инструменты): Fluxion Mobile asks this PC over the home network
(package phone). The window turns the server on, shows the address and the pairing code
to type on the phone, and downloads the vision model the «Ремонт» tab of the phone uses.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QSpinBox, QVBoxLayout)

from phone import config, vision
from phone.server import lan_addresses

from .i18n import tr


class PhoneHost(QObject):
    """Carries what a phone asked from the server's threads to the window."""

    asked = Signal(str)


# Downloads that outlive their window: a closed window must not destroy a running thread.
_background: set = set()


class DownloadWorker(QThread):
    progress = Signal(int, int)  # done, total bytes
    done = Signal(str)  # "" when it worked, else the error

    def run(self) -> None:
        try:
            vision.download(on_progress=lambda done, total: self.progress.emit(int(done), int(total)))
            self.done.emit("")
        except Exception as exc:
            self.done.emit(str(exc) or type(exc).__name__)


def vision_state(cfg: dict) -> str:
    if vision.installed(cfg):
        model, _mmproj = vision.files(cfg)
        if vision.handler_class() is None:
            return tr("Модель для фото скачана, но llama-cpp-python старый: нужна версия 0.3.9 или новее.")
        return tr("Модель для фото: {name}", name=model.name)
    return tr("Модели для фото нет. Скачайте её (около {size} ГБ) или запустите Ollama с моделью {model}.",
              size=vision.DOWNLOAD_GB, model=cfg["ollama_model"])


class PhoneDialog(QDialog):
    def __init__(self, parent=None, state: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("Связь с телефоном"))
        self.resize(620, 480)
        self.cfg = config.load()
        self.worker: DownloadWorker | None = None
        box = QVBoxLayout(self)
        intro = QLabel(tr(
            "Fluxion Mobile задаёт вопросы этому компьютеру по домашней сети Wi-Fi: на вкладке «Ремонт» фото "
            "техники разбирает модель для фото на компьютере, в интернет ничего не уходит. Отвечает только "
            "телефону, который знает код."))
        intro.setWordWrap(True)
        box.addWidget(intro)

        self.enabled = QCheckBox(tr("Принимать запросы с телефона, пока Fluxion открыт"))
        self.enabled.setChecked(self.cfg["enabled"])
        box.addWidget(self.enabled)

        form = QFormLayout()
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(self.cfg["port"])
        self.port.valueChanged.connect(self._show_address)
        form.addRow(tr("Порт"), self.port)
        self.address = QLabel()
        self.address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow(tr("Адрес для телефона"), self.address)
        code_row = QHBoxLayout()
        self.code = QLabel(self.cfg["code"])
        self.code.setStyleSheet("font-size: 18px; font-weight: 600; letter-spacing: 2px;")
        self.code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        code_row.addWidget(self.code)
        renew = QPushButton(tr("Новый код"))
        renew.setToolTip(tr("Старый код перестанет работать"))
        renew.clicked.connect(self._new_code)
        code_row.addWidget(renew)
        code_row.addStretch(1)
        form.addRow(tr("Код сопряжения"), code_row)
        box.addLayout(form)
        self.state = QLabel(state)
        self.state.setWordWrap(True)
        box.addWidget(self.state)

        hint = QLabel(tr(
            "На телефоне: «Ремонт» → ⚙ → «Домашний Fluxion», адрес и код отсюда. Телефон и компьютер должны быть "
            "в одной сети. При первом включении Windows спросит про брандмауэр: разрешите доступ в частных сетях."))
        hint.setWordWrap(True)
        box.addWidget(hint)

        box.addWidget(QLabel(tr("<b>Модель для фото</b> (Qwen2.5-VL 7B, лицензия Apache 2.0)")))
        self.vision_label = QLabel(vision_state(self.cfg))
        self.vision_label.setWordWrap(True)
        box.addWidget(self.vision_label)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        box.addWidget(self.progress)
        row = QHBoxLayout()
        self.download_button = QPushButton(tr("Скачать модель для фото"))
        self.download_button.clicked.connect(self._download)
        self.download_button.setEnabled(not vision.installed(self.cfg))
        row.addWidget(self.download_button)
        own = QPushButton(tr("Указать свои файлы…"))
        own.setToolTip(tr("Модель Qwen2.5-VL в GGUF и её файл mmproj"))
        own.clicked.connect(self._pick_files)
        row.addWidget(own)
        row.addStretch(1)
        box.addLayout(row)
        box.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self._show_address()

    def _show_address(self) -> None:
        self.address.setText("\n".join(f"http://{ip}:{self.port.value()}/v1" for ip in lan_addresses()))

    def _new_code(self) -> None:
        self.cfg["code"] = config.new_code()
        self.code.setText(self.cfg["code"])

    def _download(self) -> None:
        if self.worker is not None:
            return
        self.download_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.vision_label.setText(tr("Скачиваю модель для фото…"))
        self.worker = DownloadWorker()
        _background.add(self.worker)
        self.worker.finished.connect(lambda w=self.worker: _background.discard(w))
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_downloaded)
        self.worker.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(done * 1000 / total))

    def _on_downloaded(self, error: str) -> None:
        self.progress.setVisible(False)
        self.worker = None
        if error:
            self.vision_label.setText("✖ " + error)
            self.download_button.setEnabled(True)
        else:
            self.vision_label.setText(vision_state(self.cfg))

    def _pick_files(self) -> None:
        model, _ = QFileDialog.getOpenFileName(self, tr("Модель Qwen2.5-VL (GGUF)"), "", "GGUF (*.gguf)")
        if not model:
            return
        mmproj, _ = QFileDialog.getOpenFileName(self, tr("Файл mmproj этой модели"), "", "GGUF (*.gguf)")
        if not mmproj:
            return
        self.cfg["vision_model"], self.cfg["vision_mmproj"] = model, mmproj
        self.vision_label.setText(vision_state(self.cfg))

    def values(self) -> dict:
        return {**self.cfg, "enabled": self.enabled.isChecked(), "port": self.port.value()}

    def save(self) -> None:
        config.save(self.values())

    def done(self, result: int) -> None:
        if self.worker is not None:  # the download goes on after the window closes
            self.worker.progress.disconnect(self._on_progress)
            self.worker.done.disconnect(self._on_downloaded)
            self.worker = None
        super().done(result)
