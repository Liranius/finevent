package probe

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"finevent/internal/calendar"
)

func reminderFixture(t *testing.T, now time.Time) (*App, calendar.Event, ReminderSettings) {
	t.Helper()
	a := testApp(t)
	insertDelivery(t, a, now.Add(-time.Hour).Unix())
	p := defaultReminders()
	p.Enabled = true
	p.Devices = []int64{1}
	p.Kinds = []string{"other"}
	raw, _ := json.Marshal(p)
	if _, err := a.Store.DB.Exec(`INSERT INTO calendar_reminder_settings VALUES(1,?)`, string(raw)); err != nil {
		t.Fatal(err)
	}
	start := now.UTC().Add(48 * time.Hour).Truncate(time.Minute)
	e, err := (&calendar.Store{DB: a.Store.DB}).Save(calendar.Event{Source: "manual", SourceID: "reminder", Title: "提醒测试事件", Kind: "other", Start: start.Format(time.RFC3339), End: start.Format(time.RFC3339), Timezone: "UTC", Precision: "minute", Status: "confirmed", Importance: 2, URL: "https://example.org/notice", Evidence: "仅用于本地测试"}, 0)
	if err != nil {
		t.Fatal(err)
	}
	return a, e, p
}
func reminderCount(t *testing.T, a *App, status string) int {
	t.Helper()
	var n int
	if err := a.Store.DB.QueryRow(`SELECT COUNT(*) FROM deliveries WHERE scenario='calendar' AND status=?`, status).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}
func reconcileTest(t *testing.T, a *App, at time.Time) {
	t.Helper()
	if err := a.ReconcileReminders(at.Unix()); err != nil {
		t.Fatal(err)
	}
}

func TestReminderTimesRespectDSTAndCivilDates(t *testing.T) {
	p := defaultReminders()
	for _, at := range []string{"2026-03-08T08:30:00-04:00", "2026-11-01T08:30:00-05:00"} {
		e := calendar.Event{Precision: "minute", Start: at}
		slots := reminderSlots(e, p)
		start, _ := time.Parse(time.RFC3339, at)
		if len(slots) != 2 || slots[0].Due != start.Add(-24*time.Hour).Unix() || slots[1].Due != start.Add(-30*time.Minute).Unix() {
			t.Fatal(slots)
		}
	}
	e := calendar.Event{Precision: "date", Kind: "fund", Start: "2026-11-02", End: "2026-11-06", Timezone: "America/New_York"}
	slots := reminderSlots(e, p)
	for i, want := range []string{"2026-11-01T09:00:00+08:00", "2026-11-02T09:00:00+08:00", "2026-11-05T09:00:00+08:00", "2026-11-06T09:00:00+08:00"} {
		at, _ := time.Parse(time.RFC3339, want)
		if len(slots) != 4 || slots[i].Due != at.Unix() {
			t.Fatal(slots)
		}
	}
	e.End = e.Start
	if len(reminderSlots(e, p)) != 2 {
		t.Fatal("single-day fund duplicated")
	}
	e.Status = "cancelled"
	if len(reminderSlots(e, p)) != 0 {
		t.Fatal("cancelled event scheduled")
	}
	p.EarlyHours = 1
	p.LateMinutes = 60
	if len(reminderSlots(calendar.Event{Precision: "minute", Start: "2026-11-02T10:00:00+08:00"}, p)) != 1 {
		t.Fatal("identical offsets duplicated")
	}
	p.EarlyHours = 0
	p.LateMinutes = 1
	start, _ := time.Parse(time.RFC3339, "2026-11-02T10:00:00+08:00")
	if reminderSlots(calendar.Event{Precision: "minute", Start: start.Format(time.RFC3339)}, p)[0].Expires != start.Unix() {
		t.Fatal("reminder outlives timed event")
	}
}

