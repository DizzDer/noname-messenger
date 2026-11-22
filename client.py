"""Noname Messenger бағдарламасының қазақша PySide6 интерфейсі."""
import argparse
import csv
from datetime import datetime
import secrets
import sys
import time
from PySide6.QtCore import QObject, QRunnable, QRect, QSettings, QSize, QThreadPool, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)
from api import Client, ClientError
from decision import DEFAULT_WEIGHTS, TRAIN, demo_messages, evaluate_model, rank, validate_weights
from network_config import load_profile

STYLE = """
QWidget {background:#ffffff;color:#183238;font:14px 'Segoe UI';}
QWidget#sidebar {background:#edf4f5;}
QWidget#chatRow {background:transparent;}
QWidget#chatHeader,QWidget#composer {background:#f3f8f8;border-radius:8px;}
QLabel {background:transparent;}
QLabel#title {font-size:23px;font-weight:700;}
QLabel#brand {font-size:19px;font-weight:700;}
QLabel#avatar {background:#bcebe6;color:#087f79;border-radius:18px;font-size:17px;font-weight:700;}
QLabel#muted {color:#547079;font-size:12px;}
QLineEdit,QComboBox,QPlainTextEdit {background:white;border:1px solid #dae7e8;border-radius:7px;padding:9px;}
QLineEdit:focus {border-color:#087f79;}
QPushButton {background:#d8efec;color:#086d68;border:0;border-radius:7px;padding:9px 13px;font-weight:600;}
QPushButton:hover {background:#c6e8e4;}
QPushButton#primary {background:#087f79;color:white;}
QPushButton#primary:hover {background:#066a65;}
QListWidget {border:0;outline:0;}
QListWidget#people {background:#edf4f5;}
QListWidget#people::item {padding:12px 8px;border-radius:7px;}
QListWidget#people::item:selected {background:#d0efec;color:#183238;}
QListWidget#history::item:selected {background:#eff7f7;}
QWidget#outgoing {background:#bcece8;border-radius:9px;}
QWidget#incoming {background:#ebf2f3;border-radius:9px;}
QHeaderView::section {background:#edf4f5;padding:7px;border:0;}
QProgressBar {background:#edf4f5;text-align:center;}
QProgressBar::chunk {background:#9bddd7;}
QTabWidget::pane {border:0;}
QTabBar::tab {background:#edf4f5;padding:12px 17px;margin:3px;color:#183238;border-radius:5px;}
QTabBar::tab:selected {background:#087f79;color:#ffffff;}
"""

DARK_COLORS = {
    '#ffffff': '#17232e', '#183238': '#e6f1f3', '#edf4f5': '#1d2e39',
    '#f3f8f8': '#20343f', '#bcebe6': '#244c50', '#547079': '#a4bbc4',
    '#dae7e8': '#3d5662', '#d8efec': '#28494d', '#086d68': '#a1eee5',
    '#c6e8e4': '#356065', '#d0efec': '#31535c', '#eff7f7': '#263944',
    '#bcece8': '#285654', '#ebf2f3': '#223641',
}


def button(title, callback, primary=False):
    widget = QPushButton(title)
    if primary:
        widget.setObjectName('primary')
    widget.clicked.connect(callback)
    return widget


class Result(QObject):
    done = Signal(object, object)


