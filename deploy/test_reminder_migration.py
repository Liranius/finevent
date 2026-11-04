from contextlib import closing
import sqlite3
import unittest

import calendar_release as release


class ReminderMigrationTests(unittest.TestCase):
    def database(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.executescript('CREATE TABLE calendar_events(id TEXT PRIMARY KEY);'
                          'CREATE TABLE deliveries(id INTEGER PRIMARY KEY,status TEXT);'
                          'CREATE TABLE calendar_reminder_settings(id INTEGER PRIMARY KEY,payload TEXT);'
                          'CREATE TABLE calendar_reminder_watches(event_id TEXT PRIMARY KEY,enabled INTEGER);'
                          'CREATE TABLE calendar_reminder_jobs(key TEXT PRIMARY KEY,delivery_id INTEGER REFERENCES deliveries(id),'
                          'event_id TEXT REFERENCES calendar_events(id),anchor TEXT,scheduled_at INTEGER);')
        return conn

    def test_new_schema_must_be_complete_disabled_and_without_jobs(self):
        with closing(self.database()) as conn:
            state = release.reminder_state(conn)
            proof = release.verify_reminder_migration({'schema': 'absent'}, state)
            self.assertEqual(proof['status'], 'passed')
            self.assertFalse(proof['device_delivery_verified'])
            conn.execute("INSERT INTO calendar_reminder_settings VALUES(1,'{\"enabled\":true}')")
            with self.assertRaises(RuntimeError):
                release.verify_reminder_migration({'schema': 'absent'}, release.reminder_state(conn))
            conn.execute('DROP TABLE calendar_reminder_jobs')
            with self.assertRaises(RuntimeError):
                release.reminder_state(conn)

    def test_job_identity_is_preserved_while_delivery_status_can_advance(self):
        with closing(self.database()) as conn:
            conn.execute("INSERT INTO calendar_events VALUES('event')")
            conn.execute("INSERT INTO deliveries VALUES(1,'queued')")
            conn.execute("INSERT INTO calendar_reminder_jobs VALUES('stable-key',1,'event','early',100)")
            before = release.reminder_state(conn)
            conn.execute("UPDATE deliveries SET status='accepted'")
            after = release.reminder_state(conn)
            self.assertEqual(before, after)
            release.verify_reminder_migration(before, after)
            release.verify_restart({'reminders': before}, {'reminders': after})
            conn.execute("UPDATE calendar_reminder_jobs SET key='changed-key'")
            with self.assertRaises(RuntimeError):
                release.verify_restart({'reminders': before}, {'reminders': release.reminder_state(conn)})
            conn.execute('DELETE FROM deliveries')
            with self.assertRaises(RuntimeError):
                release.reminder_state(conn)

    def test_changed_settings_or_follows_fail_migration_comparison(self):
        with closing(self.database()) as conn:
            before = release.reminder_state(conn)
            conn.execute("INSERT INTO calendar_reminder_watches VALUES('event',1)")
            with self.assertRaises(RuntimeError):
                release.verify_reminder_migration(before, release.reminder_state(conn))
        with closing(sqlite3.connect(':memory:')) as conn:
            self.assertEqual(release.reminder_state(conn), {'schema': 'absent'})


if __name__ == '__main__':
    unittest.main()
