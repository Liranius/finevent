package probe

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func newsPushFixture(t *testing.T) (*App, int64, NewsPushSettings) {
	t.Helper()
	a := testApp(t)
	now := time.Now().Unix()
	insertDelivery(t, a, now-1)
	p := NewsPushSettings{Enabled: true, Devices: []int64{1}, Categories: []string{"monetary"}, Industries: []string{}}
	setNewsPush(t, a, p)
	return a, now, p
}
func setNewsPush(t *testing.T, a *App, p NewsPushSettings) {
	t.Helper()
	raw, _ := json.Marshal(p)
	w := perform(a, authed(t, a, "PUT", "news-push/settings", string(raw)))
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
}
func newsPushPlan(t *testing.T, a *App, at int64) {
	t.Helper()
	if err := a.PlanNewsPush(at); err != nil {
		t.Fatal(err)
	}
}
func newsPushCount(t *testing.T, a *App, state string) int {
	t.Helper()
	var n int
	if err := a.Store.DB.QueryRow(`SELECT COUNT(*) FROM deliveries WHERE scenario='news' AND (?='' OR status=?)`, state, state).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}
func pushNews(id string) Item {
	return Item{Source: "wallstreetcn", SourceID: id, Title: "央行宣布降准0.5个百分点", Content: "央行宣布下调存款准备金率0.5个百分点，涉及银行流动性安排。完整细则以发布机构正式文件为准，本报道保留来源。", URL: "https://example.org/news/" + id}
}

func TestNewsPushWindowScopeAndNoBackfill(t *testing.T) {
	a, now, p := newsPushFixture(t)
	old := pushNews("old")
	saveNews(t, a.Store, []Item{old}, now-10)
	p.Enabled = false
	setNewsPush(t, a, p)
	p.Enabled = true
	setNewsPush(t, a, p)
	baseline := pushNews("baseline")
	baseline.Baseline = true
	stale := pushNews("stale")
	stale.PublishedAt = now - 3600
	future := pushNews("future")
	future.PublishedAt = now + 600
	oldSeen := pushNews("oldSeen")
	oldSeen.FirstSeen = now - 300
	minor := pushNews("minor")
	minor.Title = "央行逆回购"
	minor.Content = "银行流动性"
	outside := pushNews("outside")
	outside.Title = "出口管制"
	outside.Content = "半导体政策"
	saveNews(t, a.Store, []Item{baseline, stale, future, oldSeen, minor, outside}, now)
	newsPushPlan(t, a, now)
	if newsPushCount(t, a, "") != 0 {
		t.Fatal("historical, baseline or non-followed news was sent")
	}
	i := pushNews("fresh")
	saveNews(t, a.Store, []Item{i}, now)
	newsPushPlan(t, a, now)
	if newsPushCount(t, a, "queued") != 1 {
		t.Fatal("fresh important item missing")
	}
	var body string
	a.Store.DB.QueryRow(`SELECT body FROM news_push_jobs`).Scan(&body)
	if !strings.Contains(body, "来源发布时间未知") {
		t.Fatal(body)
	}
	setNewsPush(t, a, p)
	newsPushPlan(t, a, now)
	if newsPushCount(t, a, "queued") != 1 {
		t.Fatal("identical save changed pending alert")
	}
	p.Enabled = false
	setNewsPush(t, a, p)
	p.Enabled = true
	setNewsPush(t, a, p)
	newsPushPlan(t, a, now)
	if newsPushCount(t, a, "queued") != 0 || newsPushCount(t, a, "cancelled") != 1 {
		t.Fatal("pause/resume replayed news")
	}
}

