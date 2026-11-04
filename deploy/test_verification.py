from contextlib import closing
from datetime import date
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import verification as verify


def create_database(path):
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE schema_version(version INTEGER PRIMARY KEY);
        INSERT INTO schema_version VALUES(1);
        CREATE TABLE sessions(token TEXT);
        INSERT INTO sessions VALUES('SECRET-MUST-NOT-BE-EXPORTED');
        CREATE TABLE news(id INTEGER,source TEXT,source_id TEXT,published_at INTEGER,first_seen INTEGER,baseline INTEGER);
        CREATE TABLE polls(id INTEGER,source TEXT,started INTEGER,completed INTEGER,success INTEGER,added INTEGER,changed INTEGER,gap TEXT,error TEXT);
        CREATE TABLE resources(at INTEGER,rss INTEGER,go_sys INTEGER,available INTEGER,cgroup_current INTEGER,cgroup_peak INTEGER,oom_kills INTEGER);
        """)
        db.commit()


class VerificationTests(unittest.TestCase):
    def test_missing_session_and_edges_are_reported(self):
        gap = verify.continuity([], 100, 7300, 240)
        self.assertEqual(gap["max_interval_seconds"], 7200)
        self.assertEqual(gap["intervals_over_threshold"], 1)
        normal = verify.continuity(range(100, 7301, 120), 100, 7300, 240)
        self.assertEqual(normal["intervals_over_threshold"], 0)
        trailing = verify.continuity([100, 220], 100, 7300, 240)
        self.assertEqual(trailing["max_interval_seconds"], 7080)

    def test_latency_excludes_baseline_and_clock_errors(self):
        rows = [{"baseline": 0, "published_at": 100, "first_seen": 168},
                {"baseline": 0, "published_at": 100, "first_seen": 500},
                {"baseline": 1, "published_at": 100, "first_seen": 9999},
                {"baseline": 0, "published_at": 500, "first_seen": 100},
                {"baseline": 0, "published_at": 0, "first_seen": 100}]
        result = verify.latency(rows)
        self.assertEqual(result["samples"], 2)
        self.assertEqual(result["p95_seconds"], 400)
        self.assertEqual(result["within_5_minutes"], 0.5)
        self.assertEqual(result["clock_errors"], 1)
        self.assertEqual(result["unknown_publication"], 1)

    def test_daily_audit_contains_failure_context_not_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"data.db"; create_database(path)
            day = date(2026, 10, 27); morning, end = verify.window(day, "09:30", "11:30")
            with closing(sqlite3.connect(path)) as db:
                db.execute("INSERT INTO news VALUES(1,'wallstreetcn','public-id',?,?,0)", (end-60, end+400))
                db.execute("INSERT INTO polls VALUES(1,'wallstreetcn',?,?,0,0,0,'','source HTTP 429')", (morning-86400, morning-86400+1))
                db.execute("INSERT INTO resources VALUES(?,100,80,1000,120,200,0)", (morning,))
                db.execute("INSERT INTO resources VALUES(?,100,80,900,120,150,0)", (morning+60,))
                db.commit()
            report = verify.audit_database(path, [day])
            self.assertEqual(report["days"][0]["sources"]["wallstreetcn"]["sessions"][0]["latency_by_publication_time"]["samples"], 1)
            self.assertEqual(report["poll_failures"][0]["error"], "source HTTP 429")
            self.assertEqual(len(report["resources_overall"]["counter_reset_candidates"]), 1)
            self.assertNotIn("SECRET-MUST-NOT-BE-EXPORTED", json.dumps(report))
            with closing(verify.readonly(path)) as db:
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("DELETE FROM news")

    def test_snapshot_includes_live_wal_and_is_independent(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"source.db"; target = Path(temporary)/"snapshot.db"
            create_database(path)
            with closing(sqlite3.connect(path)) as live:
                live.execute("PRAGMA wal_autocheckpoint=0")
                live.execute("INSERT INTO news VALUES(1,'fast','one',100,150,0)"); live.commit()
                verify.snapshot(path, target)
                live.execute("INSERT INTO news VALUES(2,'fast','two',100,150,0)"); live.commit()
                with closing(verify.readonly(target)) as saved:
                    self.assertEqual(saved.execute("SELECT COUNT(*) FROM news").fetchone()[0], 1)

    def rehearsal_fixture(self, directory):
        base = Path(directory); data = base/"data"; data.mkdir(); create_database(data/"finevent.db")
        releases = base/"releases"; releases.mkdir()
        previous = releases/"previous"; current = releases/"current"
        previous.mkdir(); current.mkdir()
        deployment = base/"deployment"; deployment.mkdir()
        (deployment/"state.json").write_text(json.dumps({"previous": str(previous)}))
        evidence = base/"evidence"; evidence.mkdir()

        class Root:
            def __truediv__(self, child):
                return current if child == "current" else releases

        return data, Root(), deployment, evidence

    def test_restore_validates_before_replacing_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)/"live.db"; create_database(target)
            invalid = Path(temporary)/"bad.db"; invalid.write_bytes(b"not a database")
            original = target.read_bytes()
            with self.assertRaises(sqlite3.DatabaseError):
                verify.installer.restore_database_snapshot(invalid, target)
            self.assertEqual(target.read_bytes(), original)
            saved = Path(temporary)/"saved.db"; create_database(saved)
            with closing(sqlite3.connect(saved)) as db:
                db.execute("INSERT INTO news VALUES(1,'fast','latest',100,200,0)"); db.commit()
            digest = verify.installer.restore_database_snapshot(saved, target)
            self.assertEqual(digest, verify.installer.sha(saved))
            self.assertEqual(digest, verify.installer.sha(target))
            with closing(verify.readonly(target)) as db:
                self.assertEqual(db.execute("SELECT source_id FROM news").fetchone()[0], "latest")

    def test_partial_snapshot_failure_restarts_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            data, root, deployment, evidence = self.rehearsal_fixture(temporary)
            def fail_snapshot(_source, target):
                target.write_text("partial")
                raise RuntimeError("snapshot failed")
            with patch.object(verify, "DATA", data), patch.object(verify, "ROOT", root), patch.object(verify.installer, "verify_bundle"), patch.object(verify, "command", return_value=subprocess.CompletedProcess([], 0, "", "")) as command, patch.object(verify, "snapshot", side_effect=fail_snapshot), patch.object(verify.installer, "rollback") as rollback:
                with self.assertRaisesRegex(RuntimeError, "snapshot failed"):
                    verify.production_roundtrip(deployment, evidence)
                calls = [c.args for c in command.call_args_list]
                self.assertIn(("systemctl", "start", "finevent.service"), calls)
                self.assertIn(("systemctl", "start", "finevent-backup.timer"), calls)
                rollback.assert_not_called()

    def test_rollback_failure_still_attempts_return_to_new_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            data, root, deployment, evidence = self.rehearsal_fixture(temporary)
            with patch.object(verify, "DATA", data), patch.object(verify, "ROOT", root), patch.object(verify.installer, "verify_bundle"), patch.object(verify, "command", return_value=subprocess.CompletedProcess([], 0, "", "")), patch.object(verify.installer, "rollback", side_effect=RuntimeError("old release failed")), patch.object(verify.installer, "install") as install:
                with self.assertRaisesRegex(RuntimeError, "old release failed"):
                    verify.production_roundtrip(deployment, evidence)
                install.assert_called_once()
                with closing(verify.readonly(evidence/"rollback-rehearsal"/"database.db")) as saved:
                    self.assertEqual(saved.execute("SELECT token FROM sessions").fetchone()[0], "SECRET-MUST-NOT-BE-EXPORTED")


if __name__ == "__main__":
    unittest.main()
