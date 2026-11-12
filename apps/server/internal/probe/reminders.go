package probe

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
	"time"

	"finevent/internal/calendar"
)

type ReminderSettings struct {
	Enabled      bool     `json:"enabled"`
	Devices      []int64  `json:"devices"`
	Kinds        []string `json:"kinds"`
	FundCodes    []string `json:"fund_codes"`
	EarlyHours   int      `json:"early_hours"`
	LateMinutes  int      `json:"late_minutes"`
	DatePrevious bool     `json:"date_previous"`
	DateSame     bool     `json:"date_same"`
	FundEnd      bool     `json:"fund_end"`
}

func defaultReminders() ReminderSettings {
	return ReminderSettings{Devices: []int64{}, Kinds: []string{}, FundCodes: []string{}, EarlyHours: 24, LateMinutes: 30, DatePrevious: true, DateSame: true, FundEnd: true}
}

func (p *ReminderSettings) normalize() {
	if p.Devices == nil {
		p.Devices = []int64{}
	}
	if p.Kinds == nil {
		p.Kinds = []string{}
	}
	if p.FundCodes == nil {
		p.FundCodes = []string{}
	}
}

func initReminders(db *sql.DB) error {
	_, err := db.Exec(`CREATE TABLE IF NOT EXISTS calendar_reminder_settings(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calendar_reminder_watches(event_id TEXT PRIMARY KEY REFERENCES calendar_events(id),enabled INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS calendar_reminder_jobs(key TEXT PRIMARY KEY,delivery_id INTEGER UNIQUE NOT NULL REFERENCES deliveries(id) ON DELETE CASCADE,event_id TEXT NOT NULL REFERENCES calendar_events(id),anchor TEXT NOT NULL,scheduled_at INTEGER NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS calendar_reminder_event ON calendar_reminder_jobs(event_id,scheduled_at);
CREATE INDEX IF NOT EXISTS deliveries_due ON deliveries(status,due);`)
	return err
}

type reminderReader interface{ QueryRow(string, ...any) *sql.Row }

func reminderSettings(db reminderReader) (ReminderSettings, error) {
	p := defaultReminders()
	var raw string
	err := db.QueryRow(`SELECT payload FROM calendar_reminder_settings WHERE id=1`).Scan(&raw)
	if err == sql.ErrNoRows {
		return p, nil
	}
	if err != nil {
		return p, err
	}
	err = json.Unmarshal([]byte(raw), &p)
	p.normalize()
	return p, err
}
func (p ReminderSettings) validate(db reminderReader) error {
	if p.EarlyHours < 0 || p.EarlyHours > 168 || p.LateMinutes < 0 || p.LateMinutes > 1440 || len(p.Devices) > 20 || len(p.FundCodes) > 100 || len(p.Kinds) > 3 {
		return errors.New("提醒时间或关注数量超出范围")
	}
	for _, kind := range p.Kinds {
		if !slices.Contains([]string{"macro", "monetary", "other"}, kind) {
			return errors.New("未知日历类别")
		}
	}
	for _, code := range p.FundCodes {
		if len(code) != 6 || strings.Trim(code, "0123456789") != "" {
			return errors.New("基金代码须为六位数字")
		}
	}
	seen := map[int64]bool{}
	for _, id := range p.Devices {
		var active int
		if seen[id] || db.QueryRow(`SELECT active FROM subscriptions WHERE id=?`, id).Scan(&active) != nil || (p.Enabled && active != 1) {
			return errors.New("请重新选择有效接收设备")
		}
		seen[id] = true
	}
	if p.Enabled && len(p.Devices) == 0 {
		return errors.New("开启自动提醒前，请选择接收设备")
	}
	return nil
}

func reminderFollow(db reminderReader, p ReminderSettings, e calendar.Event) (bool, *bool, error) {
	follow := slices.Contains(p.Kinds, e.Kind) || (e.Kind == "fund" && slices.Contains(p.FundCodes, e.FundCode))
	var value bool
	err := db.QueryRow(`SELECT enabled FROM calendar_reminder_watches WHERE event_id=?`, e.ID).Scan(&value)
	if err == nil {
		return value, &value, nil
	}
	if err == sql.ErrNoRows {
		return follow, nil, nil
	}
	return false, nil, err
}

