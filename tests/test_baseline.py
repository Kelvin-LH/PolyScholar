# SPDX-License-Identifier: AGPL-3.0-only
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript((ROOT / 'schemas/001_initial.sql').read_text())
    def tearDown(self):
        self.db.close()
    def test_foreign_keys_reject_orphan_file(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('INSERT INTO files VALUES (?,?,?,?)', ('f','missing','a'*64,'file.pdf'))
    def test_rejects_invalid_task_state_and_json(self):
        self.db.execute("INSERT INTO documents VALUES ('d','Title',NULL,'{}','now')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO jobs VALUES ('j','d','unknown','{}')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO jobs VALUES ('j','d','queued','not-json')")
    def test_baseline_links_and_schema(self):
        subprocess.run([sys.executable, str(ROOT/'scripts/check_baseline.py')], check=True,
                       stdout=subprocess.DEVNULL)

if __name__ == '__main__':unittest.main()