func TestNewsPushCrossSourceAndMaterialRevision(t *testing.T) {
	a, now, _ := newsPushFixture(t)
	i := pushNews("one")
	saveNews(t, a.Store, []Item{i}, now)
	newsPushPlan(t, a, now)
	copy := i
	copy.Source = "trendradar:cls-telegraph"
	copy.SourceID = "two"
	copy.Content = ""
	copy.Title += "，关注后续细则"
	saveNews(t, a.Store, []Item{copy}, now+1)
	newsPushPlan(t, a, now+1)
	copy2 := i
	copy2.SourceID = "three"
	copy2.URL = "https://example.org/other"
	saveNews(t, a.Store, []Item{copy2}, now+2)
	newsPushPlan(t, a, now+2)
	if newsPushCount(t, a, "") != 1 {
		t.Fatal("duplicates weren't merged")
	}
	i.Title += "。"
	saveNews(t, a.Store, []Item{i}, now+3)
	newsPushPlan(t, a, now+3)
	if newsPushCount(t, a, "queued") != 1 {
		t.Fatal("cosmetic edit replayed/cancelled alert")
	}
	d, err := a.claimDelivery(now + 3)
	if err != nil || d == nil || d.NewsID != 1 || d.Title != i.Title || d.Receipt == "" {
		t.Fatal(d, err)
	}
	a.Store.DB.Exec(`UPDATE deliveries SET status='accepted' WHERE id=?`, d.ID)
	copy.Title += "。"
	saveNews(t, a.Store, []Item{copy}, now+4)
	newsPushPlan(t, a, now+4)
	if newsPushCount(t, a, "") != 1 {
		t.Fatal("cross-source cosmetic edit replayed alert")
	}
	i.Title = "央行宣布降准0.25个百分点"
	i.Content = strings.ReplaceAll(i.Content, "0.5", "0.25")
	saveNews(t, a.Store, []Item{i}, now+5)
	newsPushPlan(t, a, now+5)
	if newsPushCount(t, a, "queued") != 1 || newsPushCount(t, a, "") != 2 {
		t.Fatal("important revision missing")
	}
	i.Title = "更正：央行未降准0.25个百分点"
	saveNews(t, a.Store, []Item{i}, now+6)
	newsPushPlan(t, a, now+6)
	if newsPushCount(t, a, "queued") != 1 || newsPushCount(t, a, "cancelled") != 1 {
		t.Fatal("superseded pending revision not cancelled")
	}
	newsPushPlan(t, a, now+7)
	if newsPushCount(t, a, "") != 3 {
		t.Fatal("reconciliation duplicated")
	}
	i = pushNews("one")
	saveNews(t, a.Store, []Item{i}, now+8)
	newsPushPlan(t, a, now+8)
	if newsPushCount(t, a, "") != 4 {
		t.Fatal("return to an earlier value is a new material revision")
	}

}

func TestNewsPushSimilarTitlesAreSeparateAndIndustryMatches(t *testing.T) {
	a, now, p := newsPushFixture(t)
	p.Categories = []string{}
	p.Industries = []string{"finance"}
	setNewsPush(t, a, p)
	one := pushNews("one")
	two := pushNews("two")
	two.Title = "另一央行宣布降准0.5个百分点"
	two.Content = "该国银行细则另行通知"
	saveNews(t, a.Store, []Item{one, two}, now)
	newsPushPlan(t, a, now)
	if newsPushCount(t, a, "queued") != 2 {
		t.Fatal("similar headlines merged or industry OR match failed")
	}
}

func TestNewsPushConflictingTitleDoesNotReplaceOriginalContentIdentity(t *testing.T) {
	a, now, _ := newsPushFixture(t)
	one := pushNews("one")
	two := pushNews("two")
	two.Title = "更正：央行未降准0.5个百分点"
	third := pushNews("three")
	for _, item := range []Item{one, two, third} {
		saveNews(t, a.Store, []Item{item}, now)
		newsPushPlan(t, a, now)
	}
	if newsPushCount(t, a, "queued") != 2 {
		t.Fatal("contradictory headline merged or original exact content identity lost")
	}
}