class Request(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.result = Result()

    def run(self):
        try:
            result = self.operation()
        except Exception as error:
            self.result.done.emit(None, error)
        else:
            self.result.done.emit(result, None)


class Window(QMainWindow):
    def __init__(self, address=None, ca=None):
        super().__init__()
        self.setWindowTitle('Noname Messenger')
        self.resize(1080, 700)
        self.setMinimumSize(900, 570)
        self.api = None
        self.user = None
        self.peer = 0
        self.users = []
        self.messages = []
        self.drafts = {}
        self.busy = False
        self.poll_busy = False
        self.epoch = 0
        self.tasks = {}
        self.retry_messages = {}
        self.closed = False
        self.preferences = QSettings('Noname', 'Messenger')
        try:
            self.weights = validate_weights({key: self.preferences.value('weight_' + key, value, type=float)
                                             for key, value in DEFAULT_WEIGHTS.items()})
        except (TypeError, ValueError):
            self.weights = dict(DEFAULT_WEIGHTS)
        self.ai_rows = []
        self.demo_rows = []
        self.demo_now = time.time()
        self.ca_pem = self.preferences.value('ca_pem', None) if address is None and ca is None else None
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.auth_page(address or self.preferences.value('server', 'http://127.0.0.1:8765'), ca)
        self.chat_page()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.apply_preferences()

    def request(self, operation, success, quiet=False):
        lane = 'poll_busy' if quiet else 'busy'
        if getattr(self, lane) or self.closed:
            return False
        setattr(self, lane, True)
        epoch = self.epoch
        task = Request(operation)
        key = id(task)
        self.tasks[key] = task
        if hasattr(self, 'send_button') and not quiet:
            self.send_button.setEnabled(False)
        def finished(result, error):
            self.tasks.pop(key, None)
            if self.closed or epoch != self.epoch:
                return
            setattr(self, lane, False)
            if not quiet:
                self.send_button.setEnabled(True)
            if error is None:
                success(result)
                return
            status = getattr(error, 'status', 0)
            if quiet:
                self.status.setText('Байланыс үзілді. Қайта қосылу әрекеті жүріп жатыр.')
                if status == 401:
                    self.clear_session()
                return
            detail = str(error) if isinstance(error, ClientError) else 'Әрекетті орындау мүмкін болмады.'
            QMessageBox.warning(self, 'Noname Messenger', detail)
        task.result.done.connect(finished, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(task)
        return True

    def auth_page(self, address, ca):
        page = QWidget()
        body = QVBoxLayout(page)
        body.setContentsMargins(170, 55, 170, 55)
        title = QLabel('Noname Messenger')
        title.setObjectName('title')
        body.addWidget(title)
        body.addWidget(QLabel('Жеке хат алмасу және жауап беру кезегін талдау'))
        body.addStretch()
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItems(['Кіру', 'Тіркелу'])
        self.address = QLineEdit(address)
        self.login = QLineEdit()
        self.name = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow('Әрекет', self.mode)
        form.addRow('Сервер мекенжайы', self.address)
        form.addRow('Логин', self.login)
        form.addRow('Аты', self.name)
        form.addRow('Құпиясөз', self.password)
        self.name.setEnabled(False)
        self.mode.currentIndexChanged.connect(lambda i: self.name.setEnabled(i == 1))
        body.addLayout(form)
        self.ca = ca
        self.profile_note = QLabel('Бірнеше құрылғы үшін сервердің connection.json файлын ашыңыз.')
        self.profile_note.setWordWrap(True)
        body.addWidget(self.profile_note)
        body.addWidget(button('Қосылу профилін ашу', self.import_profile))
        body.addWidget(button('Байланысты тексеру', self.check_connection))
        self.auth_status = QLabel('')
        self.auth_status.setWordWrap(True)
        body.addWidget(self.auth_status)
        body.addWidget(button('Жалғастыру', self.authenticate, True))
        self.password.returnPressed.connect(self.authenticate)
        body.addStretch()
        self.stack.addWidget(self.scroll_page(page))

    def chat_page(self):
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(12)
        sidebar = QWidget()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(256)
        left = QVBoxLayout(sidebar)
        left.setContentsMargins(12, 12, 12, 12)
        right = QVBoxLayout()
        outer.addWidget(sidebar)
        outer.addLayout(right, 3)
        brand = QLabel('Noname Messenger')
        brand.setObjectName('brand')
        brand_row = QHBoxLayout()
        brand_row.addWidget(self.avatar('N'))
        brand_row.addWidget(brand, 1)
        left.addLayout(brand_row)
        self.account = QLabel()
        left.addWidget(self.account)
        left.addWidget(QLabel('Чаттар'))
        self.people_filter = QLineEdit()
        self.people_filter.setPlaceholderText('Чат іздеу')
        self.people_filter.textChanged.connect(self.render_people)
        left.addWidget(self.people_filter)
        self.people = QListWidget()
        self.people.setObjectName('people')
        self.people.currentItemChanged.connect(self.choose)
        left.addWidget(self.people, 1)
        left.addWidget(button('Шығу', self.logout))
        self.chat_title = QLabel('Сұхбаттасушыны таңдаңыз')
        self.chat_title.setTextFormat(Qt.TextFormat.PlainText)
        self.chat_title.setObjectName('title')
        header = QWidget()
        header.setObjectName('chatHeader')
        header_row = QHBoxLayout(header)
        self.peer_avatar = self.avatar('…')
        header_row.addWidget(self.peer_avatar)
        header_row.addWidget(self.chat_title, 1)
        header_row.addWidget(button('ЖИ және жауап кезегі', self.analyze))
        right.addWidget(header)
        self.message_filter = QLineEdit()
        self.message_filter.setPlaceholderText('Хат мәтіні бойынша іздеу')
        self.message_filter.textChanged.connect(self.render_messages)
        right.addWidget(self.message_filter)
        self.history = QListWidget()
        self.history.setObjectName('history')
        self.history.setWordWrap(True)
        right.addWidget(self.history, 1)
        controls = QHBoxLayout()
        controls.addWidget(button('Өңдеу', self.edit))
        controls.addWidget(button('Жою', self.delete))
        controls.addWidget(button('Тарихты сақтау', self.export_history))
        controls.addStretch()
        right.addLayout(controls)
        composer_panel = QWidget()
        composer_panel.setObjectName('composer')
        composer = QHBoxLayout(composer_panel)
        self.body = QLineEdit()
        self.body.setPlaceholderText('Хабарлама жазыңыз')
        self.body.setMaxLength(4000)
        self.body.returnPressed.connect(self.send_on_enter)
        composer.addWidget(self.body, 1)
        self.send_button = button('Жіберу', self.send, True)
        composer.addWidget(self.send_button)
        right.addWidget(composer_panel)
        self.status = QLabel()
        self.status.setObjectName('muted')
        right.addWidget(self.status)
        self.tabs = QTabWidget()
        self.tabs.addTab(page, 'Чаттар')
        self.tabs.addTab(self.connection_page(), 'Қосылым')
        self.tabs.addTab(self.preferences_page(), 'Баптаулар')
        self.tabs.addTab(self.analysis_page(), 'ЖИ клиенті')
        self.tabs.addTab(self.decision_page(), 'Талдау')
        self.tabs.addTab(self.help_page(), 'Көмек')
        self.tabs.currentChanged.connect(self.tab_changed)
        self.stack.addWidget(self.tabs)
        menu = self.menuBar().addMenu('Әрекеттер')
        menu.addAction('Жаңарту', self.refresh)
        menu.addAction('Баптаулар', self.show_preferences)
        menu.addAction('Қосылым', lambda: self.tabs.setCurrentIndex(1))
        menu.addAction('Талдау', lambda: self.tabs.setCurrentIndex(4))
        menu.addAction('Көмек', lambda: self.tabs.setCurrentIndex(5))

    @staticmethod
    def scroll_page(page):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    def connection_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 25, 30, 25)
        self.server_label = QLabel()
        self.server_label.setTextFormat(Qt.TextFormat.PlainText)
        self.server_label.setWordWrap(True)
        layout.addWidget(self.server_label)
        self.connection_result = QLabel('')
        self.connection_result.setWordWrap(True)
        layout.addWidget(self.connection_result)
        layout.addWidget(button('Байланысты тексеру', self.check_connection))
        layout.addWidget(button('Мекенжайды көшіру', self.copy_address))
        help_text = QLabel(
            '1. Сервер болатын компьютерде start_server.bat ашып, оның жергілікті IPv4 мекенжайын енгізіңіз.\n\n'
            '2. Сервер жасаған connection.json файлын сенімді жолмен басқа компьютерге жіберіңіз.\n\n'
            '3. Екінші компьютерде start_client.bat ашыңыз. «Қосылу профилін ашу» арқылы файлды таңдаңыз.\n\n'
            'Құрылғылар бір желіде болуы керек. Сервер қосулы тұруы қажет. '
            'Сыртқы желіден қосылу үшін қолжетімді HTTPS сервер қажет.')
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        layout.addStretch()
        layout.addWidget(button('Серверді ауыстыру үшін шығу', self.logout))
        return page

    def copy_address(self):
        if self.api:
            QApplication.clipboard().setText(self.api.url)
            self.connection_result.setText('Сервер мекенжайы көшірілді.')

    def import_profile(self):
        if self.busy:
            self.auth_status.setText('Ағымдағы сұрау аяқталғанша күтіңіз.')
            return
        filename, _ = QFileDialog.getOpenFileName(self, 'Қосылу профилі', '', 'JSON (*.json)')
        if filename:
            try:
                profile = load_profile(filename)
                self.address.setText(profile['url'])
                self.ca_pem = profile['ca_pem']
                self.ca = None
                self.profile_note.setText('Сервер профилі жүктелді. Сертификат тексеріледі.')
                self.auth_status.setText('Жалғастыру үшін кіріңіз немесе тіркеліңіз.')
            except (ClientError, ValueError, OSError) as error:
                self.auth_status.setText(str(error))

    def make_client(self):
        return Client(self.address.text().strip(), self.ca, ca_pem=self.ca_pem)

    def check_connection(self):
        target = self.connection_result if self.user else self.auth_status
        try:
            api = self.api or self.make_client()
        except (ClientError, ValueError, OSError) as error:
            target.setText(str(error))
            return
        def done(result):
            target.setText('Байланыс бар · ' + result['service'])
        if not self.request(lambda: api.request('/health'), done):
            target.setText('Ағымдағы сұрау аяқталғанша күтіңіз.')

    def tab_changed(self, index):
        if index == 3:
            self.refresh_analysis()
        elif index == 4:
            self.refresh_demo_analysis()
        if index in (0, 3):
            self.refresh()

    def apply_preferences(self):
        style = STYLE.replace("font:14px", f"font:{self.preferences.value('font', 14, type=int)}px")
        if self.preferences.value('dark', False, type=bool):
            for light, dark in DARK_COLORS.items():
                style = style.replace(light, dark)
            style = style.replace('background:white;', 'background:#17232e;')
            style += 'QTabBar::tab:selected {color:white;}'
        self.setStyleSheet(style)
        self.timer.start(1000 * self.preferences.value('refresh', 2, type=int))
        if self.user:
            self.render_messages()

    def show_preferences(self):
        if self.user:
            self.tabs.setCurrentIndex(2)

    def preferences_page(self):
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(30, 25, 30, 25)
        theme = QComboBox()
        theme.addItems(['Ашық', 'Қараңғы'])
        theme.setCurrentIndex(int(self.preferences.value('dark', False, type=bool)))
        font = QSpinBox()
        font.setRange(12, 18)
        font.setValue(self.preferences.value('font', 14, type=int))
        refresh = QSpinBox()
        refresh.setRange(1, 30)
        refresh.setSuffix(' сек')
        refresh.setValue(self.preferences.value('refresh', 2, type=int))
        stamps = QCheckBox()
        stamps.setChecked(self.preferences.value('timestamps', True, type=bool))
        enter = QCheckBox()
        enter.setChecked(self.preferences.value('enter', True, type=bool))
        form.addRow('Түс тақырыбы', theme)
        form.addRow('Мәтін өлшемі', font)
        form.addRow('Чатты жаңарту аралығы', refresh)
        form.addRow('Хабар уақытын көрсету', stamps)
        form.addRow('Enter пернесімен жіберу', enter)
        result = QLabel('')
        def save():
            for key, value in {
                'dark': theme.currentIndex() == 1, 'font': font.value(), 'refresh': refresh.value(),
                'timestamps': stamps.isChecked(), 'enter': enter.isChecked(),
            }.items():
                self.preferences.setValue(key, value)
            self.preferences.sync()
            self.apply_preferences()
            result.setText('Баптаулар сақталды.')
        form.addRow(button('Сақтау', save, True))
        form.addRow(result)
        return page

    def send_on_enter(self):
        if self.preferences.value('enter', True, type=bool):
            self.send()

    @staticmethod
    def avatar(initial):
        label = QLabel(initial)
        label.setObjectName('avatar')
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(36, 36)
        return label

    def authenticate(self):
        if self.busy:
            return
        registering = self.mode.currentIndex() == 1
        data = {'username': self.login.text(), 'password': self.password.text()}
        if registering:
            data['name'] = self.name.text()
        try:
            api = self.make_client()
        except (ClientError, ValueError, OSError) as error:
            QMessageBox.warning(self, 'Мекенжай', str(error) if isinstance(error, ClientError)
                                else 'Сервер мекенжайын немесе сертификатты тексеріңіз.')
            return
        def done(result):
            self.api, self.user = api, result['user']
            self.epoch += 1
            self.poll_busy = False
            api.token = result['token']
            self.password.clear()
            self.account.setText(self.user['name'] + ' · @' + self.user['username'])
            self.preferences.setValue('server', api.url)
            if self.ca_pem:
                self.preferences.setValue('ca_pem', self.ca_pem)
            else:
                self.preferences.remove('ca_pem')
            self.server_label.setText('Сервер\n' + api.url + '\n\nТіркелгі\n@' + self.user['username'])
            self.tabs.setCurrentIndex(0)
            self.stack.setCurrentIndex(1)
            self.refresh()
        self.request(lambda: api.request('/register' if registering else '/login', data), done)

    def refresh(self):
        if self.api is None or self.poll_busy or self.closed:
            return
        api = self.api
        peer = self.peer if self.tabs.currentIndex() in (0, 3) else 0
        def fetch():
            result = api.request(f'/sync?peer={peer}')
            messages = result['messages']
            more = result['has_more']
            while more:
                page = api.request(f'/sync?peer={peer}&after={messages[-1]["id"]}')
                messages.extend(page['messages'])
                more = page['has_more']
            return result
        def done(result):
            self.status.setText('Байланыс бар · HTTPS' if api.url.startswith('https:') else 'Байланыс бар · жергілікті режим')
            if self.users != result['users']:
                self.users = result['users']
                self.render_people()
            if peer == self.peer and result['messages'] != self.messages:
                self.messages = result['messages']
                self.render_messages()
                if self.tabs.currentIndex() == 3:
                    self.refresh_analysis()
            elif peer != self.peer and self.tabs.currentIndex() in (0, 3):
                self.refresh()
        self.request(fetch, done, quiet=True)

    def render_people(self):
        query = self.people_filter.text().strip().casefold().lstrip('@')
        self.people.blockSignals(True)
        self.people.clear()
        for user in self.users:
            if query not in (user['name'] + ' ' + user['username']).casefold():
                continue
            unread = f"  [{user['unread']}]" if user['unread'] and user['id'] != self.peer else ''
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, user['id'])
            item.setData(Qt.ItemDataRole.UserRole + 1, user['name'])
            item.setSizeHint(QSize(0, 58))
            row = QWidget()
            row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row.setObjectName('chatRow')
            layout = QHBoxLayout(row)
            layout.setContentsMargins(7, 4, 7, 4)
            layout.addWidget(self.avatar(user['name'][:1].upper()))
            details = QVBoxLayout()
            details.setSpacing(0)
            name = QLabel(user['name'])
            name.setTextFormat(Qt.TextFormat.PlainText)
            name.setStyleSheet('font-weight:700;')
            details.addWidget(name)
            handle = QLabel('@' + user['username'] + unread)
            handle.setObjectName('muted')
            details.addWidget(handle)
            layout.addLayout(details, 1)
            self.people.addItem(item)
            self.people.setItemWidget(item, row)
            if self.peer == user['id']:
                self.people.setCurrentItem(item)
        self.people.blockSignals(False)

    def choose(self, item, _previous):
        if item is None:
            return
        peer = item.data(Qt.ItemDataRole.UserRole)
        if peer != self.peer:
            if self.peer:
                self.drafts[self.peer] = self.body.text()
            self.peer = peer
            self.body.setText(self.drafts.get(peer, ''))
            name = item.data(Qt.ItemDataRole.UserRole + 1)
            self.chat_title.setText(name)
            self.peer_avatar.setText(name[:1].upper())
            self.messages = []
            self.history.clear()
            self.refresh_analysis()
            self.refresh()

    def render_messages(self):
        query = self.message_filter.text().casefold()
        selected = self.history.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self.history.clear()
        for message in self.messages:
            if query not in message['body'].casefold():
                continue
            mine = message['sender'] == self.user['id']
            stamp = datetime.fromtimestamp(message['sent']).strftime('%d.%m %H:%M')
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, message['id'])
            row = QWidget()
            row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row.setObjectName('chatRow')
            alignment = QHBoxLayout(row)
            alignment.setContentsMargins(8, 4, 8, 4)
            bubble = QWidget()
            bubble.setObjectName('outgoing' if mine else 'incoming')
            bubble_layout = QVBoxLayout(bubble)
            bubble_layout.setContentsMargins(13, 8, 13, 7)
            bubble_layout.setSpacing(3)
            body = QLabel(message['body'])
            body.setTextFormat(Qt.TextFormat.PlainText)
            body.setWordWrap(True)
            metrics = body.fontMetrics()
            available = max(112, self.history.viewport().width() - 80)
            width = min(350, available, max(112, metrics.horizontalAdvance(message['body']) + 18))
            height = metrics.boundingRect(QRect(0, 0, width, 100000),
                                          Qt.TextFlag.TextWordWrap, message['body']).height()
            body.setFixedWidth(width)
            body.setMinimumHeight(height)
            timestamp = QLabel(stamp)
            timestamp.setObjectName('muted')
            timestamp.setAlignment(Qt.AlignmentFlag.AlignRight)
            bubble_layout.addWidget(body)
            if self.preferences.value('timestamps', True, type=bool):
                bubble_layout.addWidget(timestamp)
            if mine:
                alignment.addStretch()
            alignment.addWidget(bubble)
            if not mine:
                alignment.addStretch()
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            self.history.addItem(item)
            self.history.setItemWidget(item, row)
            if selected_id == message['id']:
                self.history.setCurrentItem(item)
        if selected_id is None:
            self.history.scrollToBottom()

    def send(self):
        if self.busy or self.closed or not self.api:
            return
        body = self.body.text().strip()
        if not self.peer or not body:
            self.status.setText('Сұхбаттасушыны таңдап, хабарлама жазыңыз.')
            return
        api = self.api
        peer = self.peer
        previous = self.retry_messages.get(peer)
        if previous and previous['body'] == body:
            data = previous
        else:
            data = {'recipient': peer, 'body': body, 'nonce': secrets.token_urlsafe(24)}
            self.retry_messages[peer] = data
        def done(_result):
            self.retry_messages.pop(peer, None)
            if self.peer == peer and self.body.text().strip() == body:
                self.body.clear()
            elif self.drafts.get(peer, '').strip() == body:
                self.drafts.pop(peer, None)
            self.refresh()
        if not self.request(lambda: api.request('/messages', data), done):
            self.status.setText('Хабар жіберіліп жатыр. Күтіңіз.')

    def selected(self):
        item = self.history.currentItem()
        mid = item.data(Qt.ItemDataRole.UserRole) if item else None
        message = next((m for m in self.messages if m['id'] == mid and m['sender'] == self.user['id']), None)
        if message is None:
            self.status.setText('Өзіңіз жіберген хабарламаны таңдаңыз.')
        return message

    def edit(self):
        message = self.selected() if self.user else None
        if message:
            dialog = QInputDialog(self)
            dialog.setWindowTitle('Хабарламаны өңдеу')
            dialog.setLabelText('Мәтін')
            dialog.setOption(QInputDialog.InputDialogOption.UsePlainTextEditForTextInput)
            dialog.setTextValue(message['body'])
            dialog.setOkButtonText('Сақтау')
            dialog.setCancelButtonText('Бас тарту')
            if dialog.exec():
                self.mutate('/messages/edit', {'id': message['id'], 'body': dialog.textValue()})

    def delete(self):
        message = self.selected() if self.user else None
        if not message:
            return
        confirm = QMessageBox(self)
        confirm.setWindowTitle('Хабарламаны жою')
        confirm.setText('Осы хабарлама екі тараптың тарихынан жойылады.')
        yes = confirm.addButton('Жою', QMessageBox.ButtonRole.DestructiveRole)
        no = confirm.addButton('Бас тарту', QMessageBox.ButtonRole.RejectRole)
        confirm.setDefaultButton(no)
        confirm.exec()
        if confirm.clickedButton() == yes:
            self.mutate('/messages/delete', {'id': message['id']})

    def mutate(self, path, data):
        api = self.api
        if not self.request(lambda: api.request(path, data), lambda _: self.refresh()):
            self.status.setText('Жаңарту аяқталған соң әрекетті қайталаңыз.')

    def analyze(self):
        if self.user:
            self.tabs.setCurrentIndex(3)
            self.refresh_analysis()

    def analysis_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        self.ai_formula = QLabel()
        self.ai_formula.setWordWrap(True)
        layout.addWidget(self.ai_formula)
        note = QLabel('Күту — сағат / 24, ең көбі 1. Жаңалық — соңғы сағатта келсе 1, әйтпесе 0. '
                      'Модель мәтінді осы құрылғыда талдайды. Салмақтарды «Талдау» қойындысында өзгертіңіз. '
                      'Бұл тізім ашық диалогтағы кіріс хабарларды ғана салыстырады.')
        note.setWordWrap(True)
        layout.addWidget(note)
        label = QLabel('Талдау үшін «Чаттар» бөлімінде сұхбаттасушыны таңдаңыз.')
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.ai_table = self.ranking_table()
        self.ai_summary = label
        layout.addWidget(self.ai_table, 1)
        self.ai_detail = self.detail_field()
        self.ai_table.itemSelectionChanged.connect(
            lambda: self.show_rank_detail(self.ai_table, self.ai_rows, self.ai_detail))
        layout.addWidget(self.ai_detail)
        controls = QHBoxLayout()
        controls.addWidget(button('Рейтингті жаңарту', self.refresh_analysis))
        controls.addWidget(button('CSV есебін сақтау', self.export_ranking, True))
        controls.addStretch()
        layout.addLayout(controls)
        self.ai_export_status = QLabel('')
        self.ai_export_status.setWordWrap(True)
        layout.addWidget(self.ai_export_status)
        self.update_formula()
        return self.scroll_page(page)

    @staticmethod
    def ranking_table():
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(['№', 'Хабарлама', 'Жеделдік', 'Күту', 'Жаңалық', 'Балл', 'Тірек сөздер'])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setWordWrap(False)
        table.setMinimumHeight(190)
        header = table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for index, width in {0: 40, 2: 82, 3: 65, 4: 80, 5: 65, 6: 145}.items():
            table.setColumnWidth(index, width)
        return table

    @staticmethod
    def detail_field():
        field = QPlainTextEdit()
        field.setReadOnly(True)
        field.setPlaceholderText('Хабарламаны таңдасаңыз, толық мәтіні мен есептеуі көрсетіледі.')
        field.setMinimumHeight(105)
        field.setMaximumHeight(145)
        return field

    def update_formula(self):
        values = self.weights
        text = (f"Балл = 100 × ({values['urgency']:.3f} × жеделдік + "
                f"{values['wait']:.3f} × күту + {values['recent']:.3f} × жаңалық)")
        self.ai_formula.setText(text)
        if hasattr(self, 'decision_formula'):
            self.decision_formula.setText(text + '\nЕң жоғары балл алған нұсқа бірінші ұсынылады.')

    @staticmethod
    def populate_ranking(table, rows):
        table.blockSignals(True)
        table.setRowCount(len(rows))
        for i, (score, p, wait, recent, signals, msg) in enumerate(rows):
            values = [str(msg['id']), msg['body'], f'{p:.3f}', f'{wait:.3f}',
                      f'{recent:.0f}', f'{score:.2f}', ', '.join(signals) or 'Таныс сөз жоқ']
            for j, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if j not in (1, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(i, j, item)
        table.blockSignals(False)
        if rows:
            table.setCurrentCell(0, 0)

    def show_rank_detail(self, table, rows, detail):
        index = table.currentRow()
        if index < 0 or index >= len(rows):
            detail.clear()
            return
        score, p, wait, recent, signals, message = rows[index]
        contributions = [100 * self.weights[key] * value
                         for key, value in zip(('urgency', 'wait', 'recent'), (p, wait, recent))]
        detail.setPlainText(
            f"Хабарлама №{message['id']}: {message['body']}\n\n"
            f"Жеделдік үлесі {contributions[0]:.2f} + күту үлесі {contributions[1]:.2f} + "
            f"жаңалық үлесі {contributions[2]:.2f} = {score:.2f} балл.\n"
            'ЖИ бағасына әсер еткен сөздер: ' + (', '.join(signals) or 'Таныс сөз жоқ') +
            '. Сөздер жеделдікті арттыруы да, төмендетуі де мүмкін. Соңғы шешімді пайдаланушы қабылдайды.')

    def decision_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        title = QLabel('Хабарламаларға жауап беру кезегін таңдау')
        title.setObjectName('title')
        layout.addWidget(title)
        explanation = QLabel(
            'Әдіс — көпкритерийлі салмақталған қосынды. Әр хабарлама — жеке нұсқа. '
            'Үш критерий 0–1 аралығында бағаланады: ЖИ жеделдігі, 24 сағатқа дейінгі күту '
            'және соңғы сағаттағы жаңалық. Салмақтар қосындыға бөлініп, 1-ге келтіріледі.')
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        self.weight_controls = {}
        for key, label in [('urgency', 'Жеделдік салмағы'), ('wait', 'Күту салмағы'), ('recent', 'Жаңалық салмағы')]:
            control = QSpinBox()
            control.setRange(0, 100)
            control.setValue(round(self.weights[key] * 100))
            self.weight_controls[key] = control
            form.addRow(label, control)
        layout.addLayout(form)
        controls = QHBoxLayout()
        controls.addWidget(button('Салмақтарды сақтау', self.save_weights, True))
        controls.addWidget(button('Әдепкі салмақтар', self.reset_weights))
        controls.addWidget(button('Демо есептеу', self.refresh_demo_analysis))
        controls.addStretch()
        layout.addLayout(controls)
        self.weight_status = QLabel('Сақталған салмақтар нақты чат пен демо есебіне қолданылады.')
        self.weight_status.setWordWrap(True)
        layout.addWidget(self.weight_status)
        self.decision_formula = QLabel()
        self.decision_formula.setWordWrap(True)
        layout.addWidget(self.decision_formula)
        demo_note = QLabel('Демо деректер: үш жасанды хабарлама. Бұл кесте тіркелгі хаттары емес.')
        demo_note.setWordWrap(True)
        layout.addWidget(demo_note)
        self.demo_summary = QLabel()
        self.demo_summary.setWordWrap(True)
        layout.addWidget(self.demo_summary)
        self.demo_table = self.ranking_table()
        layout.addWidget(self.demo_table)
        self.demo_detail = self.detail_field()
        self.demo_table.itemSelectionChanged.connect(
            lambda: self.show_rank_detail(self.demo_table, self.demo_rows, self.demo_detail))
        layout.addWidget(self.demo_detail)
        evaluation = evaluate_model()
        training_size = sum(len(samples) for samples in TRAIN.values())
        model_note = QLabel(
            f"ЖИ — мультиномиалды аңғал Байес моделі. Оқыту: {training_size} қазақша мысал. "
            f"Бөлек бақылау жиыны: {evaluation['correct']}/{evaluation['total']} дұрыс "
            f"({100 * evaluation['accuracy']:.1f}%). Бұл шағын оқу жиынындағы нәтиже; "
            'шынайы хаттардағы дәлдікке кепіл болмайды. Терістеу мен күрделі контекст қате бағалануы мүмкін.')
        model_note.setWordWrap(True)
        layout.addWidget(model_note)
        self.refresh_demo_analysis()
        return self.scroll_page(page)

    def save_weights(self):
        try:
            self.weights = validate_weights({key: control.value() for key, control in self.weight_controls.items()})
        except ValueError:
            self.weight_status.setText('Кемінде бір салмақ нөлден үлкен болуы керек.')
            return
        for key, value in self.weights.items():
            self.preferences.setValue('weight_' + key, value)
        self.preferences.sync()
        self.weight_status.setText('Салмақтар сақталды және қосындысы 1-ге келтірілді.')
        self.refresh_demo_analysis()
        self.refresh_analysis()

    def reset_weights(self):
        for key, value in DEFAULT_WEIGHTS.items():
            self.weight_controls[key].setValue(round(value * 100))
        self.save_weights()

    def refresh_demo_analysis(self):
        self.demo_rows = rank(demo_messages(self.demo_now), 0, self.demo_now, self.weights)
        self.update_formula()
        self.populate_ranking(self.demo_table, self.demo_rows)
        best = self.demo_rows[0]
        self.demo_summary.setText(f"Ұсынылған нұсқа: хабарлама №{best[5]['id']} · {best[0]:.2f} балл")
        self.show_rank_detail(self.demo_table, self.demo_rows, self.demo_detail)

    def help_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        title = QLabel('Жоба және пайдалану тәртібі')
        title.setObjectName('title')
        layout.addWidget(title)
        for text in [
            'Мақсаты: бір жергілікті желідегі пайдаланушылардың жеке хат алмасуын ұйымдастыру '
            'және маңызды кіріс хабарларға жауап беру кезегін ұсыну. Негізгі пайдаланушылар — студенттер мен шағын топтар.',
            'Әзірлеушілер: Қаһарман Нұрмухамед және Қанатұлы Қуат. Технологиялар: Python, PySide6, SQLite және HTTPS.',
            '1. Серверді іске қосып, қосылу профилін клиенттерге беріңіз. Әр пайдаланушы тіркелгі ашады. '
            '2. «Чаттар» бөлімінде адамды таңдап, хабар жіберіңіз. Чатты және хабар мәтінін іздеуге болады. '
            'Өзіңіз жіберген хабарды таңдасаңыз, оны өңдеуге немесе жоюға болады.',
            '3. «ЖИ клиенті» бөлімінде кіріс хабарлардың салыстырмалы рейтингін қараңыз. Жолды таңдағанда '
            'толық мәтін мен балл үлестері ашылады. CSV есебін және чат тарихын өзіңіз таңдаған файлға сақтай аласыз.',
            '4. «Талдау» бөлімінде үш салмақты өзгертіп, шешімнің қалай өзгеретінін көрсетіңіз. '
            '«Баптаулар» бөлімінде тақырыпты, мәтін өлшемін, уақыт белгісін және жаңарту аралығын сақтаңыз.',
            'Деректер базасы серверде сақталады. Пайдаланушылар, сеанстар және хабарламалар бөлек кестелерде. '
            'Хабар мәтіні параметрленген SQL сұрауларымен сақталады. Жойылған хабар чатта көрсетілмейді.',
            'Құпиялылық: HTTPS тасымалданатын деректі қорғайды, клиент сервер сертификатын тексереді. '
            'Сервер әкімшісі сақталған хат мәтінін оқи алады. Бұл нұсқада ұшынан ұшына дейін шифрлау іске асырылмаған.',
            'Қорғауда көрсету: тіркелу → екі клиенттен хат алмасу → іздеу → өңдеу және жою → қайта кіріп тарихты тексеру '
            '→ ЖИ рейтингін түсіндіру → салмақтарды өзгерту → CSV есебін сақтау. '
            'Жоба бумасындағы Word есебі мен презентацияда құрылым, деректер базасы және тексеру нәтижелері берілген.',
        ]:
            label = QLabel(text)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
        layout.addStretch()
        return self.scroll_page(page)

    def refresh_analysis(self):
        if not self.user:
            return
        self.ai_rows = rank(self.messages, self.user['id'], time.time(), self.weights)
        self.update_formula()
        self.ai_summary.setText(
            f"Алдымен қарау ұсынылады: №{self.ai_rows[0][5]['id']} · {self.ai_rows[0][0]:.2f} балл · "
            + self.ai_rows[0][5]['body'][:100]
            if self.ai_rows else 'Талдауға кіріс хабарлама жоқ. Чатты таңдаңыз.')
        self.populate_ranking(self.ai_table, self.ai_rows)
        self.show_rank_detail(self.ai_table, self.ai_rows, self.ai_detail)

    @staticmethod
    def csv_text(value):
        # Spreadsheet programs must treat message text as text, not a formula.
        value = str(value)
        return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else value

    def export_ranking(self):
        if not self.ai_rows:
            self.ai_export_status.setText('Экспорттау үшін кіріс хабарлары бар чатты таңдаңыз.')
            return
        filename, _ = QFileDialog.getSaveFileName(self, 'ЖИ есебін сақтау', 'ЖИ_рейтингі.csv', 'CSV (*.csv)')
        if not filename:
            return
        try:
            with open(filename, 'w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['Орын', 'Хабарлама нөмірі', 'Мәтін', 'Жеделдік', 'Күту', 'Жаңалық',
                                 'Жеделдік салмағы', 'Күту салмағы', 'Жаңалық салмағы', 'Балл', 'Тірек сөздер'])
                for position, (score, p, wait, recent, signals, msg) in enumerate(self.ai_rows, 1):
                    writer.writerow([position, msg['id'], self.csv_text(msg['body']), f'{p:.6f}',
                                     f'{wait:.6f}', recent, self.weights['urgency'], self.weights['wait'],
                                     self.weights['recent'], f'{score:.6f}', self.csv_text(', '.join(signals))])
        except (OSError, UnicodeError):
            self.ai_export_status.setText('Файл сақталмады. Қолжетімді буманы таңдаңыз.')
            return
        self.ai_export_status.setText('ЖИ рейтингі CSV файлына сақталды.')

    def export_history(self):
        if not self.user or not self.peer or not self.messages:
            self.status.setText('Сақтау үшін хабарламалары бар чатты таңдаңыз.')
            return
        filename, _ = QFileDialog.getSaveFileName(self, 'Чат тарихын сақтау', 'Чат_тарихы.csv', 'CSV (*.csv)')
        if not filename:
            return
        names = {person['id']: person['name'] for person in [self.user] + self.users}
        try:
            with open(filename, 'w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['Хабарлама нөмірі', 'Жіберуші', 'Алушы', 'Уақыты', 'Мәтін'])
                for msg in self.messages:
                    writer.writerow([msg['id'], self.csv_text(names.get(msg['sender'], msg['sender'])),
                                     self.csv_text(names.get(msg['recipient'], msg['recipient'])),
                                     datetime.fromtimestamp(msg['sent']).isoformat(timespec='seconds'),
                                     self.csv_text(msg['body'])])
        except (OSError, UnicodeError):
            self.status.setText('Файл сақталмады. Қолжетімді буманы таңдаңыз.')
            return
        self.status.setText('Чат тарихы CSV файлына сақталды.')

    def clear_session(self):
        self.epoch += 1
        self.busy = self.poll_busy = False
        self.api = self.user = None
        self.peer = 0
        self.users = []
        self.messages = []
        self.drafts.clear()
        self.retry_messages.clear()
        self.account.clear()
        self.people.clear()
        self.history.clear()
        self.body.clear()
        self.message_filter.clear()
        self.chat_title.setText('Сұхбаттасушыны таңдаңыз')
        self.peer_avatar.setText('…')
        self.ai_table.setRowCount(0)
        self.ai_rows = []
        self.ai_detail.clear()
        self.ai_export_status.clear()
        self.ai_summary.setText('Талдау үшін чат таңдаңыз.')
        self.server_label.clear()
        self.connection_result.clear()
        self.stack.setCurrentIndex(0)

    def logout(self):
        if self.api:
            api = self.api
            if not self.request(lambda: api.request('/logout', {}), lambda _: self.clear_session()):
                self.status.setText('Ағымдағы әрекет аяқталған соң шығуды қайталаңыз.')

    def closeEvent(self, event):
        self.closed = True
        self.epoch += 1
        self.timer.stop()
        event.accept()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Noname Messenger клиенті')
    parser.add_argument('--server', default=None)
    parser.add_argument('--ca')
    parser.add_argument('--profile')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setStyleSheet(STYLE)
    window = Window(args.server, args.ca)
    if args.profile:
        try:
            profile = load_profile(args.profile)
            window.address.setText(profile['url'])
            window.ca_pem = profile['ca_pem']
            window.ca = None
            window.profile_note.setText('Қосылу профилі жүктелді.')
        except (ClientError, ValueError, OSError) as error:
            window.auth_status.setText(str(error))
    window.show()
    sys.exit(app.exec())


