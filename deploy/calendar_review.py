"""Explicit, checksum-bound acceptance of reviewed source recovery, never resource failures."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re


def manifest():
    return json.loads(Path(__file__).with_name('calendar-reviews.json').read_text(encoding='utf-8'))


def report_sha(report):
    # Status downloads append worker state; the underlying completed report is unchanged.
    original = {key: value for key, value in report.items() if key != 'observer_status'}
    return hashlib.sha256(json.dumps(original, ensure_ascii=False, indent=2).encode()).hexdigest()


def followup_accepted(entry, review, started, finished, inspected):
    scope = entry.get('followup_scope', 'all_after_observation')
    if scope == 'all_after_observation':
        return review.get('followup_cycles', 0) >= 2 and review.get('followup_source_failures') == []
    if scope != 'first_two_after_observation':
        return False

    # Opt-in per reviewed release; older approvals keep their original scope.
    expected = entry.get('expected_sources', [])
    if (len(expected) != 9 or not all(isinstance(s, str) and s for s in expected)
            or len(set(expected)) != 9 or set(review.get('source_latest', {})) != set(expected)):
        return False
    cycles = review.get('cycles', [])
    if not cycles or any(not isinstance(c, dict) or type(c.get('at')) is not int
                         or not started <= c['at'] <= inspected for c in cycles):
        return False
    cycles = sorted(cycles, key=lambda c: c['at'])
    if len({c['at'] for c in cycles}) != len(cycles):
        return False
    observed = [c for c in cycles if c['at'] <= finished]
    followup = [c for c in cycles if c['at'] > finished]
    if (len(observed) != review.get('observation_cycles')
            or len(followup) != review.get('followup_cycles') or len(followup) < 2):
        return False
    window = followup[:2]  # Never skip a failed or incomplete cycle to find later successes.
    for cycle in observed + window:
        sources = cycle.get('sources', [])
        if (len(sources) != len(expected) or any(not isinstance(s, dict) for s in sources)
                or {s.get('platform_id') for s in sources} != set(expected)
                or any(s.get('status') not in ('success', 'failed') for s in sources)):
            return False
    # Later failures stay in the checksum-bound diagnostic and operational tracking.
    return all(s['status'] == 'success' for c in window for s in c['sources'])


def accepted(report, raw):
    if not raw:
        return False
    entry = manifest().get(report.get('id'), {})
    if (entry.get('decision') != 'accepted_with_recovered_source_failures'
            or report_sha(report) != entry.get('report_sha256')
            or hashlib.sha256(raw).hexdigest() != entry.get('review_sha256')):
        return False
    review = json.loads(raw)
    failures = review.get('observation_source_failures', [])
    latest = review.get('source_latest', {})
    trend = report.get('trendradar', {})
    started = datetime.fromisoformat(report['observation_started']).timestamp()
    finished = datetime.fromisoformat(report['finished_at']).timestamp()
    inspected = datetime.fromisoformat(review['inspected_at']).timestamp()
    return (review.get('id') == entry.get('review_id') and review.get('release_id') == report['id']
            and review.get('original_report_sha256') == entry['report_sha256']
            and review.get('status') == 'completed_for_review' and not review.get('failure')
            and review.get('active_release') == review.get('observed_release') == report['deployment']['release']
            and review.get('missing_databases') == [] and review.get('original_counts_match') is True
            and review.get('observation_cycles') == trend.get('cycles')
            and review.get('observation_cycles_with_incomplete_sources') == []
            and 0 < len(failures) == trend.get('source_failures')
            and all(f.get('latest_success') is True and (f.get('first_later_success') or {}).get('status') == 'success'
                    and 0 < f['first_later_success']['at']-f['at'] <= 1800 for f in failures)
            and followup_accepted(entry, review, started, finished, inspected)
            and len(latest) == 9 and all(s.get('status') == 'success' and finished < s.get('at', 0) <= inspected
                                       and inspected-s['at'] <= 3600 for s in latest.values())
            and trend.get('polls', 0) > 0 and trend.get('poll_failures') == 0
            and 0 <= finished-trend.get('last_import', 0) <= 180
            and trend.get('html_mtime', 0) >= max((c['at'] for c in review.get('cycles', [])
                                                 if c['at'] <= finished), default=finished))


def load(report, backups):
    entry = manifest().get(report.get('id'))
    if entry is None:
        return None
    review_id = entry['review_id']
    if not re.fullmatch(r'calendar-observation-review-\d{8}T\d{6}Z-[a-f0-9]{8}', review_id):
        raise RuntimeError('Invalid source review identity')
    raw = (backups/review_id/'report.json').read_bytes()
    if not accepted(report, raw):
        raise RuntimeError('Reviewed source evidence no longer matches the accepted records')
    return raw
