package calendar

import (
	"crypto/sha256"
	"database/sql"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
)

//go:embed snapshots/bls.json snapshots/bls-*.tsv
var blsSchedules embed.FS

type blsRelease struct {
	name, period, nyfedID string
	event                 Event
}

const productivitySeries = "Productivity and Costs"
const preliminaryProductivity = productivitySeries + " (P)"
const revisedProductivity = productivitySeries + " (R)"

// An NYFed release has no reference period in its title. Only these exact
// national series can be compared with the BLS schedule.
var blsNYFedSeries = map[string]string{
	"Employment Situation":                   "employment situation",
	"Consumer Price Index":                   "consumer price index",
	"Producer Price Index":                   "producer price index",
	"Job Openings and Labor Turnover Survey": "jolts",
	"U.S. Import and Export Price Indexes":   "imports and exports",
	"Employment Cost Index":                  "employment cost index",
	preliminaryProductivity:                  "productivity & costs (preliminary)",
	revisedProductivity:                      "productivity & costs (revised)",
}

const nationalBenchmark = "Current Employment Statistics Preliminary Benchmark (National)"
const dolBenchmarkNotice = "https://www.dol.gov/newsroom/economicdata/empsit_08072026.pdf"
const finalBenchmarkNote = "本期同时公布 2026 年 3 月全国非农就业年度基准最终修订。"

var dolReportURL = regexp.MustCompile(`^https://www\.dol\.gov/newsroom/economicdata/empsit_(\d{8})\.pdf$`)

func blsSnapshotFile(info SnapshotInfo) (string, error) {
	if info.URL == fmt.Sprintf("https://www.bls.gov/schedule/%d/home.htm", info.Year) {
		return fmt.Sprintf("snapshots/bls-%d.tsv", info.Year), nil
	}
	if m := dolReportURL.FindStringSubmatch(info.URL); m != nil {
		return "snapshots/bls-notice-" + m[1] + ".tsv", nil
	}
	return "", errors.New("BLS 快照的官方来源无效")
}

func readBLSReleases(info SnapshotInfo, body []byte, now int64) ([]blsRelease, error) {
	file, err := blsSnapshotFile(info)
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256(body)
	digest := hex.EncodeToString(sum[:])
	idPrefix := strings.TrimSuffix(strings.TrimPrefix(file, "snapshots/"), ".tsv")
	if len(body) > 2*1024*1024 || info.SHA256 != digest || info.ID != idPrefix+"-"+digest[:12] ||
		info.Format != "release-table" ||
		info.RetrievedAt <= 0 || info.RetrievedAt > info.ReviewedAt || info.ReviewedAt > now {
		return nil, errors.New("BLS 快照身份或核对记录无效")
	}
	lines := strings.Split(strings.TrimSuffix(string(body), "\n"), "\n")
	if lines[0] != "date_et\ttime_et\trelease\treference_period" || len(lines)-1 != info.Entries || info.Entries < 1 {
		return nil, errors.New("BLS 快照表头或条数不符")
	}
	var releases []blsRelease
	seen := make(map[string]bool)
	for _, line := range lines[1:] {
		cols := strings.Split(line, "\t")
		if len(cols) != 4 || cols[2] == "" || cols[3] == "" {
			return nil, errors.New("BLS 快照缺少发布项目或统计期")
		}
		day, clock, name, period := cols[0], cols[1], cols[2], cols[3]
		at, err := localTime(day, clock, easternZone)
		if err != nil || !strings.HasPrefix(day, fmt.Sprintf("%d-", info.Year)) {
			return nil, fmt.Errorf("BLS 快照时刻无效：%s %s", day, clock)
		}
		fullTitle := name + " for " + period
		if seen[fullTitle] {
			return nil, fmt.Errorf("BLS 快照项目与统计期重复：%s", fullTitle)
		}
		seen[fullTitle] = true
		e := event("bls", "schedule:"+Key(fullTitle), fullTitle, info.URL, easternZone)
		e.Start, e.End, e.Precision = at, at, "minute"
		e.Evidence = fullTitle + " · " + day + " " + clock + "（美国东部时间）"
		e.Provenance = &Provenance{Mode: "snapshot", Format: info.Format, SnapshotID: info.ID, SHA256: info.SHA256, RetrievedAt: info.RetrievedAt, ReviewedAt: info.ReviewedAt}
		indicator, title := nyfedIndicator(blsNYFedSeries[name])
		nyfedID := ""
		if indicator != "" {
			e.Title = title
			nyfedID = day[:7] + ":" + indicator
		}
		switch name {
		case nationalBenchmark:
			e.Title = "美国非农就业年度基准初步修订（全国）"
		case "Current Employment Statistics Preliminary Benchmark (State and Area)":
			e.Title = "美国就业年度基准初步修订（州及地区）"
		}
		// The July 2026 report explicitly ties this final benchmark to the
		// January 2027 jobs report, regardless of its eventual publication day.
		if name == "Employment Situation" && period == "January 2027" {
			e.Conditions = finalBenchmarkNote
			e.Provenance.AuthorityURL = dolBenchmarkNotice
		}
		if name == nationalBenchmark && period == "March 2026" && day == "2026-08-28" && clock == "10:00" {
			e.Provenance.AuthorityURL = dolBenchmarkNotice
		}
		releases = append(releases, blsRelease{name, period, nyfedID, e})
	}
	return releases, nil
}