func TestNewsPushClaimRechecksExpiryDowngradeAndDevice(t *testing.T) {
	for _, kind := range []string{"expire", "downgrade", "device", "scope"} {
		t.Run(kind, func(t *testing.T) {
			a, now, p := newsPushFixture(t)
			i := pushNews(kind)
			saveNews(t, a.Store, []Item{i}, now)
			newsPushPlan(t, a, now)
			switch kind {
			case "expire":
				now += 901
			case "downgrade":
				i.Title = "央行常规操作"
				i.Content = "例行说明"
				saveNews(t, a.Store, []Item{i}, now+1)
			case "device":
				a.Store.DB.Exec(`UPDATE subscriptions SET active=0`)
			case "scope":
				p.Categories = []string{"company"}
				setNewsPush(t, a, p)
			}
			d, err := a.claimDelivery(now)
			if err != nil || d != nil {
				t.Fatal("invalid delivery claimed", d, err)
			}
			if newsPushCount(t, a, "queued") != 0 {
				t.Fatal("invalid pending delivery retained")
			}
		})
	}
}

func TestNewsPushRestartRetryReceiptAndRetention(t *testing.T) {
	a, now, _ := newsPushFixture(t)
	i := pushNews("one")
	saveNews(t, a.Store, []Item{i}, now)
	newsPushPlan(t, a, now)
	d, err := a.claimDelivery(now)
	if err != nil || d == nil {
		t.Fatal(d, err)
	}
	var path string
	var seq int
	var name string
	if err = a.Store.DB.QueryRow(`PRAGMA database_list`).Scan(&seq, &name, &path); err != nil {
		t.Fatal(err)
	}
	a.Store.DB.Close()
	s, err := OpenStore(filepath.Dir(path))
	if err != nil {
		t.Fatal(err)
	}
	defer s.DB.Close()
	a.Store = s
	recovered, err := a.claimDelivery(now)
	if err != nil || recovered == nil || recovered.ID != d.ID || recovered.Receipt != d.Receipt {
		t.Fatal(recovered, err)
	}
	a.Store.DB.Exec(`UPDATE deliveries SET status='queued',due=? WHERE id=?`, now, d.ID)
	calls := 0
	a.Client = &http.Client{Transport: fakeTransport(func(r *http.Request) (*http.Response, error) {
		calls++
		status := 503
		if calls > 1 {
			status = 201
		}
		return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(""))}, nil
	})}
	if err = a.SendDue(context.Background()); err != nil {
		t.Fatal(err)
	}
	newsPushPlan(t, a, now)
	var due int64
	a.Store.DB.QueryRow(`SELECT due FROM deliveries WHERE id=?`, d.ID).Scan(&due)
	if due <= now {
		t.Fatal("retry backoff missing")
	}
	a.Store.DB.Exec(`UPDATE deliveries SET due=? WHERE id=?`, now, d.ID)
	if err = a.SendDue(context.Background()); err != nil {
		t.Fatal(err)
	}
	if newsPushCount(t, a, "accepted") != 1 {
		t.Fatal("news send failed")
	}
	w := perform(a, authed(t, a, "GET", "news-push", ""))
	if w.Code != 200 || strings.Contains(w.Body.String(), d.Receipt) || strings.Contains(w.Body.String(), "fcm.googleapis.com") {
		t.Fatal(w.Body.String())
	}
	body := fmt.Sprintf(`{"id":%d,"token":%q,"kind":"clicked"}`, d.ID, d.Receipt)
	if w = perform(a, httptest.NewRequest("POST", prefix+"push/receipt", strings.NewReader(body))); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	if err = a.Store.Prune(time.Unix(now, 0).AddDate(0, 0, 91)); err != nil {
		t.Fatal(err)
	}
}

func TestNewsPushSettingsSecurityAndValidation(t *testing.T) {
	a, _, _ := newsPushFixture(t)
	if w := perform(a, httptest.NewRequest("GET", prefix+"news-push", nil)); w.Code != 401 {
		t.Fatal(w.Code)
	}
	for _, body := range []string{`{"enabled":true}`, `{"enabled":true,"devices":[999],"categories":["macro"]}`, `{"categories":["fake"]}`, `{"industries":["fake"]}`, `{"cursor":1}`} {
		w := perform(a, authed(t, a, "PUT", "news-push/settings", body))
		if w.Code != 400 {
			t.Fatal(w.Code, body)
		}
	}
	r := authed(t, a, "PUT", "news-push/settings", `{}`)
	r.Header.Set("Origin", "https://evil.test")
	if w := perform(a, r); w.Code != 403 {
		t.Fatal(w.Code)
	}
}
