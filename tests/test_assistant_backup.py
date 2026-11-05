"""Only synthetic WAL files; this test never opens a production database."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from maintenance.assistant_test_backup import backup_sqlite


class MaintenanceBackupTest(unittest.TestCase):
    def test_committed_wal_is_in_consistent_backup_without_changing_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve(); source=root/'synthetic.db'; destination=root/'snapshot.db'
            connection=sqlite3.connect(source)
            try:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('PRAGMA wal_autocheckpoint=0')
                connection.execute('CREATE TABLE events(value TEXT)')
                connection.execute("INSERT INTO events VALUES('synthetic only')"); connection.commit()
                self.assertGreater(Path(str(source)+'-wal').stat().st_size,0)
                with self.assertRaises(ValueError): backup_sqlite(source,destination)
                self.assertFalse(destination.exists())
                result=backup_sqlite(source,destination,maintenance_started=True)
                self.assertTrue(result['complete']); self.assertEqual(destination.stat().st_mode & 0o777,0o600)
                with sqlite3.connect(destination) as copy:
                    self.assertEqual(copy.execute('SELECT value FROM events').fetchall(),[('synthetic only',)])
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)
                with self.assertRaises(FileExistsError): backup_sqlite(source,destination,maintenance_started=True)
            finally: connection.close()

    def test_timeout_marks_copy_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve(); source=root/'synthetic.db'; target=root/'snapshot.db'
            with sqlite3.connect(source) as db: db.execute('CREATE TABLE synthetic(id INTEGER)')
            with self.assertRaises(TimeoutError): backup_sqlite(source,target,maintenance_started=True,timeout=-1)
            self.assertFalse(target.exists()); self.assertTrue(root.joinpath('snapshot.db.incomplete').exists())
