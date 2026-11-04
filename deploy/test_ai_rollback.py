import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import install


class AIRollbackTests(unittest.TestCase):
    def test_charges_and_unknown_reservations_survive_restoring_old_database(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/'latest.db', Path(directory)/'restored.db'
            with closing(sqlite3.connect(source)) as db, db:
                db.execute("CREATE TABLE ai_calls(id INTEGER PRIMARY KEY,job_id TEXT,month TEXT,started INTEGER,completed INTEGER,status TEXT,reserved_nano INTEGER,cost_upper_nano INTEGER,prompt_tokens INTEGER,cached_tokens INTEGER,completion_tokens INTEGER,http_status INTEGER,pricing TEXT,price_basis TEXT)")
                db.execute("INSERT INTO ai_calls VALUES(1,'job-1','2026-11',100,0,'running',100000,NULL,NULL,NULL,NULL,0,'{}','reserved_peak')")
                db.execute("INSERT INTO ai_calls VALUES(2,'job-2','2026-11',101,102,'succeeded',100000,1234,10,0,20,200,'{}','off_peak_estimate')")
            with closing(sqlite3.connect(target)) as db, db:
                db.execute('CREATE TABLE unrelated(value TEXT)')
                db.execute("INSERT INTO unrelated VALUES('unchanged')")
                db.execute('CREATE TABLE ai_settings(enabled INTEGER)')
                db.execute('INSERT INTO ai_settings VALUES(1)')
            install.preserve_ai_ledger(source, target)
            install.preserve_ai_ledger(source, target)
            install.pause_ai(target)
            with closing(sqlite3.connect(target)) as db:
                self.assertEqual(db.execute('SELECT SUM(COALESCE(cost_upper_nano,reserved_nano)) FROM ai_calls').fetchone()[0],101234)
                self.assertEqual(db.execute('SELECT value FROM unrelated').fetchone()[0],'unchanged')
                self.assertEqual(db.execute('SELECT enabled FROM ai_settings').fetchone()[0],0)

    def test_conflicting_call_identity_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target=Path(directory)/'latest.db',Path(directory)/'restored.db'
            for path, job in ((source,'new'),(target,'different')):
                with closing(sqlite3.connect(path)) as db,db:
                    db.execute("CREATE TABLE ai_calls(id INTEGER PRIMARY KEY,job_id TEXT,month TEXT,started INTEGER,completed INTEGER,status TEXT,reserved_nano INTEGER,cost_upper_nano INTEGER,prompt_tokens INTEGER,cached_tokens INTEGER,completion_tokens INTEGER,http_status INTEGER,pricing TEXT,price_basis TEXT)")
                    db.execute("INSERT INTO ai_calls VALUES(1,?,'2026-11',100,0,'running',100000,NULL,NULL,NULL,NULL,0,'{}','reserved_peak')",(job,))
            with self.assertRaisesRegex(RuntimeError,'identity'):
                install.preserve_ai_ledger(source,target)
            with closing(sqlite3.connect(target)) as db:
                self.assertEqual(db.execute('SELECT job_id FROM ai_calls').fetchone()[0],'different')

    def test_legacy_database_and_missing_files_remain_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'empty.db'
            install.pause_ai(target)
            with closing(sqlite3.connect(target)) as db: db.execute('CREATE TABLE legacy(id INTEGER)')
            install.preserve_ai_ledger(Path(directory)/'missing.db',target)
            install.pause_ai(target)


if __name__ == '__main__':
    unittest.main()
