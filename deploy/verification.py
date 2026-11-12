"""Bounded production verification. Secrets and database snapshots stay on the server."""

import argparse
from contextlib import closing
from datetime import date, datetime, time as day_time, timedelta, timezone
import getpass
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import install as installer
import verification_resources as resource_gate

ZONE = timezone(timedelta(hours=8))
DATA = Path("/srv/example/finevent-data")
ROOT = Path("/srv/example/finevent")
BACKUPS = Path("/srv/example/finevent-backups")
PRICING = {"version": "2026-10-28-peak", "input_nano_per_token": 2000,
           "cached_nano_per_token": 40, "output_nano_per_token": 8000}


def command(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=30)


def readonly(path):
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def snapshot(source, target):
    deadline = time.monotonic()+40
    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise RuntimeError("Snapshot exceeded its time limit")
    with closing(readonly(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst, pages=128, sleep=0.05, progress=progress)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Snapshot integrity check failed")
    target.chmod(0o600)


def stamp(seconds):
    return datetime.fromtimestamp(seconds, ZONE).isoformat() if seconds else None


def window(day, start="00:00", end="23:59:59"):
    return (int(datetime.combine(day, day_time.fromisoformat(start), ZONE).timestamp()),
            int(datetime.combine(day, day_time.fromisoformat(end), ZONE).timestamp()))


def continuity(times, start, end, warning_seconds):
    inside = sorted(set(t for t in times if start <= t <= end))
    edges = [start, *inside, end]
    gaps = [{"from": stamp(a), "to": stamp(b), "seconds": b-a}
            for a, b in zip(edges, edges[1:]) if b-a > warning_seconds]
    return {"observations": len(inside), "max_interval_seconds": max(b-a for a, b in zip(edges, edges[1:])),
            "intervals_over_threshold": len(gaps), "longest_intervals": sorted(gaps, key=lambda g: -g["seconds"])[:10],
            "warning_threshold_seconds": warning_seconds}


def latency(rows):
    usable = [r for r in rows if not r["baseline"]]
    values = sorted(r["first_seen"]-r["published_at"] for r in usable
                    if r["published_at"] > 0 and r["first_seen"] >= r["published_at"])
    return {"samples": len(values), "unknown_publication": sum(r["published_at"] == 0 for r in usable),
            "clock_errors": sum(r["published_at"] > r["first_seen"] for r in usable),
            "p50_seconds": values[(len(values)-1)//2] if values else None,
            "p95_seconds": values[(len(values)*95+99)//100-1] if values else None,
            "within_5_minutes": sum(v <= 300 for v in values)/len(values) if values else None,
            "over_5_minutes": sum(v > 300 for v in values)}


def resource_stats(rows, start, end):
    selected = [r for r in rows if start <= r["at"] <= end]
    positive = [r["available"] for r in selected if r["available"] > 0]
    return {"coverage": continuity([r["at"] for r in selected], start, end, 180),
            "rss_peak_mib": max((r["rss"] for r in selected), default=0)/1024**2,
            "cgroup_peak_mib": max((r["cgroup_peak"] for r in selected), default=0)/1024**2,
            "host_available_min_mib": min(positive)/1024**2 if positive else None,
            "oom_counter_max": max((r["oom_kills"] for r in selected), default=None),
            "missing_linux_metrics": sum(r["available"] <= 0 or r["cgroup_peak"] <= 0 for r in selected),
            "counter_reset_candidates": [stamp(b["at"]) for a, b in zip(selected, selected[1:])
                                         if b["cgroup_peak"] < a["cgroup_peak"] or b["oom_kills"] < a["oom_kills"]]}


def audit_database(path, days):
    start, _ = window(days[0]); _, end = window(days[-1])
    context_start = start-86400  # Include the deployment evening when diagnosing aggregate failures.
    with closing(readonly(path)) as conn:
        polls = [dict(r) for r in conn.execute("SELECT * FROM polls WHERE started BETWEEN ? AND ? ORDER BY started", (context_start, end))]
        resources = [dict(r) for r in conn.execute("SELECT * FROM resources WHERE at BETWEEN ? AND ? ORDER BY at", (start, end))]
        news = [dict(r) for r in conn.execute("SELECT id,source,source_id,published_at,first_seen,baseline FROM news WHERE first_seen>=? OR published_at>=?", (context_start, context_start))]
        count = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    sources = sorted({r["source"] for r in polls} | {"trendradar", "wallstreetcn"})
    report = {"timezone": "Asia/Shanghai", "diagnostic_context_start": stamp(context_start), "total_news": count, "days": [], "poll_failures": [], "slow_items": []}
    for day in days:
        lo, hi = window(day)
        detail = {"date": str(day), "sources": {}, "resources": resource_stats(resources, lo, min(hi, int(time.time())))}
        for source in sources:
            selected = [p for p in polls if p["source"] == source and lo <= p["started"] <= hi]
            detail["sources"][source] = {"polls": len(selected), "successes": sum(p["success"] for p in selected),
                "detected_gaps": sum(bool(p["gap"]) for p in selected), "sessions": []}
            for begin, finish in (("09:30", "11:30"), ("13:00", "15:00")):
                a, b = window(day, begin, finish)
                samples = [r for r in news if r["source"] == source and a <= r["published_at"] <= b]
                detail["sources"][source]["sessions"].append({"from": stamp(a), "to": stamp(b),
                    "coverage": continuity([p["started"] for p in selected], a, b, 240 if source == "wallstreetcn" else 180),
                    "latency_by_publication_time": latency(samples) if source == "wallstreetcn" else None})
        report["days"].append(detail)
    report["poll_failures"] = [{"source": p["source"], "started": stamp(p["started"]),
        "completed": stamp(p["completed"]), "error": p["error"][:800], "gap": p["gap"][:800]}
        for p in polls if not p["success"] or p["gap"]][:50]
    slow = sorted((r for r in news if r["source"] == "wallstreetcn" and not r["baseline"] and context_start <= r["published_at"] <= end and r["first_seen"]-r["published_at"] > 300),
                  key=lambda r: r["first_seen"]-r["published_at"], reverse=True)
    report["slow_count"] = len(slow)
    for row in slow[:20]:
        previous = [p for p in polls if p["source"] == "wallstreetcn" and p["success"] and p["completed"] < row["first_seen"]]
        last = previous[-1] if previous else None
        report["slow_items"].append({"source_id": row["source_id"], "published": stamp(row["published_at"]),
            "first_seen": stamp(row["first_seen"]), "delay_seconds": row["first_seen"]-row["published_at"],
            "prior_successful_poll": stamp(last["completed"]) if last else None})
    report["resources_overall"] = resource_stats(resources, start, min(end, int(time.time())))
    return report


def system_evidence(start):
    fields = ["ActiveState", "SubState", "NRestarts", "ExecMainStartTimestamp", "ActiveEnterTimestamp",
              "MemoryCurrent", "MemoryPeak", "MemoryMax", "ControlGroup"]
    result = command("systemctl", "show", "finevent.service", "--property="+",".join(fields))
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    journal = command("journalctl", "-u", "finevent.service", "--since", str(start), "--no-pager", "-o", "json", "-n", "500", check=False)
    events = []
    for line in journal.stdout.splitlines():
        try:
            item = json.loads(line); message = item.get("MESSAGE", "")
            if isinstance(message, str) and any(word in message for word in ("Started finevent", "Stopped finevent", "Main process exited", "Failed with result", "Scheduled restart job", "out-of-memory")):
                events.append({"at": stamp(int(item["__REALTIME_TIMESTAMP"])//1000000), "message": message[:500]})
        except (ValueError, KeyError, TypeError):
            continue
    trend = command("docker", "inspect", "--format", '{{json .State}}', "trend-radar", check=False)
    state = json.loads(trend.stdout) if trend.returncode == 0 else {}
    return {"finevent": values, "lifecycle_events": events, "journal_exit": journal.returncode,
            "journal_limit": 500, "journal_may_be_truncated": len(journal.stdout.splitlines()) >= 500,
            "trendradar": {k: state.get(k) for k in ("Running", "OOMKilled", "StartedAt", "FinishedAt")},
            "backup_timer_active": command("systemctl", "is-active", "--quiet", "finevent-backup.timer", check=False).returncode == 0}


def save_report(path, report):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def run_ai_probe(run_id, trial, evidence, align_trend=False):
    import pwd
    request_path = DATA / "ai-probe-request.json"
    if request_path.exists():
        raise RuntimeError("An AI probe request already exists; inspect it before proceeding")
    if not sys.stdin.isatty():
        raise RuntimeError("AI key input requires an interactive terminal")
    print("DeepSeek probe: one synthetic request, 1024 output tokens maximum, CNY 0.02 reservation. No automatic retries.", flush=True)
    key = getpass.getpass("DeepSeek API Key (hidden; never saved in the report): ").strip()
    if len(key) < 8 or len(key) > 512 or any(c in key for c in "\r\n"):
        raise RuntimeError("Invalid API key format")
    # Recheck after interactive input: headroom may have changed while waiting for the key.
    monitor = resource_gate.LiveProbe(trial, evidence)
    account = pwd.getpwnam("finevent")
    temporary = DATA / (".ai-probe-"+run_id)
    try:
        if align_trend:
            # Keep the key in this terminal process's memory while waiting, not on disk.
            monitor.wait_for_next_job()
        request = {"id": run_id, "api_key": key, "expires": int(time.time())+(15 if align_trend else 600), "pricing": PRICING}
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(request, output)
        temporary.chmod(0o600)
        os.chown(temporary, account.pw_uid, account.pw_gid)
        temporary.replace(request_path)
        del key, request
        print("One bounded model request queued; observing collection and resources...", flush=True)
        deadline = time.monotonic()+420
        consumed_without_ledger_since = None
        next_service_check = 0
        while time.monotonic() < deadline:
            monitor.sample()
            with closing(readonly(DATA / "finevent.db")) as conn:
                row = conn.execute("SELECT * FROM ai_probe_runs WHERE id=?", (run_id,)).fetchone()
                timing = conn.execute("SELECT * FROM ai_probe_timings WHERE id=?", (run_id,)).fetchone()
                if row and row["completed"] and timing and timing['collector_completed_ms']:
                    result = dict(row)
                    result['timing'] = dict(timing)
                    overlapping = conn.execute("SELECT source,started,completed,success FROM polls WHERE started<=? AND completed>=?", (row["completed"], row["started"])).fetchall()
                    result["overlapping_polls"] = [dict(p) for p in overlapping]
                    resources = [dict(r) for r in conn.execute("SELECT * FROM resources WHERE at BETWEEN ? AND ? ORDER BY at", (row["started"]-1, row["completed"]+2))]
                    result["resources"] = resource_stats(resources, row["started"], row["completed"])
                    result["booked_cny_upper_bound"] = (row["cost_upper_nano"] if row["cost_upper_nano"] is not None else row["reserved_nano"])/1000000000
                    if align_trend:
                        monitor.follow_triggered_job()
                    monitor.finish(result)
                    return result
                if row and int(time.time())-row["started"] > 90:
                    raise RuntimeError("AI request did not finish after reservation; retain the reserved charge and inspect the service")
                if row is None and not request_path.exists():
                    consumed_without_ledger_since = consumed_without_ledger_since or time.monotonic()
                    if time.monotonic()-consumed_without_ledger_since > 15:
                        raise RuntimeError("AI request was consumed without a reservation; check request validity and monthly budget")
            if time.monotonic() >= next_service_check:
                if command("systemctl", "is-active", "--quiet", "finevent.service", check=False).returncode != 0:
                    raise RuntimeError("Service stopped during AI probe")
                next_service_check = time.monotonic()+30
            time.sleep(0.1 if align_trend else 1)
        raise RuntimeError("AI probe did not finish in seven minutes; inspect its ledger before retrying")
    finally:
        temporary.unlink(missing_ok=True)
        request_path.unlink(missing_ok=True)


def production_roundtrip(deployment_backup, directory):
    """Exercise the real rollback with a fresh frozen DB, then return to the verified new release."""
    original_state = json.loads((deployment_backup / "state.json").read_text())
    previous = Path(original_state["previous"]).resolve() if original_state.get("previous") else None
    current = (ROOT / "current").resolve()
    releases = (ROOT / "releases").resolve()
    if previous is None or not previous.is_relative_to(releases) or not current.is_relative_to(releases):
        raise RuntimeError("Managed previous and current releases are required for rehearsal")
    installer.verify_bundle(previous)
    # Probe tables are additive; pre-existing schema/data remain compatible with prior releases.
    with closing(readonly(DATA / "finevent.db")) as conn:
        if [r[0] for r in conn.execute("SELECT version FROM schema_version")] != [1]:
            raise RuntimeError("Rollback rehearsal supports only verification schema version 1")
    rehearsal = directory / "rollback-rehearsal"
    rehearsal.mkdir(mode=0o700)
    (rehearsal / "state.json").write_text(json.dumps(original_state))
    timer_was_active = command("systemctl", "is-active", "--quiet", "finevent-backup.timer", check=False).returncode == 0
    snapshot_ok = False
    try:
        command("systemctl", "stop", "finevent-backup.timer", "finevent.service")
        snapshot(DATA / "finevent.db", rehearsal / "database.db")
        snapshot_ok = True
    finally:
        # Even a failed snapshot must not leave the current service stopped.
        if not snapshot_ok:
            command("systemctl", "start", "finevent.service")
            if timer_was_active:
                command("systemctl", "start", "finevent-backup.timer")
    rollback_result = None
    try:
        rollback_result = installer.rollback(rehearsal)
        installer.wait_for_https(rehearsal)
        command("systemctl", "is-active", "--quiet", "finevent.service", "finevent-backup.timer")
    finally:
        # A fresh install backs up the current DB before returning to the new program, preserving new rows and AI charges.
        print("Returning to the new release after the rollback rehearsal...", flush=True)
        installer.install()
    return {"status": "passed", "previous_release": str(previous), "restored": rollback_result,
            "final_release": str((ROOT / "current").resolve()), "timer_active": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--with-ai", action="store_true")
    parser.add_argument("--resource-trial-id")
    parser.add_argument("--overlap-after", help="Reviewed verification ID; align one request and reuse its completed rollback evidence")
    args = parser.parse_args()
    import re
    if not re.fullmatch(r"verification-\d{8}T\d{6}Z-[a-f0-9]{8}", args.report_id):
        raise RuntimeError("Invalid report ID")
    if args.with_ai and not args.resource_trial_id:
        raise RuntimeError("Real AI verification requires the reviewed resource trial ID")
    if args.overlap_after and (not args.with_ai or not resource_gate.VERIFICATION_ID.fullmatch(args.overlap_after)):
        raise RuntimeError("Overlap continuation requires --with-ai and a valid preceding verification ID")
    if os.geteuid() != 0 or not 0 <= (args.end-args.start).days <= 6 or args.end > datetime.now(ZONE).date():
        raise RuntimeError("Root and a historical date range of at most seven days are required")
    os.umask(0o077)
    signal.signal(signal.SIGHUP, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    directory = BACKUPS / args.report_id
    directory.mkdir(mode=0o700)
    report_path = directory / "report.json"
    report = {"id": args.report_id, "created_at": datetime.now(ZONE).isoformat(), "status": "running", "stage": "history"}
    save_report(report_path, report)
    lock = None
    try:
        import fcntl
        lock = open('/run/lock/finevent-memory-recovery.lock', 'a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print("1/4 Reading historical evidence before any service change...", flush=True)
        snapshot(DATA / "finevent.db", directory / "before.db")
        days = [args.start+timedelta(days=i) for i in range((args.end-args.start).days+1)]
        report["history"] = audit_database(directory / "before.db", days)
        report["system_before"] = system_evidence(args.start)
        save_report(report_path, report)
        resources = report["history"]["resources_overall"]
        trial = None
        if args.resource_trial_id:
            report["resource_admission"] = {}
            trial = resource_gate.admit(args.resource_trial_id, directory / "before.db",
                                        report["resource_admission"], readonly, resource_stats, BACKUPS, args.overlap_after, ROOT)
        elif (resources["oom_counter_max"] not in (None, 0)
                or (resources["host_available_min_mib"] is not None and resources["host_available_min_mib"] < 64)):
            raise RuntimeError("Historical memory pressure requires review before deployment or model load; history report saved")
        print("2/4 Installing the verified probe release (with automatic deployment rollback)...", flush=True)
        report["stage"] = "deployment"
        save_report(report_path, report)
        deployment_backup = installer.install()
        report["deployment_backup"] = str(deployment_backup)
        save_report(report_path, report)
        print("3/4 Real-model coexistence probe...", flush=True)
        report["stage"] = "ai_probe"
        save_report(report_path, report)
        if args.with_ai:
            report["ai_observation"] = {}
            report["ai"] = run_ai_probe(args.report_id, trial, report["ai_observation"], bool(args.overlap_after))
        else:
            report["ai"] = {"status": "not_requested"}
        save_report(report_path, report)
        if args.overlap_after:
            print("4/4 Retaining the preceding passed rollback evidence; no repeat rehearsal.", flush=True)
            report["rollback"] = {"status": "previously_passed", "report_id": args.overlap_after}
        else:
            print("4/4 Production rollback and return, preserving a fresh database snapshot...", flush=True)
            report["stage"] = "rollback_rehearsal"
            save_report(report_path, report)
            report["rollback"] = production_roundtrip(deployment_backup, directory)
        report["system_after"] = system_evidence(args.start)
        if trial:
            _, _, report["resources_after"] = resource_gate.live_check(trial, minimum_mib=64)
        report["status"] = "completed_for_review"
        report["stage"] = "review"
    except BaseException as error:
        report["status"] = "incomplete"
        report["error_type"] = type(error).__name__
        # Our own error text has no credentials; provider responses and request bodies are never persisted.
        report["error"] = str(error)[:1000] if isinstance(error, RuntimeError) else "Verification interrupted or command failed; inspect service state before retrying."
        raise
    finally:
        if "system_after" not in report:
            try:
                report["system_after"] = system_evidence(args.start)
            except Exception as failure:
                report["system_after_error"] = type(failure).__name__
        report["finished_at"] = datetime.now(ZONE).isoformat()
        save_report(report_path, report)
        if lock:
            lock.close()
        print("Evidence: " + str(report_path), flush=True)


if __name__ == "__main__":
    main()
