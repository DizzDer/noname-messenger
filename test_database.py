"""Тапсырылатын бос SQLite базасының қайта жасалуы мен тұтастығы."""
from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from prepare_database import create_template


class DatabaseTemplateTests(unittest.TestCase):
    def test_template_has_schema_without_personal_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'project.sqlite3'
            schema = create_template(path)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                for table in ('users', 'sessions', 'messages'):
                    self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
                columns = {r[1] for r in db.execute('PRAGMA table_info(messages)')}
                self.assertIn('deleted', columns)
            # The supplied SQL must reconstruct the same enforceable schema.
            with closing(sqlite3.connect(':memory:')) as restored:
                restored.executescript(schema)
                self.assertEqual(restored.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
            with self.assertRaises(FileExistsError):
                create_template(path)


if __name__ == '__main__':
    unittest.main()
