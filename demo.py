"""Run an isolated local server and two desktop clients for a classroom demo."""
from pathlib import Path
import subprocess
import sys
import threading

from server import MessengerServer


def main():
    base = Path(__file__).resolve().parent
    server = MessengerServer(('127.0.0.1', 0), base / 'data' / 'demo.sqlite3')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f'http://127.0.0.1:{server.server_port}'
    print('Noname Messenger екі терезеде ашылады.')
    print('Әр терезеде бөлек тіркелгімен кіріп, сұхбаттасушыны таңдаңыз.')
    print('Хабарламалар data/demo.sqlite3 базасына сақталады.')
    children = []
    try:
        for _ in range(2):
            children.append(subprocess.Popen([sys.executable, str(base / 'client.py'), '--server', address], cwd=base))
        for process in children:
            process.wait()
    except KeyboardInterrupt:
        for process in children:
            if process.poll() is None:
                process.terminate()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == '__main__':
    main()