type reminderSlot struct {
	Anchor  string
	Due     int64
	Expires int64
	Body    string
}

func reminderSlots(e calendar.Event, p ReminderSettings) []reminderSlot {
	if e.Status == "cancelled" {
		return nil
	}
	loc, _ := time.LoadLocation("Asia/Shanghai")
	var slots []reminderSlot
	seen := map[string]bool{}
	add := func(anchor string, due time.Time, body string) {
		key := fmt.Sprintf("%s:%d", anchor, due.Unix())
		if !seen[key] {
			slots = append(slots, reminderSlot{anchor, due.Unix(), due.Add(10 * time.Minute).Unix(), body})
			seen[key] = true
		}
	}
	if e.Precision == "minute" {
		start, err := time.Parse(time.RFC3339, e.Start)
		if err != nil {
			return nil
		}
		for _, minutes := range []int{p.EarlyHours * 60, p.LateMinutes} {
			if minutes > 0 {
				add("start:"+start.UTC().Format(time.RFC3339), start.Add(-time.Duration(minutes)*time.Minute), "计划时间："+start.In(loc).Format("01月02日 15:04")+"（北京时间）。以最新原文为准。")
			}
		}
		for i := range slots {
			slots[i].Expires = min(slots[i].Expires, start.Unix())
		}
	} else if e.Precision == "date" {
		anchors := []struct{ day, label string }{{e.Start, "日程开始"}}
		if e.Kind == "fund" {
			anchors[0].label = "基金开放开始"
			if p.FundEnd && e.End != e.Start {
				anchors = append(anchors, struct{ day, label string }{e.End, "基金开放最后一天"})
			}
		}
		for _, anchor := range anchors {
			day, err := time.ParseInLocation("2006-01-02", anchor.day, loc)
			if err != nil {
				continue
			}
			for _, before := range []int{1, 0} {
				if (before == 1 && !p.DatePrevious) || (before == 0 && !p.DateSame) {
					continue
				}
				body := anchor.label + "：" + anchor.day + "。仅提供日期提醒，具体受理时段和限制请核对原文。"
				add(anchor.label+":"+anchor.day, day.AddDate(0, 0, -before).Add(9*time.Hour), body)
			}
		}
	}
	return slots
}
func reminderKey(e calendar.Event, slot reminderSlot, device int64) string {
	return calendar.Key(fmt.Sprintf("%s:%s:%d:%d", e.ID, slot.Anchor, slot.Due, device))
}