func TestReminderReconciliationRevisionCancellationAndSettings(t *testing.T) {
	now := time.Now().UTC()
	a, e, p := reminderFixture(t, now)
	reconcileTest(t, a, now)
	reconcileTest(t, a, now)
	if reminderCount(t, a, "queued") != 2 {
		t.Fatal("duplicate plans")
	}
	s := &calendar.Store{DB: a.Store.DB}
	e.Title = "修订后的标题"
	var err error
	e, err = s.Save(e, e.Version)
	if err != nil {
		t.Fatal(err)
	}
	reconcileTest(t, a, now)
	var count int
	if err = a.Store.DB.QueryRow(`SELECT COUNT(*) FROM calendar_reminder_jobs WHERE title=?`, e.Title).Scan(&count); err != nil || count != 2 {
		t.Fatal("content revision duplicated or stale", count, err)
	}
	start, _ := time.Parse(time.RFC3339, e.Start)
	e.Start = start.Add(24 * time.Hour).Format(time.RFC3339)
	e.End = e.Start
	e, err = s.Save(e, e.Version)
	if err != nil {
		t.Fatal(err)
	}
	reconcileTest(t, a, now)
	if reminderCount(t, a, "queued") != 2 || reminderCount(t, a, "cancelled") != 2 {
		t.Fatal("reschedule did not replace pending jobs")
	}
	e.Status = "cancelled"
	e, err = s.Save(e, e.Version)
	if err != nil {
		t.Fatal(err)
	}
	reconcileTest(t, a, now)
	if reminderCount(t, a, "queued") != 0 {
		t.Fatal("cancelled event retained pending jobs")
	}
	e.Status = "confirmed"
	e, err = s.Save(e, e.Version)
	if err != nil {
		t.Fatal(err)
	}
	reconcileTest(t, a, now)
	if reminderCount(t, a, "queued") != 2 {
		t.Fatal("unattempted future reminders did not resume")
	}
	p.Enabled = false
	raw, _ := json.Marshal(p)
	w := perform(a, authed(t, a, "PUT", "reminders/settings", string(raw)))
	if w.Code != 200 || reminderCount(t, a, "queued") != 0 {
		t.Fatal(w.Code, w.Body.String())
	}
}

func TestFollowOverridesFundCodesAndNeverInventsNextWindow(t *testing.T) {
	now := time.Now()
	a, e, p := reminderFixture(t, now)
	e.Kind = "fund"
	e.FundCode = "123456"
	e.Precision = "date"
	e.Timezone = "Asia/Shanghai"
	e.Start = now.AddDate(0, 0, 2).Format("2006-01-02")
	e.End = now.AddDate(0, 0, 4).Format("2006-01-02")
	var err error
	e, err = (&calendar.Store{DB: a.Store.DB}).Save(e, e.Version)
	if err != nil {
		t.Fatal(err)
	}
	p.FundCodes = []string{"123456"}
	raw, _ := json.Marshal(p)
	w := perform(a, authed(t, a, "PUT", "reminders/settings", string(raw)))
	if w.Code != 200 || reminderCount(t, a, "queued") != 4 {
		t.Fatal(w.Code, w.Body.String())
	}
	w = perform(a, authed(t, a, "PUT", "reminders/events/"+e.ID, `{"enabled":false}`))
	if w.Code != 200 || reminderCount(t, a, "queued") != 0 {
		t.Fatal(w.Code, w.Body.String())
	}
	w = perform(a, authed(t, a, "DELETE", "reminders/events/"+e.ID, ""))
	if w.Code != 200 || reminderCount(t, a, "queued") != 4 {
		t.Fatal(w.Code, w.Body.String())
	}
	var jobs int
	_ = a.Store.DB.QueryRow(`SELECT COUNT(*) FROM calendar_reminder_jobs`).Scan(&jobs)
	if jobs != 4 {
		t.Fatal("unexpected future fund cycle", jobs)
	}
}

func TestNewFollowDoesNotBackfillAndExistingExpiredJobsStayExpired(t *testing.T) {
	now := time.Now().UTC()
	a, e, _ := reminderFixture(t, now)
	start, _ := time.Parse(time.RFC3339, e.Start)
	reconcileTest(t, a, start.Add(-20*time.Minute))
	if reminderCount(t, a, "queued") != 0 {
		t.Fatal("new subscription backfilled past reminders")
	}
	reconcileTest(t, a, now)
	reconcileTest(t, a, start.Add(time.Hour))
	if reminderCount(t, a, "expired") != 2 || reminderCount(t, a, "queued") != 0 {
		t.Fatal("expiry not retained")
	}
}

