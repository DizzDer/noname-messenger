"""Noname Messenger бағдарламасының қазақша PySide6 интерфейсі."""
import argparse
from datetime import datetime
import secrets
import sys
import time
from PySide6.QtCore import QObject, QRunnable, QRect, QSettings, QSize, QThreadPool, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from api import Client, ClientError
from decision import rank

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
QLineEdit,QComboBox {background:white;border:1px solid #dae7e8;border-radius:7px;padding:9px;}
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
    def __init__(self, address, ca=None):
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
        self.preferences = QSettings('Noname', 'Messenger')
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.auth_page(address, ca)
        self.chat_page()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.apply_preferences()

    def request(self, operation, success, quiet=False):
        if self.busy:
            return False
        self.busy = True
        task = Request(operation)
        self.task = task
        def finished(result, error):
            self.busy = False
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
        body.addWidget(button('Жалғастыру', self.authenticate, True))
        self.password.returnPressed.connect(self.authenticate)
        body.addStretch()
        self.stack.addWidget(page)

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
        composer.addWidget(button('Жіберу', self.send, True))
        right.addWidget(composer_panel)
        self.status = QLabel()
        self.status.setObjectName('muted')
        right.addWidget(self.status)
        self.stack.addWidget(page)
        menu = self.menuBar().addMenu('Әрекеттер')
        menu.addAction('Жаңарту', self.refresh)
        menu.addAction('Баптаулар', self.show_preferences)

    def apply_preferences(self):
        style = STYLE.replace("font:14px", f"font:{self.preferences.value('font', 14, type=int)}px")
        if self.preferences.value('dark', False, type=bool):
            for light, dark in DARK_COLORS.items():
                style = style.replace(light, dark)
            style = style.replace('background:white;', 'background:#17232e;')
        self.setStyleSheet(style)
        self.timer.start(1000 * self.preferences.value('refresh', 2, type=int))
        if self.user:
            self.render_messages()

    def show_preferences(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Баптаулар')
        dialog.setMinimumWidth(380)
        form = QFormLayout(dialog)
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
        buttons = QDialogButtonBox()
        buttons.addButton('Сақтау', QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton('Бас тарту', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            for key, value in {
                'dark': theme.currentIndex() == 1, 'font': font.value(), 'refresh': refresh.value(),
                'timestamps': stamps.isChecked(), 'enter': enter.isChecked(),
            }.items():
                self.preferences.setValue(key, value)
            self.preferences.sync()
            self.apply_preferences()

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
            api = Client(self.address.text().strip(), self.ca)
        except (ClientError, ValueError, OSError) as error:
            QMessageBox.warning(self, 'Мекенжай', str(error) if isinstance(error, ClientError)
                                else 'Сервер мекенжайын немесе сертификатты тексеріңіз.')
            return
        def done(result):
            self.api, self.user = api, result['user']
            api.token = result['token']
            self.password.clear()
            self.account.setText(self.user['name'] + ' · @' + self.user['username'])
            self.stack.setCurrentIndex(1)
            self.refresh()
        self.request(lambda: api.request('/register' if registering else '/login', data), done)

    def refresh(self):
        if self.api is None or self.busy:
            return
        api, peer = self.api, self.peer
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
            elif peer != self.peer:
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
            width = min(350, max(112, metrics.horizontalAdvance(message['body']) + 18))
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
        body = self.body.text().strip()
        if not self.peer or not body:
            self.status.setText('Сұхбаттасушыны таңдап, хабарлама жазыңыз.')
            return
        api = self.api
        peer = self.peer
        data = {'recipient': peer, 'body': body, 'nonce': secrets.token_urlsafe(24)}
        def done(_result):
            if self.peer == peer and self.body.text().strip() == body:
                self.body.clear()
            elif self.drafts.get(peer, '').strip() == body:
                self.drafts.pop(peer, None)
            self.refresh()
        if not self.request(lambda: api.request('/messages', data), done):
            self.status.setText('Жаңарту аяқталған соң қайта басыңыз.')

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
        if not self.user:
            return
        rows = rank(self.messages, self.user['id'], time.time())
        dialog = QDialog(self)
        dialog.setWindowTitle('Жауап беру кезегін талдау')
        dialog.resize(980, 460)
        layout = QVBoxLayout(dialog)
        formula = QLabel('Балл = 100 × (0,65 × ЖИ жеделдігі + 0,25 × күту + 0,10 × жаңалық)')
        layout.addWidget(formula)
        note = QLabel('Күту — сағат / 24, ең көбі 1. Жаңалық — соңғы сағатта келсе 1, әйтпесе 0. '
                      'Модель мәтінді осы құрылғыда талдайды. Белгісіз сөздер басым болса, ұсынысты өзіңіз тексеріңіз.')
        note.setWordWrap(True)
        layout.addWidget(note)
        label = QLabel('Алдымен қарау ұсынылады: ' + rows[0][5]['body'][:85] if rows else 'Талдауға кіріс хабарлама жоқ.')
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        table = QTableWidget(len(rows), 6)
        table.setHorizontalHeaderLabels(['Хабарлама', 'ЖИ бағасы', 'Күту', 'Жаңалық', 'Тірек сөздер', 'Балл'])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i, (score, p, wait, recent, signals, msg) in enumerate(rows):
            for j, value in enumerate([msg['body'], f'{p:.2f}', f'{wait:.2f}', str(recent), ', '.join(signals) or 'Таныс сөз жоқ', '']):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(i, j, item)
            bar = QProgressBar()
            bar.setValue(round(score))
            bar.setFormat(f'{score:.1f}')
            table.setCellWidget(i, 5, bar)
        layout.addWidget(table)
        layout.addWidget(button('Жабу', dialog.accept))
        dialog.exec()

    def clear_session(self):
        self.api = self.user = None
        self.peer = 0
        self.users = []
        self.messages = []
        self.drafts.clear()
        self.people.clear()
        self.history.clear()
        self.body.clear()
        self.message_filter.clear()
        self.chat_title.setText('Сұхбаттасушыны таңдаңыз')
        self.peer_avatar.setText('…')
        self.stack.setCurrentIndex(0)

    def logout(self):
        if self.api:
            api = self.api
            self.request(lambda: api.request('/logout', {}), lambda _: self.clear_session())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Noname Messenger клиенті')
    parser.add_argument('--server', default='http://127.0.0.1:8765')
    parser.add_argument('--ca')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setStyleSheet(STYLE)
    window = Window(args.server, args.ca)
    window.show()
    sys.exit(app.exec())


