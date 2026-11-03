"""A recovered SQLite export must retain data and its own file metadata."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]


class SQLiteBackupMetadataTests(unittest.TestCase):
    def test_consistent_export_preserves_source_mode_and_records_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.db'
            with sqlite3.connect(source) as database:
                database.execute('create table recovery (value text)')
                database.execute("insert into recovery values ('recoverable')")
            source.chmod(0o640)
            if os.geteuid() == 0:
                os.chown(source, 5050, 0)  # pgAdmin differs from its parent directory.
            stage = root / 'stage'
            (stage / 'inventory').mkdir(parents=True)
            (stage / 'plan.json').write_text(json.dumps({'sqlite': [str(source)], 'sqlite_scan': []}))
            subprocess.run([sys.executable, str(REPO / 'backup/infra-backup-sqlite.py'), str(stage)],
                           check=True, capture_output=True)
            entry = json.loads((stage / 'inventory/sqlite-manifest.json').read_text())[0]
            export = Path(entry['backup'])
            self.assertEqual(export.stat().st_mode & 0o7777, 0o640)
            self.assertEqual((export.stat().st_uid, export.stat().st_gid),
                             (source.stat().st_uid, source.stat().st_gid))
            self.assertEqual((entry['uid'], entry['gid'], entry['mode']),
                             (source.stat().st_uid, source.stat().st_gid, 0o640))
            with sqlite3.connect(export) as database:
                self.assertEqual(database.execute('select value from recovery').fetchall(), [('recoverable',)])

    def test_coordinated_export_uses_captured_database_even_after_live_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'data/app.db'
            source.parent.mkdir()
            with sqlite3.connect(source) as database:
                database.execute('create table records (value text)')
                database.execute("insert into records values ('captured')")
            stage = root / 'stage'
            captured = stage / 'quiesced' / source.relative_to('/')
            captured.parent.mkdir(parents=True)
            import shutil
            shutil.copy2(source, captured)
            with sqlite3.connect(source) as database:
                database.execute("update records set value='later write'")
            (stage / 'inventory').mkdir()
            (stage / 'plan.json').write_text(json.dumps({'sqlite': [], 'sqlite_scan': [str(source.parent)], 'quiesce': {'app': {'copy': [str(source.parent)]}}}))
            subprocess.run([sys.executable, str(REPO / 'backup/infra-backup-sqlite.py'), str(stage)], check=True, capture_output=True)
            entries = json.loads((stage / 'inventory/sqlite-manifest.json').read_text())
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['source'], str(source))
            with sqlite3.connect(entries[0]['backup']) as database:
                self.assertEqual(database.execute('select value from records').fetchall(), [('captured',)])