func TestClaimRejectsChangesSinceLastReconciliation(t *testing.T) {
	for _, kind := range []string{"cancel", "reschedule", "unfollow", "disable", "device", "title"} {
		t.Run(kind, func(t *testing.T) {
			now := time.Now().UTC()
			a, e, p := reminderFixture(t, now)
			reconcileTest(t, a, now)
			start, _ := time.Parse(time.RFC3339, e.Start)
			due := start.Add(-24 * time.Hour).Unix()
			var err error
			switch kind {
			case "title":
				e.Title = "发送前刚修订的标题"
				_, err = (&calendar.Store{DB: a.Store.DB}).Save(e, e.Version)
			case "cancel":
				e.Status = "cancelled"
				_, err = (&calendar.Store{DB: a.Store.DB}).Save(e, e.Version)
			case "reschedule":
				e.Start = start.Add(time.Hour).Format(time.RFC3339)
				e.End = e.Start
				_, err = (&calendar.Store{DB: a.Store.DB}).Save(e, e.Version)
			case "unfollow":
				_, err = a.Store.DB.Exec(`INSERT INTO calendar_reminder_watches VALUES(?,0)`, e.ID)
			case "disable":
				p.Enabled = false
				raw, _ := json.Marshal(p)
				_, err = a.Store.DB.Exec(`UPDATE calendar_reminder_settings SET payload=?`, string(raw))
			case "device":
				_, err = a.Store.DB.Exec(`UPDATE subscriptions SET active=0`)
			}
			if err != nil {
				t.Fatal(err)
			}
			d, err := a.claimDelivery(due)
			if kind == "title" {
				if err != nil || d == nil || d.Title != e.Title {
					t.Fatal("stale push content", d, err)
				}
				return
			}
			if err != nil || d != nil {
				t.Fatal("stale reminder claimed", d, err)
			}
		})
	}
}

func TestReminderRestartAndRetentionPreserveNoDuplicateClaim(t *testing.T) {
	now := time.Now().UTC()
	a, e, _ := reminderFixture(t, now)
	reconcileTest(t, a, now)
	var seq int
	var name, path string
	if err := a.Store.DB.QueryRow(`PRAGMA database_list`).Scan(&seq, &name, &path); err != nil {
		t.Fatal(err)
	}
	start, _ := time.Parse(time.RFC3339, e.Start)
	due := start.Add(-24 * time.Hour).Unix()
	d, err := a.claimDelivery(due)
	if err != nil || d == nil {
		t.Fatal(d, err)
	}
	second, err := a.claimDelivery(due)
	if err != nil || second != nil {
		t.Fatal("duplicate claim", second, err)
	}
	if err = a.Store.DB.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := OpenStore(filepath.Dir(path))
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.DB.Close()
	a.Store = reopened
	reconcileTest(t, a, time.Unix(due, 0))
	recovered, err := a.claimDelivery(due)
	if err != nil || recovered == nil || recovered.ID != d.ID || recovered.Receipt != d.Receipt {
		t.Fatal("restart lost stable identity", recovered, err)
	}
	if _, err = a.Store.DB.Exec(`UPDATE deliveries SET status='accepted',accepted_at=? WHERE id=?`, due, d.ID); err != nil {
		t.Fatal(err)
	}
	reconcileTest(t, a, time.Unix(due, 0))
	if reminderCount(t, a, "accepted") != 1 {
		t.Fatal("accepted reminder changed")
	}
	if err = a.Store.Prune(start.AddDate(0, 0, 40)); err != nil {
		t.Fatal("job foreign key prevents retention", err)
	}
}