// Reconciliation is atomic with preference changes. It preserves accepted jobs
// and retry deadlines, and never creates a new delivery for a past reminder time.
func reconcileReminders(tx *sql.Tx, now int64) error {
	if _, err := tx.Exec(`UPDATE deliveries SET status='expired',receipt_token='' WHERE status IN ('queued','sending') AND expires<=?`, now); err != nil {
		return err
	}
	p, err := reminderSettings(tx)
	if err != nil {
		return err
	}
	devices := []int64{}
	if p.Enabled {
		for _, id := range p.Devices {
			var active int
			if err = tx.QueryRow(`SELECT active FROM subscriptions WHERE id=?`, id).Scan(&active); err != nil && err != sql.ErrNoRows {
				return err
			}
			if active == 1 && !slices.Contains(devices, id) {
				devices = append(devices, id)
			}
		}
	}
	if len(devices) == 0 {
		_, err = tx.Exec(`UPDATE deliveries SET status='cancelled',error='事件、关注或设备已变更',receipt_token='' WHERE scenario='calendar' AND status='queued'`)
		return err
	}
	rows, err := tx.Query(`SELECT payload FROM calendar_events WHERE end_day>=?`, time.Unix(now, 0).UTC().AddDate(0, 0, -1).Format("2006-01-02"))
	if err != nil {
		return err
	}
	events := []calendar.Event{}
	for rows.Next() {
		var raw string
		var e calendar.Event
		if err = rows.Scan(&raw); err == nil {
			err = json.Unmarshal([]byte(raw), &e)
		}
		if err != nil {
			break
		}
		events = append(events, e)
	}
	rowErr := rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	if rowErr != nil {
		return rowErr
	}
	desired := map[string]bool{}
	for _, e := range events {
		if len(devices) == 0 {
			break
		}
		follow, _, err := reminderFollow(tx, p, e)
		if err != nil {
			return err
		}
		if !follow {
			continue
		}
		for _, slot := range reminderSlots(e, p) {
			if slot.Expires <= now {
				continue
			}
			for _, device := range devices {
				key := reminderKey(e, slot, device)
				desired[key] = true
				var id int64
				err = tx.QueryRow(`SELECT delivery_id FROM calendar_reminder_jobs WHERE key=?`, key).Scan(&id)
				if err == sql.ErrNoRows {
					if slot.Due < now {
						continue
					}
					t := token()
					result, err := tx.Exec(`INSERT INTO deliveries(subscription_id,scenario,status,due,expires,receipt_hash,receipt_token) VALUES(?,'calendar','queued',?,?,?,?)`, device, slot.Due, slot.Expires, digest(t), t)
					if err != nil {
						return err
					}
					id, err = result.LastInsertId()
					if err != nil {
						return err
					}
					_, err = tx.Exec(`INSERT INTO calendar_reminder_jobs VALUES(?,?,?,?,?,?,?)`, key, id, e.ID, slot.Anchor, slot.Due, e.Title, slot.Body)
					if err != nil {
						return err
					}
				} else if err != nil {
					return err
				} else {
					// A text-only revision refreshes pending content without another notification.
					_, err = tx.Exec(`UPDATE calendar_reminder_jobs SET title=?,body=? WHERE key=? AND delivery_id IN (SELECT id FROM deliveries WHERE status IN ('queued','cancelled'))`, e.Title, slot.Body, key)
					if err != nil {
						return err
					}
					if slot.Due >= now {
						t := token()
						_, err = tx.Exec(`UPDATE deliveries SET status='queued',due=?,error='',receipt_token=?,receipt_hash=? WHERE id=? AND status='cancelled' AND attempts=0`, slot.Due, t, digest(t), id)
						if err != nil {
							return err
						}
					}
				}
			}
		}
	}
	rows, err = tx.Query(`SELECT j.key,d.id FROM calendar_reminder_jobs j JOIN deliveries d ON d.id=j.delivery_id WHERE d.status='queued'`)
	if err != nil {
		return err
	}
	ids := []int64{}
	for rows.Next() {
		var key string
		var id int64
		if err = rows.Scan(&key, &id); err != nil {
			break
		}
		if !desired[key] {
			ids = append(ids, id)
		}
	}
	rowErr = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	if rowErr != nil {
		return rowErr
	}
	for _, id := range ids {
		if _, err = tx.Exec(`UPDATE deliveries SET status='cancelled',error='事件、关注或提醒设置已变更',receipt_token='' WHERE id=?`, id); err != nil {
			return err
		}
	}
	return nil
}

func (a *App) ReconcileReminders(now int64) error {
	tx, err := a.Store.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = reconcileReminders(tx, now); err != nil {
		return err
	}
	return tx.Commit()
}

// Recheck current event, selection and timing in the delivery claim transaction.
// A push already accepted by the browser service cannot be recalled.
func validCalendarDelivery(tx *sql.Tx, d *claimedDelivery, now int64) (bool, error) {
	p, err := reminderSettings(tx)
	if err != nil {
		return false, err
	}
	if !p.Enabled || !slices.Contains(p.Devices, d.SubscriptionID) {
		return false, nil
	}
	var raw string
	var e calendar.Event
	err = tx.QueryRow(`SELECT payload FROM calendar_events WHERE id=?`, d.EventID).Scan(&raw)
	if err == sql.ErrNoRows {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if err = json.Unmarshal([]byte(raw), &e); err != nil {
		return false, err
	}
	follow, _, err := reminderFollow(tx, p, e)
	if err != nil || !follow {
		return false, err
	}
	for _, slot := range reminderSlots(e, p) {
		if slot.Due <= now && slot.Expires > now && reminderKey(e, slot, d.SubscriptionID) == d.Key {
			d.Title, d.Body = e.Title, slot.Body
			return true, nil
		}
	}
	return false, nil
}
