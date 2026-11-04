from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import install


class ReminderRollbackTests(unittest.TestCase):
    def test_pause_preserves_history_and_does_not_expose_jobs_to_old_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'finevent.db'
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript("CREATE TABLE calendar_reminder_settings(id INTEGER,payload TEXT);"
                                   "CREATE TABLE deliveries(id INTEGER,scenario TEXT,status TEXT,receipt_token TEXT,error TEXT);"
                                   "INSERT INTO deliveries VALUES(1,'calendar','queued','secret',''),(2,'calendar','sending','secret',''),(3,'calendar','accepted','',''),(4,'locked','queued','probe','');")
                conn.execute('INSERT INTO calendar_reminder_settings VALUES(1,?)', (json.dumps({'enabled':True,'devices':[1],'fund_codes':['123456']}),))
            self.assertEqual(install.pause_calendar_reminders(path), 2)
            self.assertEqual(install.pause_calendar_reminders(path), 0)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT status,receipt_token FROM deliveries ORDER BY id').fetchall(), [('cancelled',''),('cancelled',''),('accepted',''),('queued','probe')])
                self.assertEqual(json.loads(conn.execute('SELECT payload FROM calendar_reminder_settings').fetchone()[0]), {'enabled':False,'devices':[1],'fund_codes':['123456']})

    def test_legacy_database_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'finevent.db'
            self.assertEqual(install.pause_calendar_reminders(path), 0)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute('CREATE TABLE original(value TEXT)')
            before = path.read_bytes()
            self.assertEqual(install.pause_calendar_reminders(path), 0)
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
