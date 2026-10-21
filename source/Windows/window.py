"""Small Windows settings window. Telegram and network work stay in the controller."""
from __future__ import annotations

import json
import re

from PySide6.QtCore import Qt, QSignalBlocker, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)


def paragraph(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def secret_field(placeholder: str) -> QLineEdit:
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    field.setEchoMode(QLineEdit.EchoMode.Password)
    return field


class DialogPicker(QDialog):
    """Pick recent dialogs without exposing chat contents or account credentials."""
    def __init__(self, controller, mode: str, parent=None):
        super().__init__(parent)
        self.controller, self.mode = controller, mode
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle({"people": "Важные люди", "chats": "Группы и каналы",
                             "aiExcludedChats": "Никогда не отправлять в ИИ"}[mode])
        self.resize(540, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(paragraph("Последние 500 диалогов. Выбери нужные и нажми «Добавить»."))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию")
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.list)
        self.status = paragraph("")
        layout.addWidget(self.status)
        row = QHBoxLayout()
        refresh_button = QPushButton("Обновить диалоги")
        refresh_button.clicked.connect(lambda: controller.load_dialogs())
        row.addWidget(refresh_button)
        row.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Добавить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.add_selected)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        layout.addLayout(row)
        controller.changed.connect(self.refresh)
        self.finished.connect(lambda _result: self._disconnect())
        self.refresh()

    def _disconnect(self):
        try:
            self.controller.changed.disconnect(self.refresh)
        except (RuntimeError, TypeError):
            pass

    def refresh(self, *_):
        selected = {item.data(Qt.ItemDataRole.UserRole)["id"] for item in self.list.selectedItems()}
        existing = {str(item["id"]) for item in self.controller.config.get(self.mode, [])}
        search = self.search.text().casefold()
        self.list.clear()
        for dialog in self.controller.dialogs:
            if self.mode == "people" and dialog.get("kind") != "private":
                continue
            if self.mode == "chats" and dialog.get("kind") == "private":
                continue
            if search not in dialog.get("name", "").casefold():
                continue
            identifier = str(dialog["id"])
            suffix = " — добавлен" if identifier in existing else ""
            item = QListWidgetItem(dialog.get("name", "Без имени") + suffix)
            item.setData(Qt.ItemDataRole.UserRole, dict(dialog, id=identifier))
            if identifier in existing:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(item)
            item.setSelected(identifier in selected and identifier not in existing)
        self.status.setText("Сначала подключи Telegram." if self.controller.state != "connected" else
                            "Если нужного диалога нет, открой его в Telegram и обнови список.")

    def add_selected(self):
        values = list(self.controller.config.get(self.mode, []))
        existing = {str(item["id"]) for item in values}
        for item in self.list.selectedItems():
            dialog = item.data(Qt.ItemDataRole.UserRole)
            if dialog["id"] not in existing:
                value = {"id": dialog["id"], "name": dialog.get("name", "Без имени")}
                if self.mode == "chats":
                    value["keywordsOnly"] = True
                values.append(value)
                existing.add(dialog["id"])
        self.controller.update_config({self.mode: values})
        self.accept()


class SettingsWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._config_snapshot = None
        self._activity_snapshot = None
        self._displayed_qr = None
        self._pickers = []
        self.setWindowTitle("Telegram Focus — Windows, эксперимент")
        self.resize(860, 760)
        central = QWidget()
        layout = QVBoxLayout(central)
        heading = QLabel("Telegram Focus")
        heading.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(heading)
        layout.addWidget(paragraph("Работает в трее рядом с часами. Закрытие этого окна не останавливает фильтр."))
        self.error_row = QWidget()
        error_layout = QHBoxLayout(self.error_row)
        error_layout.setContentsMargins(8, 8, 8, 8)
        self.error_label = paragraph("")
        error_layout.addWidget(self.error_label, 1)
        clear_error = QPushButton("Скрыть")
        clear_error.clicked.connect(lambda: controller.clear_error())
        error_layout.addWidget(clear_error)
        self.error_row.setStyleSheet("background-color: #783e18; color: white; border-radius: 4px;")
        layout.addWidget(self.error_row)
        self.tabs = QTabWidget()
        self.pages = {}
        layout.addWidget(self.tabs, 1)
        self._overview()
        self._connection()
        self._rules()
        self._ai()
        self._guide()
        self.setCentralWidget(central)
        controller.changed.connect(self.refresh)
        controller.qr_changed.connect(self.render_qr)
        controller.simulation_received.connect(self.show_simulation)
        self.refresh()

    def _page(self, key: str, title: str):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, title)
        self.pages[key] = scroll
        return layout

    @staticmethod
    def _group(title, layout):
        group = QGroupBox(title)
        inner = QVBoxLayout(group)
        inner.setSpacing(8)
        layout.addWidget(group)
        return inner

    def _toggle(self, label, key, layout):
        checkbox = QCheckBox(label)
        checkbox.toggled.connect(lambda checked: self.controller.update_config({key: checked}))
        layout.addWidget(checkbox)
        return checkbox

    def _overview(self):
        layout = self._page("overview", "Обзор")
        self.filter_status = paragraph("")
        self.filter_status.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(self.filter_status)
        self.master = QCheckBox("Разрешить работу фильтра")
        self.master.toggled.connect(self.controller.set_filter_enabled)
        layout.addWidget(self.master)
        self.connection_status = paragraph("")
        layout.addWidget(self.connection_status)
        auto = self._group("Автоматизация — эксперимент", layout)
        self.auto_focus = self._toggle("По сеансу фокусировки Windows (Focus Session)",
                                       "windowsFocusAutomation", auto)
        self.focus_status = paragraph("")
        auto.addWidget(self.focus_status)
        auto.addWidget(paragraph("Focus Session и отдельно включённое «Не беспокоить» — разные режимы. "
            "Экспериментальная автоматизация отслеживает только сеансы Focus. При неизвестном состоянии "
            "доставка приостановлена. Ручной режим доступен всегда."))
        auto.addWidget(paragraph("Опрос состояния — раз в 15 секунд и дополнительно перед важным баннером. "
            "После смены режима обработка в Telegram-модуле переключается при следующей проверке."))
        auto.addWidget(paragraph("Если обычный Telegram тоже показывает уведомления, возможны дубли. "
            "В ручном режиме выключай TGFocus перед выходом из «Не беспокоить»."))
        history = self._group("Последние решения — без текста переписки", layout)
        self.counters = paragraph("")
        history.addWidget(self.counters)
        self.activities = QListWidget()
        self.activities.setMinimumHeight(180)
        history.addWidget(self.activities)
        layout.addStretch()

    def _connection(self):
        layout = self._page("connection", "Подключение")
        layout.addWidget(paragraph("Нужны твои API ID и API Hash с my.telegram.org/apps. "
            "Это отдельная пользовательская сессия Telegram. Ключи и сессия защищены средствами Windows; "
            "код входа и пароль не сохраняются."))
        form = QFormLayout()
        self.api_id = QLineEdit()
        self.api_id.setPlaceholderText("Числовой API ID")
        self.api_hash = secret_field("API Hash — 32 символа")
        form.addRow("API ID", self.api_id)
        form.addRow("API Hash", self.api_hash)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.connect_button = QPushButton("Подключить / переподключить")
        self.connect_button.clicked.connect(self.connect_telegram)
        row.addWidget(self.connect_button)
        get_keys = QPushButton("Получить API ID / Hash")
        get_keys.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://my.telegram.org/apps")))
        row.addWidget(get_keys)
        row.addStretch()
        layout.addLayout(row)
        self.auth_group = QGroupBox("Вход")
        auth = QVBoxLayout(self.auth_group)
        self.auth_hint = paragraph("")
        auth.addWidget(self.auth_hint)
        qr_row = QHBoxLayout()
        self.qr_button = QPushButton("Войти по QR / обновить QR")
        self.qr_button.clicked.connect(lambda: self.controller.auth("qr"))
        qr_row.addWidget(self.qr_button)
        self.cancel_auth = QPushButton("Другой способ входа")
        self.cancel_auth.clicked.connect(lambda: self.controller.auth("cancelAuth"))
        qr_row.addWidget(self.cancel_auth)
        qr_row.addStretch()
        auth.addLayout(qr_row)
        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        auth.addWidget(self.qr_label)
        self.phone_row = QWidget()
        phone_layout = QHBoxLayout(self.phone_row)
        phone_layout.setContentsMargins(0, 0, 0, 0)
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("Номер телефона с кодом страны: +7…")
        phone_layout.addWidget(self.phone)
        send_phone = QPushButton("Получить код")
        send_phone.clicked.connect(lambda: self.controller.auth("phone", self.phone.text()))
        phone_layout.addWidget(send_phone)
        auth.addWidget(self.phone_row)
        self.code_row = QWidget()
        code_layout = QHBoxLayout(self.code_row)
        code_layout.setContentsMargins(0, 0, 0, 0)
        self.code = secret_field("Код из выбранного Telegram способа доставки")
        self.code.returnPressed.connect(lambda: self.send_auth_secret("code", self.code))
        code_layout.addWidget(self.code)
        send_code = QPushButton("Подтвердить код")
        send_code.clicked.connect(lambda: self.send_auth_secret("code", self.code))
        code_layout.addWidget(send_code)
        resend = QPushButton("Запросить повторно")
        resend.clicked.connect(lambda: self.controller.auth("resend"))
        code_layout.addWidget(resend)
        auth.addWidget(self.code_row)
        self.password_row = QWidget()
        password_layout = QHBoxLayout(self.password_row)
        password_layout.setContentsMargins(0, 0, 0, 0)
        self.password = secret_field("Пароль двухэтапной аутентификации")
        self.password.returnPressed.connect(lambda: self.send_auth_secret("password", self.password))
        password_layout.addWidget(self.password)
        send_password = QPushButton("Войти")
        send_password.clicked.connect(lambda: self.send_auth_secret("password", self.password))
        password_layout.addWidget(send_password)
        auth.addWidget(self.password_row)
        layout.addWidget(self.auth_group)
        self.logged_in = paragraph("")
        layout.addWidget(self.logged_in)
        logout = QPushButton("Выйти и отозвать сессию")
        logout.clicked.connect(self.confirm_logout)
        layout.addWidget(logout)
        if hasattr(self.controller, "test_notification"):
            test = QPushButton("Тестовое уведомление Windows")
            test.clicked.connect(lambda: self.controller.test_notification())
            layout.addWidget(test)
        layout.addStretch()

    def _rules(self):
        layout = self._page("rules", "Правила")
        self.all_private = self._toggle("Пропускать все личные сообщения", "allPrivate", layout)
        self.allow_mentions = self._toggle("Уведомлять об упоминаниях меня в группах", "allowMentions", layout)
        self.allow_replies = self._toggle("Уведомлять об ответах на мои сообщения", "allowReplies", layout)
        self.preview = self._toggle("Показывать текст в уведомлении", "showPreview", layout)
        self.sound = self._toggle("Звук обычных уведомлений", "playSound", layout)
        keywords = self._group("Ключевые слова — резерв при сбое ИИ", layout)
        keywords.addWidget(paragraph("Одно слово или фраза на строку. Регистр и ё/е не важны. "
            "«срочно» не совпадает с «несрочно». Нужные формы слов добавь отдельно."))
        self.keywords = QPlainTextEdit()
        self.keywords.setMaximumHeight(110)
        keywords.addWidget(self.keywords)
        save_words = QPushButton("Сохранить слова")
        save_words.clicked.connect(lambda: self.controller.update_config({"keywords": list(dict.fromkeys(
            word.strip() for word in self.keywords.toPlainText().splitlines() if word.strip()))}))
        keywords.addWidget(save_words)
        self.people = self._rules_list("Важные люди — все их личные проходят локально", "people", layout)
        self.chats = self._rules_list("Группы и каналы — исключения", "chats", layout)
        layout.addWidget(paragraph("Для чата галочка означает проверку важности: по словам или ИИ, если включён. "
            "Без галочки проходят все сообщения чата. Mute Telegram и отдельных тем имеет приоритет."))
        simulation = self._group("Локальная проверка — без ИИ и реального сообщения", layout)
        row = QHBoxLayout()
        self.test_kind = QComboBox()
        for title, kind in [("Личка", "private"), ("Группа", "group"), ("Канал", "channel")]:
            self.test_kind.addItem(title, kind)
        row.addWidget(self.test_kind)
        self.test_chat = QLineEdit()
        self.test_chat.setPlaceholderText("ID чата, необязательно")
        row.addWidget(self.test_chat)
        self.test_sender = QLineEdit()
        self.test_sender.setPlaceholderText("ID собеседника, необязательно")
        row.addWidget(self.test_sender)
        simulation.addLayout(row)
        self.test_text = QPlainTextEdit("Можешь срочно позвонить?")
        self.test_text.setMaximumHeight(80)
        simulation.addWidget(self.test_text)
        self.test_mentioned = QCheckBox("Упоминание меня")
        self.test_replied = QCheckBox("Ответ на моё сообщение")
        row = QHBoxLayout()
        row.addWidget(self.test_mentioned)
        row.addWidget(self.test_replied)
        row.addStretch()
        simulation.addLayout(row)
        test = QPushButton("Проверить локальные правила")
        test.clicked.connect(self.simulate)
        simulation.addWidget(test)
        self.simulation_result = paragraph("")
        simulation.addWidget(self.simulation_result)
        layout.addStretch()

    def _rules_list(self, title, key, parent_layout):
        group = self._group(title, parent_layout)
        widget = QListWidget()
        widget.setMaximumHeight(130)
        if key == "chats":
            widget.itemChanged.connect(self.chat_priority_changed)
        group.addWidget(widget)
        row = QHBoxLayout()
        add = QPushButton("Добавить из Telegram")
        add.clicked.connect(lambda: self.pick_dialog(key))
        row.addWidget(add)
        remove = QPushButton("Убрать выбранное")
        remove.clicked.connect(lambda: self.remove_rule(key, widget))
        row.addWidget(remove)
        row.addStretch()
        group.addLayout(row)
        return widget

    def _ai(self):
        layout = self._page("ai", "ИИ-фильтр")
        layout.addWidget(paragraph("ИИ выключен по умолчанию. При включении первые 4000 символов "
            "подходящего сообщения передаются OpenRouter и выбранному провайдеру модели либо напрямую DeepSeek. "
            "Сам текст может содержать личные данные. История, вложения, имена и ID отдельно не добавляются. "
            "Сначала исключи конфиденциальные диалоги ниже."))
        self.ai_enabled = self._toggle("Отправлять подходящие сообщения в ИИ", "aiEnabled", layout)
        form = QFormLayout()
        self.ai_provider = QComboBox()
        self.ai_provider.addItem("OpenRouter", "openrouter")
        self.ai_provider.addItem("DeepSeek", "deepseek")
        self.ai_provider.currentIndexChanged.connect(self.provider_changed)
        form.addRow("Провайдер", self.ai_provider)
        self.ai_model = QLineEdit()
        self.ai_model.editingFinished.connect(lambda: self.controller.update_config({"aiModel": self.ai_model.text().strip()}))
        form.addRow("ID модели", self.ai_model)
        self.ai_threshold = QComboBox()
        for title, value in [("Обычный и выше", "normal"), ("Высокий и срочный", "high"), ("Только срочный", "urgent")]:
            self.ai_threshold.addItem(title, value)
        self.ai_threshold.currentIndexChanged.connect(lambda: self.controller.update_config(
            {"aiMinimumPriority": self.ai_threshold.currentData()}))
        form.addRow("Порог", self.ai_threshold)
        self.ai_limit = QSpinBox()
        self.ai_limit.setRange(1, 300)
        self.ai_limit.editingFinished.connect(lambda: self.controller.update_config({"aiHourlyLimit": self.ai_limit.value()}))
        form.addRow("Запросов в час", self.ai_limit)
        layout.addLayout(form)
        layout.addWidget(paragraph("openrouter/free выбирает доступную бесплатную модель; тарифы и ограничения "
            "зависят от сервиса. Платные модели расходуют баланс. API DeepSeek может быть платным, даже если веб-чат бесплатный."))
        self.ai_key = secret_field("API-ключ выбранного провайдера")
        layout.addWidget(self.ai_key)
        row = QHBoxLayout()
        save_key = QPushButton("Сохранить ключ")
        save_key.clicked.connect(self.save_ai_key)
        row.addWidget(save_key)
        remove_key = QPushButton("Удалить ключ")
        remove_key.clicked.connect(lambda: self.controller.save_ai_key(""))
        row.addWidget(remove_key)
        get_key = QPushButton("Получить ключ")
        get_key.clicked.connect(self.open_key_page)
        row.addWidget(get_key)
        row.addStretch()
        layout.addLayout(row)
        self.ai_key_status = paragraph("")
        layout.addWidget(self.ai_key_status)
        layout.addWidget(paragraph("Что считать важным именно для тебя (до 2000 символов):"))
        self.ai_instruction = QPlainTextEdit()
        self.ai_instruction.setMaximumHeight(90)
        layout.addWidget(self.ai_instruction)
        context_save = QPushButton("Сохранить контекст")
        context_save.clicked.connect(lambda: self.controller.update_config(
            {"aiInstruction": self.ai_instruction.toPlainText()[:2000]}))
        layout.addWidget(context_save)
        self.ai_test = QPushButton("Проверить ИИ на вымышленном сообщении")
        self.ai_test.clicked.connect(lambda: self.controller.test_ai())
        layout.addWidget(self.ai_test)
        self.ai_status = paragraph("")
        layout.addWidget(self.ai_status)
        self.ai_exclusions = self._rules_list("Никогда не отправлять в ИИ", "aiExcludedChats", layout)
        layout.addWidget(paragraph("Исключённые диалоги используют локальные правила. Заглушённые в Telegram "
            "чаты и темы не отправляются в ИИ. Важные люди и разрешённые обращения проходят локально. "
            "При ошибке или лимите действуют обычные слова; сбой ИИ показывает тихое уведомление. "
            "Уже отправленный запрос нельзя отозвать. ИИ может ошибаться: для экстренной связи добавь важных людей."))
        layout.addStretch()

    def _guide(self):
        layout = self._page("guide", "Как настроить")
        steps = [
            ("1. Запусти приложение", "Полностью распакуй архив в постоянную папку и запусти TGFocus.exe. "
             "Python отдельно не нужен. Не переноси один exe без остальных файлов. Значок находится в трее "
             "возле часов, иногда под стрелкой ^. Закрытие настроек прячет окно. Для завершения выбери «Выход» в трее."),
            ("2. Подключи Telegram", "Получить собственные API ID и API Hash можно на my.telegram.org/apps. "
             "Введи их во вкладке «Подключение». Для QR-входа: Telegram на телефоне → Настройки → Устройства → "
             "Подключить устройство. Подтверди QR и, если потребуется, введи пароль 2FA. Код/пароль не сохраняются. "
             "Никому не отправляй ключи и сессию."),
            ("3. Настрой правила", "Добавь важных людей, ключевые слова и нужные группы/каналы. Упоминания "
             "и ответы включаются отдельно. Mute чата или темы Telegram имеет приоритет над исключениями. "
             "Кнопка проверки правил не вызывает ИИ и не проверяет системные разрешения Windows."),
            ("4. Разреши уведомления Windows", "Открой Параметры Windows → Система → Уведомления. "
             "Разреши уведомления TGFocus. При использовании «Не беспокоить» настрой разрешение/приоритет "
             "для TGFocus и проверь настоящие баннеры. Обычный Telegram должен оставаться заглушённым в это время, "
             "иначе возможны дубли. Отдельные уведомления Telegram не перехватываются и не отменяются."),
            ("5. Выбери управление", "По умолчанию включай и выключай фильтр в трее или «Обзоре» вручную. "
             "Экспериментальный авто-режим относится только к Focus Sessions Windows. Отдельно включённое "
             "«Не беспокоить» он не определяет. При неизвестном состоянии авто-режим приостанавливает доставку. "
             "Проверь включение/выключение сеанса, запуск приложения при активном сеансе и отсутствие дублей."),
            ("6. ИИ — по желанию", "Сначала добавь приватные диалоги в исключения ИИ. Выбери провайдера, "
             "модель и тариф, сохрани ключ, затем включи отправку и проверь на вымышленном сообщении. "
             "Сообщения передаются внешнему сервису на его условиях. При сбое работают локальные правила. "
             "ИИ и автоматизация независимы."),
            ("7. Проверь на своих сообщениях", "Главный фильтр на новой установке выключен. Включи его, "
             "попроси другого человека написать подходящее сообщение и проверь реальный баннер. Проверь "
             "обычное сообщение, заглушённый чат, выключение фильтра и переход по баннеру. Во сне, без сети "
             "или после «Выход» приложение новые сообщения не обрабатывает. Это экспериментальная Windows-версия."),
        ]
        for title, text in steps:
            group = self._group(title, layout)
            group.addWidget(paragraph(text))
        layout.addStretch()

    def show_page(self, page: str):
        self.tabs.setCurrentWidget(self.pages.get(page, self.pages["overview"]))
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.api_hash.clear()
        self.code.clear()
        self.password.clear()
        self.ai_key.clear()

    def connect_telegram(self):
        self.controller.connect_telegram(self.api_id.text().strip(), self.api_hash.text().strip())
        self.api_hash.clear()

    def send_auth_secret(self, command, field):
        value = field.text()
        field.clear()
        if value.strip():
            self.controller.auth(command, value)

    def confirm_logout(self):
        answer = QMessageBox.question(self, "Выйти из Telegram Focus?",
            "Сессия этого приложения будет отозвана. Обычный Telegram продолжит работать.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.logout()

    def save_ai_key(self):
        value = self.ai_key.text().strip()
        self.ai_key.clear()
        if value:
            self.controller.save_ai_key(value)

    def provider_changed(self):
        provider = self.ai_provider.currentData()
        if not provider:
            return
        self.ai_key.clear()
        self.controller.update_config({"aiProvider": provider,
            "aiModel": "openrouter/free" if provider == "openrouter" else "deepseek-chat"})

    def open_key_page(self):
        url = "https://openrouter.ai/keys" if self.ai_provider.currentData() == "openrouter" else "https://platform.deepseek.com/api_keys"
        QDesktopServices.openUrl(QUrl(url))

    def pick_dialog(self, mode):
        picker = DialogPicker(self.controller, mode, self)
        self._pickers.append(picker)
        picker.finished.connect(lambda _result: self._pickers.remove(picker) if picker in self._pickers else None)
        picker.open()
        self.controller.load_dialogs()

    def remove_rule(self, key, widget):
        identifiers = {item.data(Qt.ItemDataRole.UserRole) for item in widget.selectedItems()}
        if identifiers:
            self.controller.update_config({key: [dict(item) for item in self.controller.config.get(key, [])
                                                 if str(item["id"]) not in identifiers]})

    def chat_priority_changed(self, item):
        identifier = item.data(Qt.ItemDataRole.UserRole)
        if identifier is None:
            return
        values = [dict(chat) for chat in self.controller.config.get("chats", [])]
        for chat in values:
            if str(chat["id"]) == identifier:
                chat["keywordsOnly"] = item.checkState() == Qt.CheckState.Checked
        self.controller.update_config({"chats": values})

    def simulate(self):
        self.controller.simulate({"kind": self.test_kind.currentData(), "text": self.test_text.toPlainText(),
            "chatId": self.test_chat.text().strip(), "senderId": self.test_sender.text().strip(),
            "mentioned": self.test_kind.currentData() == "group" and self.test_mentioned.isChecked(),
            "replyToMe": self.test_kind.currentData() == "group" and self.test_replied.isChecked()})

    def show_simulation(self, allowed, reason):
        self.simulation_result.setText(("Пропустим: " if allowed else "Заглушим: ") + str(reason))

    def render_qr(self, url):
        if url == self._displayed_qr:
            return
        self._displayed_qr = url
        self.qr_label.clear()
        if not url:
            return
        if not re.fullmatch(r"tg://login\?token=[A-Za-z0-9_-]{1,1024}", url):
            self.qr_label.setText("Не удалось показать QR. Обнови код.")
            return
        try:
            import qrcode
            code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
            code.add_data(url)
            code.make(fit=True)
            matrix = code.get_matrix()
            scale, size = 5, len(matrix)
            image = QImage(size * scale, size * scale, QImage.Format.Format_RGB32)
            image.fill(QColor("white"))
            painter = QPainter(image)
            for y, line in enumerate(matrix):
                for x, filled in enumerate(line):
                    if filled:
                        painter.fillRect(x * scale, y * scale, scale, scale, QColor("black"))
            painter.end()
            self.qr_label.setPixmap(QPixmap.fromImage(image))
        except Exception:
            self.qr_label.setText("Не удалось показать QR. Используй вход по номеру или обнови QR.")

    def refresh(self, *_):
        controller = self.controller
        self.error_label.setText(controller.error_text)
        self.error_row.setVisible(bool(controller.error_text))
        self.filter_status.setText(controller.filter_title)
        self.connection_status.setText(controller.status_text + (" · " + controller.account_name if controller.account_name else ""))
        self.focus_status.setText(controller.focus_status)
        self.counters.setText(f"Пропущено: {controller.passed} · Заглушено: {controller.suppressed}")
        self.ai_status.setText(controller.ai_status)
        self.ai_key_status.setText("Ключ выбранного провайдера сохранён" if controller.has_ai_key() else "Ключ не задан")
        self.ai_test.setEnabled(bool(controller.config.get("aiEnabled")) and controller.has_ai_key())
        self.auth_hint.setText(controller.status_text)
        self.logged_in.setText("Telegram подключён: " + controller.account_name if controller.state == "connected" else "")
        auth_states = {"phone", "code", "password", "qr", "qrExpired", "authUnsupported"}
        self.auth_group.setVisible(controller.state in auth_states)
        self.phone_row.setVisible(controller.state in {"phone", "authUnsupported"})
        self.code_row.setVisible(controller.state == "code")
        self.password_row.setVisible(controller.state == "password")
        self.qr_label.setVisible(controller.state == "qr")
        self.qr_button.setEnabled(controller.state != "password")
        self.connect_button.setEnabled(controller.state not in {"connecting", "reconnecting"})
        if controller.state != "qr":
            self.render_qr("")
        elif controller.qr_url:
            self.render_qr(controller.qr_url)
        if controller.state == "connected":
            self.password.clear()
            self.code.clear()
        config = controller.config
        snapshot = json.dumps(config, sort_keys=True, ensure_ascii=False)
        if snapshot != self._config_snapshot:
            previous = json.loads(self._config_snapshot) if self._config_snapshot is not None else {}
            self._config_snapshot = snapshot
            for checkbox, key in [(self.master, "enabled"), (self.auto_focus, "windowsFocusAutomation"),
                (self.all_private, "allPrivate"), (self.allow_mentions, "allowMentions"),
                (self.allow_replies, "allowReplies"), (self.preview, "showPreview"),
                (self.sound, "playSound"), (self.ai_enabled, "aiEnabled")]:
                with QSignalBlocker(checkbox):
                    checkbox.setChecked(bool(config.get(key, False)))
            # Keep unsaved edits when unrelated settings or connection state change.
            if previous.get("keywords") != config.get("keywords"):
                self.keywords.setPlainText("\n".join(config.get("keywords", [])))
            if previous.get("aiModel") != config.get("aiModel"):
                self.ai_model.setText(config.get("aiModel", "openrouter/free"))
            if previous.get("aiInstruction") != config.get("aiInstruction"):
                self.ai_instruction.setPlainText(config.get("aiInstruction", ""))
            with QSignalBlocker(self.ai_provider):
                self.ai_provider.setCurrentIndex(max(0, self.ai_provider.findData(config.get("aiProvider", "openrouter"))))
            with QSignalBlocker(self.ai_threshold):
                self.ai_threshold.setCurrentIndex(max(0, self.ai_threshold.findData(config.get("aiMinimumPriority", "high"))))
            with QSignalBlocker(self.ai_limit):
                self.ai_limit.setValue(int(config.get("aiHourlyLimit", 30)))
            for widget, key in [(self.people, "people"), (self.chats, "chats"), (self.ai_exclusions, "aiExcludedChats")]:
                selected = {item.data(Qt.ItemDataRole.UserRole) for item in widget.selectedItems()}
                with QSignalBlocker(widget):
                    widget.clear()
                    for value in config.get(key, []):
                        item = QListWidgetItem(value.get("name", "Без имени") + " · " + str(value["id"]))
                        item.setData(Qt.ItemDataRole.UserRole, str(value["id"]))
                        if key == "chats":
                            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                            item.setCheckState(Qt.CheckState.Checked if value.get("keywordsOnly", True) else Qt.CheckState.Unchecked)
                        widget.addItem(item)
                        item.setSelected(str(value["id"]) in selected)
        history = [(item.get("title", "Telegram"), item.get("reason", ""), bool(item.get("allowed")))
                   for item in list(controller.activities)[:50]]
        if history != self._activity_snapshot:
            self._activity_snapshot = history
            self.activities.clear()
            for title, reason, allowed in history:
                self.activities.addItem(("✓ " if allowed else "— ") + title + " · " + reason)
