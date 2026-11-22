"""Жеке дерексіз бастапқы SQLite базасы мен SQL сызбасын жасау."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3

from server import Store


def create_template(path):
    path = Path(path)
    if path.exists():
        raise FileExistsError('Файл бар. Басқа атауды таңдаңыз: ' + str(path))
    Store(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('PRAGMA journal_mode=DELETE')
        schema = '\n'.join(db.iterdump()) + '\n'
    return schema


def main():
    parser = argparse.ArgumentParser(description='Жеке дерексіз жоба дерекқорын жасау')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'Project_Database.sqlite3')
    parser.add_argument('--schema', type=Path)
    args = parser.parse_args()
    try:
        schema = create_template(args.output)
        if args.schema:
            args.schema.write_text(schema, encoding='utf-8')
    except (OSError, sqlite3.Error) as error:
        parser.exit(1, str(error) + '\n')
    print('Бастапқы дерекқор дайын: ' + str(args.output))
    print('users, sessions, messages кестелері бос. Жеке деректер енгізілмеген.')


if __name__ == '__main__':
    main()