func TestReminderHTTPValidationAndAuthentication(t *testing.T) {
	a, _, p := reminderFixture(t, time.Now())
	original, _ := reminderSettings(a.Store.DB)
	for _, kind := range []string{"negative", "no_device", "bad_code", "unknown_kind", "duplicate_device", "too_large"} {
		x := p
		switch kind {
		case "negative":
			x.LateMinutes = -1
		case "no_device":
			x.Devices = nil
		case "bad_code":
			x.FundCodes = []string{"123"}
		case "unknown_kind":
			x.Kinds = []string{"bogus"}
		case "duplicate_device":
			x.Devices = []int64{1, 1}
		case "too_large":
			x.EarlyHours = 169
		}
		raw, _ := json.Marshal(x)
		w := perform(a, authed(t, a, "PUT", "reminders/settings", string(raw)))
		if w.Code != 400 {
			t.Fatal(kind, w.Code, w.Body.String())
		}
	}
	unchanged, _ := reminderSettings(a.Store.DB)
	old, _ := json.Marshal(original)
	next, _ := json.Marshal(unchanged)
	if string(old) != string(next) {
		t.Fatal("invalid settings partially saved")
	}
	w := perform(a, httptest.NewRequest("GET", prefix+"reminders", nil))
	if w.Code != 401 {
		t.Fatal(w.Code)
	}
	r := authed(t, a, "PUT", "reminders/settings", string(old))
	r.Header.Set("Origin", "https://other.test")
	if w = perform(a, r); w.Code != 403 {
		t.Fatal(w.Code)
	}
	w = perform(a, authed(t, a, "GET", "reminders", ""))
	if w.Code != 200 || strings.Contains(w.Body.String(), "receipt_token") || strings.Contains(w.Body.String(), "fcm.googleapis") {
		t.Fatal(w.Code, w.Body.String())
	}
}

func TestCalendarPushRetryUsesStableTopicAndReceipt(t *testing.T) {
	now := time.Now().UTC()
	a, e, p := reminderFixture(t, now)
	p.EarlyHours = 0
	raw, _ := json.Marshal(p)
	_, err := a.Store.DB.Exec(`UPDATE calendar_reminder_settings SET payload=?`, string(raw))
	if err != nil {
		t.Fatal(err)
	}
	start := now.Add(30*time.Minute - 5*time.Second)
	e.Start = start.Format(time.RFC3339)
	e.End = e.Start
	e, err = (&calendar.Store{DB: a.Store.DB}).Save(e, e.Version)
	if err != nil {
		t.Fatal(err)
	}
	reconcileTest(t, a, now.Add(-10*time.Second))
	var id int64
	var receipt, key string
	if err = a.Store.DB.QueryRow(`SELECT d.id,d.receipt_token,j.key FROM deliveries d JOIN calendar_reminder_jobs j ON j.delivery_id=d.id`).Scan(&id, &receipt, &key); err != nil {
		t.Fatal(err)
	}
	calls := 0
	a.Client = &http.Client{Transport: fakeTransport(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.Header.Get("Topic") != "cal-"+key[:28] {
			t.Error("unstable topic")
		}
		code := 503
		if calls == 2 {
			code = 201
		}
		return &http.Response{StatusCode: code, Body: io.NopCloser(strings.NewReader(""))}, nil
	})}
	if err = a.SendDue(context.Background()); err != nil {
		t.Fatal(err)
	}
	reconcileTest(t, a, now)
	var attempt int
	var due int64
	var retained string
	if err = a.Store.DB.QueryRow(`SELECT due,attempts,receipt_token FROM deliveries WHERE id=?`, id).Scan(&due, &attempt, &retained); err != nil || attempt != 1 || due <= now.Unix() || retained != receipt {
		t.Fatal("retry reset by reconciliation", err)
	}
	_, err = a.Store.DB.Exec(`UPDATE deliveries SET due=? WHERE id=?`, now.Unix(), id)
	if err != nil {
		t.Fatal(err)
	}
	if err = a.SendDue(context.Background()); err != nil {
		t.Fatal(err)
	}
	if calls != 2 || reminderCount(t, a, "accepted") != 1 {
		t.Fatal("calendar push failed", calls)
	}
}
