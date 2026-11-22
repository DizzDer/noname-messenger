"""Isolated Qt integration checks for the criteria edition; needs PySide6."""
import csv
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import traceback

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parent
PROJECT = ROOT if (ROOT / 'client.py').is_file() else ROOT / 'noname-criteria'
for candidate in (ROOT / 'noname-qa-deps', PROJECT / '.runtime', os.environ.get('NONAME_QA_DEPS')):
    if candidate:
        sys.path.insert(0, str(candidate))
sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QSettings, QThreadPool, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
import client
from lan_server import create_server


def main():
    app = QApplication([])
    # The Windows offscreen plugin starts without a system font database.
    for font_file in ('segoeui.ttf', 'segoeuib.ttf', 'segoeuii.ttf'):
        font_path = Path('C:/Windows/Fonts') / font_file
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont('Segoe UI', 10))
    app.setQuitOnLastWindowClosed(False)
    screenshots = ROOT / ('qa-gui' if PROJECT == ROOT else 'noname-criteria-qa')
    screenshots.mkdir(exist_ok=True)
    failures, warnings, passed, windows = [], [], [], []
    sys.excepthook = lambda kind, error, tb: failures.append(''.join(traceback.format_exception(kind, error, tb)))
    original_warning = QMessageBox.warning
    original_open = QFileDialog.getOpenFileName
    original_save = QFileDialog.getSaveFileName
    original_settings = client.QSettings
    QMessageBox.warning = lambda *args: warnings.append(str(args[-1]))
    server = None

    def settle(predicate, label, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if failures:
                raise AssertionError(failures[-1])
            if predicate():
                return
            time.sleep(.015)
        raise AssertionError('Timed out: ' + label)

    def refresh_until(window, predicate, label):
        def poll():
            window.refresh()
            return predicate()
        settle(poll, label)

    def choose(window, peer):
        window.tabs.setCurrentIndex(0)
        refresh_until(window, lambda: any(window.people.item(i).data(Qt.ItemDataRole.UserRole) == peer
                                         for i in range(window.people.count())), 'users sync')
        for index in range(window.people.count()):
            if window.people.item(index).data(Qt.ItemDataRole.UserRole) == peer:
                window.people.setCurrentRow(index)
                break
        settle(lambda: window.peer == peer and not window.poll_busy, 'peer selection')

    try:
        with tempfile.TemporaryDirectory(prefix='noname-criteria-qt-', dir=ROOT) as directory:
            temp = Path(directory)
            client.QSettings = lambda *_: QSettings(str(temp / 'preferences.ini'), QSettings.Format.IniFormat)
            server = create_server('127.0.0.1', 0, temp / 'test.sqlite3', temp / 'network')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            profile = temp / 'network' / 'connection.json'

            def register(username, name):
                window = client.Window('http://127.0.0.1:1')
                windows.append(window)
                window.show()
                QFileDialog.getOpenFileName = lambda *_, **__: (str(profile), 'JSON (*.json)')
                window.import_profile()
                window.check_connection()
                settle(lambda: not window.busy, 'HTTPS health')
                assert 'Байланыс бар' in window.auth_status.text()
                window.mode.setCurrentIndex(1)
                window.login.setText(username)
                window.name.setText(name)
                window.password.setText('Criteria-Test-Password-2026')
                app.processEvents()
                if username == 'alice':
                    window.grab().save(str(screenshots / 'login.png'))
                window.authenticate()
                settle(lambda: window.user is not None, 'registration')
                assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
                    'Чаттар', 'Қосылым', 'Баптаулар', 'ЖИ клиенті', 'Талдау', 'Көмек']
                return window

            alice = register('alice', 'Нұрмухамед')
            bob = register('bob', 'Қуат')
            passed.append('verified HTTPS profile import and two Qt registrations')
            choose(alice, bob.user['id'])
            choose(bob, alice.user['id'])
            for sender, text in [
                (bob, 'Сәлем! Қорғауға дайынсың ба?'),
                (alice, 'Иә, чат пен іздеуді көрсетемін.'),
                (bob, 'Шұғыл көмек керек, дереу хабарлас!'),
                (alice, 'Қазір тексеремін. ЖИ есебін де көрсетеміз.'),
                (bob, 'Ертең жоспар құрамыз, асықпай қарап шық.'),
            ]:
                sender.body.setText(text)
                sender.send()
                settle(lambda: not sender.busy and not sender.body.text(), 'send acknowledgement')
            refresh_until(alice, lambda: len(alice.messages) == 5, 'incoming delivery')
            alice.grab().save(str(screenshots / 'chat.png'))
            alice.analyze()
            assert alice.ai_table.rowCount() == 3
            assert 'Хабарлама №' in alice.ai_detail.toPlainText()
            assert 'үлесі' in alice.ai_detail.toPlainText()
            alice.grab().save(str(screenshots / 'ai.png'))
            passed.append('cross-client message delivery and real-message weighted explanation')

            rank_csv = temp / 'rank.csv'
            QFileDialog.getSaveFileName = lambda *_, **__: (str(rank_csv), 'CSV (*.csv)')
            alice.export_ranking()
            records = list(csv.reader(rank_csv.open(encoding='utf-8-sig', newline='')))
            assert len(records) == 4 and records[1][2] == alice.ai_rows[0][5]['body']
            assert abs(float(records[1][9]) - alice.ai_rows[0][0]) < .000001
            history_csv = temp / 'history.csv'
            QFileDialog.getSaveFileName = lambda *_, **__: (str(history_csv), 'CSV (*.csv)')
            alice.export_history()
            assert len(list(csv.reader(history_csv.open(encoding='utf-8-sig', newline='')))) == 6
            assert client.Window.csv_text('=1+1') == "'=1+1"
            assert client.Window.csv_text('Қазақша') == 'Қазақша'
            passed.append('UTF-8 CSV ranking and history export with formula-safe message text')

            alice.tabs.setCurrentIndex(4)
            assert alice.demo_table.rowCount() == 3
            assert alice.demo_rows[0][5]['id'] == 1
            alice.weight_controls['urgency'].setValue(0)
            alice.weight_controls['wait'].setValue(100)
            alice.weight_controls['recent'].setValue(0)
            alice.save_weights()
            assert alice.demo_rows[0][5]['id'] == 2
            assert alice.preferences.value('weight_wait', type=float) == 1
            reopened = client.Window('http://127.0.0.1:1')
            windows.append(reopened)
            assert reopened.weights['wait'] == 1
            reopened.close()
            before = dict(alice.weights)
            for control in alice.weight_controls.values():
                control.setValue(0)
            alice.save_weights()
            assert alice.weights == before and 'нөлден үлкен' in alice.weight_status.text()
            alice.reset_weights()
            assert alice.demo_rows[0][5]['id'] == 1
            alice.grab().save(str(screenshots / 'analysis.png'))
            alice.resize(900, 570)
            app.processEvents()
            alice.grab().save(str(screenshots / 'analysis-minimum.png'))
            alice.tabs.setCurrentIndex(5)
            alice.grab().save(str(screenshots / 'help.png'))
            passed.append('weight tradeoff changes winner, zero vector rejected, six tabs fit minimum window')

            alice.tabs.setCurrentIndex(1)
            alice.check_connection()
            settle(lambda: not alice.busy, 'authenticated health')
            alice.grab().save(str(screenshots / 'connection.png'))
            alice.tabs.setCurrentIndex(2)
            from PySide6.QtWidgets import QPushButton, QSpinBox
            settings = alice.tabs.widget(2)
            font, interval = settings.findChildren(QSpinBox)
            font.setValue(15)
            interval.setValue(3)
            next(control for control in settings.findChildren(QPushButton) if control.text() == 'Сақтау').click()
            assert alice.preferences.value('font', type=int) == 15 and alice.timer.interval() == 3000
            alice.grab().save(str(screenshots / 'settings.png'))
            passed.append('weights persist across windows and display preferences apply immediately')
            assert not warnings, warnings
            for window in windows:
                window.close()
            QThreadPool.globalInstance().waitForDone(10000)
            app.processEvents()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            server = None
        (screenshots / 'results.json').write_text(json.dumps({'passed': passed}, ensure_ascii=False, indent=2), encoding='utf-8')
        for name in ('login', 'chat', 'ai', 'analysis', 'connection', 'settings', 'help'):
            shutil.copyfile(screenshots / (name + '.png'), PROJECT / ('noname-' + name + '.png'))
        print('GUI PASS: ' + '; '.join(passed), flush=True)
    finally:
        QMessageBox.warning = original_warning
        QFileDialog.getOpenFileName = original_open
        QFileDialog.getSaveFileName = original_save
        client.QSettings = original_settings
        for window in windows:
            window.close()
        QThreadPool.globalInstance().waitForDone(10000)
        app.processEvents()
        if server:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