func (s *Store) importBundledBLS(now int64) error {
	data, err := blsSchedules.ReadFile("snapshots/bls.json")
	if err != nil {
		return err
	}
	var manifest []SnapshotInfo
	if err := json.Unmarshal(data, &manifest); err != nil {
		return err
	}
	tx, err := s.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, info := range manifest {
		file, err := blsSnapshotFile(info)
		if err != nil {
			return err
		}
		body, err := blsSchedules.ReadFile(file)
		if err != nil {
			return err
		}
		if err := importBLSTable(tx, info, body, now); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func importBLSTable(tx *sql.Tx, info SnapshotInfo, body []byte, now int64) error {
	releases, err := readBLSReleases(info, body, now)
	if err != nil {
		return err
	}
	var exists bool
	if err := tx.QueryRow(`SELECT EXISTS(SELECT 1 FROM calendar_snapshots WHERE id=?)`, info.ID).Scan(&exists); err != nil || exists {
		return err
	}
	counts := make(map[string]int)
	for _, r := range releases {
		counts[r.nyfedID]++
	}
	for _, r := range releases {
		saved, err := importBLSRelease(tx, r, counts[r.nyfedID] == 1, now)
		if err != nil {
			return fmt.Errorf("%s for %s: %w", r.name, r.period, err)
		}
		if saved {
			info.ImportedEntries++
		}
		_, end := r.event.Days()
		if end > info.CoverageEnd {
			info.CoverageEnd = end
		}
	}
	info.ImportedAt = now
	raw, err := json.Marshal(info)
	if err != nil {
		return err
	}
	_, err = tx.Exec(`INSERT INTO calendar_snapshots VALUES(?,'bls',?,?)`, info.ID, string(raw), body)
	return err
}

func importBLSRelease(tx *sql.Tx, r blsRelease, uniqueMonth bool, now int64) (bool, error) {
	e := r.event
	// Prefer the already enrolled period. For a database predating snapshots,
	// adopt the official row or an identifiable NYFed row without changing its ID.
	var id, previousNYFedID string
	err := tx.QueryRow(`SELECT event_id,nyfed_id FROM calendar_bls_releases WHERE name=? AND period=?`, r.name, r.period).Scan(&id, &previousNYFedID)
	if err == sql.ErrNoRows {
		full := r.name + " for " + r.period
		id, err = oneBLSMatch(tx.Query(`SELECT id FROM calendar_events WHERE source='bls' AND
			(lower(trim(json_extract(payload,'$.title')))=lower(?) OR (lower(trim(json_extract(payload,'$.title')))=lower(?) AND
			(instr(lower(json_extract(payload,'$.evidence')),lower(?))>0 OR
			(instr(lower(json_extract(payload,'$.evidence')),lower(?||' for '))=0 AND start_day=?))))`, full, r.name, full, r.name, e.Start[:10]))
	}
	if err == sql.ErrNoRows && (r.name == preliminaryProductivity || r.name == revisedProductivity) {
		// An ICS imported before this table may omit the preliminary/revised suffix.
		id, err = oneBLSMatch(tx.Query(`SELECT id FROM calendar_events WHERE source='bls'
			AND trim(json_extract(payload,'$.title'))=? COLLATE NOCASE AND json_extract(payload,'$.precision')='minute'
			AND instr(lower(json_extract(payload,'$.evidence')),lower(?||' for '))=0
			AND unixepoch(json_extract(payload,'$.start'))=unixepoch(?)`, productivitySeries, productivitySeries, e.Start))
	}
	if err == sql.ErrNoRows && r.nyfedID != "" {
		err = tx.QueryRow(`SELECT id FROM calendar_events WHERE source='nyfed' AND source_id=? AND (? OR start_day=?)`, r.nyfedID, uniqueMonth, e.Start[:10]).Scan(&id)
	}
	if err == sql.ErrNoRows {
		// A manually confirmed official release can already have watches. Match
		// the official document and exact project/period or title and instant.
		id, err = oneBLSMatch(tx.Query(`SELECT id FROM calendar_events WHERE source='manual' AND json_extract(payload,'$.kind')='macro'
			AND (json_extract(payload,'$.url') LIKE 'https://www.bls.gov/%' OR json_extract(payload,'$.url') LIKE 'https://www.dol.gov/newsroom/economicdata/%')
			AND (lower(json_extract(payload,'$.title'))=lower(?) OR
			(json_extract(payload,'$.title')=? AND unixepoch(json_extract(payload,'$.start'))=unixepoch(?)))`,
			r.name+" for "+r.period, e.Title, e.Start))
	}
	if err != nil && err != sql.ErrNoRows {
		return false, err
	}
	if id == "" {
		id = Key("bls:" + e.SourceID)
	}
	old, err := get(tx, id)
	if err != nil && err != sql.ErrNoRows {
		return false, err
	}
	if old.Provenance != nil && old.Provenance.Mode == "snapshot" && old.Provenance.ReviewedAt >= e.Provenance.ReviewedAt {
		return false, nil
	}
	// Live BLS data, including cancellations, always retain precedence.
	saveSnapshot := old.ID == "" || (old.Source == "nyfed" && dolReportURL.MatchString(e.URL)) ||
		(old.Source == "manual" && old.Status != "cancelled") || (old.Provenance != nil && old.Provenance.Mode == "snapshot")
	if old.Source == "nyfed" {
		if err := rememberBLSAlias(tx, old.Source, old.SourceID, id); err != nil {
			return false, err
		}
	}
	if previousNYFedID != "" && previousNYFedID != r.nyfedID {
		// A stale NYFed calendar can still list the original release month.
		// Keep that binding only when the original month had one possible period.
		var count int
		if err := tx.QueryRow(`SELECT count(*) FROM calendar_bls_releases WHERE nyfed_id=?`, previousNYFedID).Scan(&count); err != nil {
			return false, err
		}
		if count == 1 {
			if err := rememberBLSAlias(tx, "nyfed", previousNYFedID, id); err != nil {
				return false, err
			}
		}
	}
	if saveSnapshot {
		if old.Source == "manual" {
			e.Conditions = strings.TrimSpace(old.Conditions + " " + e.Conditions)
			e.Importance = old.Importance
			e.Status = old.Status
		}
		if _, err := saveAs(tx, e, id, -1, now); err != nil {
			return false, err
		}
	} else if e.Conditions == finalBenchmarkNote && !strings.Contains(old.Conditions, finalBenchmarkNote) {
		old.Conditions = strings.TrimSpace(old.Conditions + " " + finalBenchmarkNote)
		if _, err := saveAs(tx, old, id, -1, now); err != nil {
			return false, err
		}
	}
	_, err = tx.Exec(`INSERT INTO calendar_bls_releases(name,period,event_id,nyfed_id,release_day) VALUES(?,?,?,?,?)
		ON CONFLICT(name,period) DO UPDATE SET nyfed_id=excluded.nyfed_id,release_day=excluded.release_day`,
		r.name, r.period, id, r.nyfedID, e.Start[:10])
	return saveSnapshot, err
}

func rememberBLSAlias(tx *sql.Tx, source, sourceID, id string) error {
	_, err := tx.Exec(`INSERT INTO calendar_bls_aliases(source,source_id,event_id) VALUES(?,?,?) ON CONFLICT(source,source_id) DO NOTHING`, source, sourceID, id)
	return err
}

func oneBLSMatch(rows *sql.Rows, err error) (string, error) {
	if err != nil {
		return "", err
	}
	defer rows.Close()
	var id string
	for rows.Next() {
		if id != "" {
			return "", errAmbiguousRelease
		}
		if err := rows.Scan(&id); err != nil {
			return "", err
		}
	}
	if err := rows.Err(); err != nil {
		return "", err
	}
	if id == "" {
		return "", sql.ErrNoRows
	}
	return id, nil
}

func saveLiveBLSRelease(tx *sql.Tx, e Event, now int64) error {
	if err := e.Validate(); err != nil {
		return err
	}
	if e.Source == "nyfed" {
		// Its indicator/month ID cannot distinguish two periods of the same series.
		// Keep their independently identified official releases in this case.
		var count int
		if err := tx.QueryRow(`SELECT count(*) FROM calendar_bls_releases WHERE nyfed_id=?`, e.SourceID).Scan(&count); err != nil || count > 1 {
			return err
		}
	}
	id, err := matchBLSRelease(tx, e)
	if err != nil {
		return err
	}
	if id == "" {
		_, err := save(tx, e, -1, now)
		return err
	}
	if err := rememberBLSAlias(tx, e.Source, e.SourceID, id); err != nil {
		return err
	}
	old, err := get(tx, id)
	if err != nil {
		return err
	}
	if olderICS(old, e) || (e.Source == "nyfed" && (old.Status == "cancelled" || (old.Source == "bls" && (old.Provenance == nil || old.Provenance.Mode == "upload" || dolReportURL.MatchString(old.URL))))) {
		return nil
	}
	if err := includeBenchmarkNote(tx, id, &e); err != nil {
		return err
	}
	_, err = saveAs(tx, e, id, -1, now)
	return err
}

func matchBLSRelease(tx *sql.Tx, e Event) (string, error) {
	var id string
	err := tx.QueryRow(`SELECT event_id FROM calendar_bls_aliases WHERE source=? AND source_id=?`, e.Source, e.SourceID).Scan(&id)
	if err == sql.ErrNoRows && e.Source == "bls" {
		// A previously saved ICS UID remains authoritative even when its date or title changes.
		err = tx.QueryRow(`SELECT id FROM calendar_events WHERE source='bls' AND source_id=?`, e.SourceID).Scan(&id)
	}
	if err == sql.ErrNoRows {
		if e.Source == "nyfed" {
			id, err = oneBLSMatch(tx.Query(`SELECT event_id FROM calendar_bls_releases WHERE nyfed_id=? AND release_day=?`, e.SourceID, e.Start[:10]))
			if err == sql.ErrNoRows {
				id, err = oneBLSMatch(tx.Query(`SELECT event_id FROM calendar_bls_releases WHERE nyfed_id=?`, e.SourceID))
			}
		} else {
			id, err = oneBLSMatch(tx.Query(`SELECT event_id FROM calendar_bls_releases WHERE lower(name||' for '||period)=lower(?) OR
				(lower(name)=lower(?) AND instr(lower(?),lower(name||' for '||period))>0)`, e.Title, e.Title, e.Evidence))
			if err == sql.ErrNoRows && !strings.Contains(strings.ToLower(e.Evidence), strings.ToLower(e.Title)+" for ") {
				id, err = oneBLSMatch(tx.Query(`SELECT event_id FROM calendar_bls_releases WHERE lower(name)=lower(?) AND release_day=?`, e.Title, e.Start[:10]))
				if err == sql.ErrNoRows && strings.EqualFold(e.Title, productivitySeries) && e.Precision == "minute" {
					// The ICS omits the stage. Only a unique release at the same instant can identify it.
					id, err = oneBLSMatch(tx.Query(`SELECT r.event_id FROM calendar_bls_releases r JOIN calendar_events c ON c.id=r.event_id
						WHERE r.name IN (?,?) AND json_extract(c.payload,'$.precision')='minute'
						AND unixepoch(json_extract(c.payload,'$.start'))=unixepoch(?)`, preliminaryProductivity, revisedProductivity, e.Start))
				} else if err == sql.ErrNoRows && (e.Evidence == e.Title || strings.HasPrefix(e.Evidence, e.Title+"; DTSTART")) {
					id, err = oneBLSMatch(tx.Query(`SELECT event_id FROM calendar_bls_releases WHERE lower(name)=lower(?) AND substr(release_day,1,7)=?`, e.Title, e.Start[:7]))
				}
			}
		}
	}
	if err != nil && err != sql.ErrNoRows {
		return "", err
	}
	return id, nil
}

func includeBenchmarkNote(tx *sql.Tx, id string, e *Event) error {
	var finalBenchmark bool
	if err := tx.QueryRow(`SELECT EXISTS(SELECT 1 FROM calendar_bls_releases WHERE event_id=? AND name='Employment Situation' AND period='January 2027')`, id).Scan(&finalBenchmark); err != nil {
		return err
	}
	if finalBenchmark {
		e.Conditions = strings.TrimSpace(e.Conditions + " " + finalBenchmarkNote)
	}
	return nil
}
