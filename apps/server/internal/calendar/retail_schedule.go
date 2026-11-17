package calendar

import (
	"database/sql"
	"errors"
	"strings"
	"time"
)

// Prefer an explicit statistical period for delayed or multiple releases.
// A NYFed month with multiple undated periods cannot establish their order.
func nyfedRetailPeriod(tx *sql.Tx, day string, monthCount int) (string, error) {
	var count int
	var period string
	for _, source := range []string{"census", "omb"} {
		err := tx.QueryRow(`SELECT count(*), coalesce(min(period),'') FROM (
			SELECT period FROM calendar_retail_schedule WHERE source=? AND release_day=?
			UNION SELECT substr(source_id,?) FROM calendar_events WHERE source=? AND source_id LIKE ? AND start_day=?
		)`, source, day, len(retailPrefix)+1, source, retailPrefix+"%", day).Scan(&count, &period)
		if err != nil || count > 1 {
			return "", err
		}
		if count == 1 {
			return period, nil
		}
	}
	if monthCount != 1 {
		return "", nil
	}
	date, err := time.Parse(time.DateOnly, day)
	if err != nil {
		return "", err
	}
	first := date.AddDate(0, 0, 1-date.Day())
	period = first.AddDate(0, -1, 0).Format("2006-01")
	// A publisher-confirmed shift across months makes the usual previous-month
	// assumption unsafe. Wait for an explicit match instead of creating a duplicate.
	err = tx.QueryRow(`SELECT count(*) FROM calendar_events WHERE source='census'
		AND source_id LIKE 'advance-retail:%' AND ((substr(start_day,1,7)=? AND source_id!=?)
		OR (source_id=? AND substr(start_day,1,7)!=?))`, day[:7], retailPrefix+period, retailPrefix+period, day[:7]).Scan(&count)
	if count != 0 {
		return "", nil
	}
	return period, err
}

func saveRetail(tx *sql.Tx, e Event, months map[string]int, now int64) (bool, error) {
	if err := e.Validate(); err != nil {
		return false, err
	}
	day := e.Start[:10]
	period := strings.TrimPrefix(e.SourceID, retailPrefix)
	if e.Source == "nyfed" {
		var err error
		period, err = nyfedRetailPeriod(tx, day, months[day[:7]])
		if err != nil || period == "" {
			return false, err
		}
		e.SourceID = retailPrefix + period
	}
	if _, err := time.Parse("2006-01", period); err != nil {
		return false, errors.New("零售销售统计月份无效")
	}
	if _, err := tx.Exec("INSERT INTO calendar_retail_schedule(source,period,release_day) VALUES(?,?,?)", e.Source, period, day); err != nil {
		return false, err
	}
	id := Key("census:" + e.SourceID) // Preserve pre-upgrade IDs and their watches.
	old, err := get(tx, id)
	if err != nil && err != sql.ErrNoRows {
		return false, err
	}
	if err == nil {
		if e.Source == "omb" && old.Precision == "minute" {
			return true, nil
		}
		if e.Source == "nyfed" && old.Source == "census" {
			// NYFed does not identify the reference month. Its stale monthly row
			// must not undo a publisher-confirmed move across months.
			if old.Start[:7] != day[:7] || old.Start == e.Start {
				return true, nil
			}
			var available bool
			err = tx.QueryRow(`SELECT EXISTS(SELECT 1 FROM calendar_sources WHERE id='census' AND error='' AND last_success>0)`).Scan(&available)
			if err != nil || available {
				return true, err
			}
		}
	}
	_, err = saveAs(tx, e, id, -1, now)
	return true, err
}
