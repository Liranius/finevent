package probe

import (
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	webpush "github.com/SherClockHolmes/webpush-go"
	"golang.org/x/crypto/bcrypt"
)

func testApp(t *testing.T) *App {
	t.Helper()
	s, e := OpenStore(t.TempDir())
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { s.DB.Close() })
	private, public, e := webpush.GenerateVAPIDKeys()
	if e != nil {
		t.Fatal(e)
	}
	hash, _ := bcrypt.GenerateFromPassword([]byte("test-password-123"), bcrypt.MinCost)
	return NewApp(Config{Origin: "https://example.test", PasswordHash: string(hash), VAPIDPrivate: private, VAPIDPublic: public, Contact: "mailto:owner@example.test"}, s, t.TempDir())
}
func authed(t *testing.T, a *App, method, path, body string) *http.Request {
	t.Helper()
	key := token()
	_, e := a.Store.DB.Exec(`INSERT INTO sessions VALUES(?,?)`, digest(key), time.Now().Add(time.Hour).Unix())
	if e != nil {
		t.Fatal(e)
	}
	r := httptest.NewRequest(method, prefix+path, strings.NewReader(body))
	r.Header.Set("Origin", a.Config.Origin)
	r.AddCookie(&http.Cookie{Name: "finevent_session", Value: key})
	return r
}
func perform(a *App, r *http.Request) *httptest.ResponseRecorder {
	w := httptest.NewRecorder()
	a.Handler().ServeHTTP(w, r)
	return w
}

func TestSessionSecurity(t *testing.T) {
	a := testApp(t)
	if w := perform(a, httptest.NewRequest("GET", prefix+"status", nil)); w.Code != 401 {
		t.Fatal(w.Code)
	}
	r := httptest.NewRequest("POST", prefix+"login", strings.NewReader(`{"password":"test-password-123"}`))
	r.Header.Set("Origin", a.Config.Origin)
	w := perform(a, r)
	if w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	cookies := w.Result().Cookies()
	if len(cookies) != 1 || !cookies[0].Secure || !cookies[0].HttpOnly || cookies[0].SameSite != http.SameSiteStrictMode {
		t.Fatal(cookies)
	}
	r = httptest.NewRequest("POST", prefix+"logout", nil)
	r.AddCookie(cookies[0])
	r.Header.Set("Origin", "https://evil.test")
	if w = perform(a, r); w.Code != 403 {
		t.Fatal(w.Code)
	}
	r.Header.Set("Origin", a.Config.Origin)
	if w = perform(a, r); w.Code != 200 {
		t.Fatal(w.Code)
	}
	r = httptest.NewRequest("GET", prefix+"status", nil)
	r.AddCookie(cookies[0])
	if w = perform(a, r); w.Code != 401 {
		t.Fatal("logged-out session retained")
	}
}
func TestLoginRateLimit(t *testing.T) {
	a := testApp(t)
	for i := 0; i < 6; i++ {
		r := httptest.NewRequest("POST", prefix+"login", strings.NewReader(`{"password":"wrong"}`))
		r.Header.Set("Origin", a.Config.Origin)
		w := perform(a, r)
		want := 401
		if i == 5 {
			want = 429
		}
		if w.Code != want {
			t.Fatalf("attempt %d: %d", i, w.Code)
		}
	}
}

func TestLoginRequiresConfiguredOrigin(t *testing.T) {
	a := testApp(t)
	a.Config.Origin = "https://www.example.com"
	for _, origin := range []string{"", "null", "https://example.com", "http://www.example.com", "https://www.example.com.evil.test"} {
		t.Run(origin, func(t *testing.T) {
			r := httptest.NewRequest("POST", prefix+"login", strings.NewReader(`{"password":"test-password-123"}`))
			r.Header.Set("Origin", origin)
			w := perform(a, r)
			if w.Code != http.StatusForbidden || len(w.Result().Cookies()) != 0 {
				t.Fatalf("unexpected origin accepted: %q, status %d", origin, w.Code)
			}
		})
	}
	r := httptest.NewRequest("POST", prefix+"login", strings.NewReader(`{"password":"test-password-123"}`))
	r.Header.Set("Origin", a.Config.Origin)
	w := perform(a, r)
	if w.Code != http.StatusOK || len(w.Result().Cookies()) != 1 {
		t.Fatalf("configured origin rejected: %d %s", w.Code, w.Body.String())
	}
}

func TestEndpointRestriction(t *testing.T) {
	for _, u := range []string{"http://fcm.googleapis.com/x", "https://fcm.googleapis.com.evil.test/x", "https://127.0.0.1/x", "https://fcm.googleapis.com:443/x", "https://user@fcm.googleapis.com/x"} {
		if validEndpoint(u) {
			t.Fatal(u)
		}
	}
	for _, u := range []string{"https://fcm.googleapis.com/fcm/send/abc", "https://wns.notify.windows.com/x"} {
		if !validEndpoint(u) {
			t.Fatal(u)
		}
	}
}

func TestRevisionAndLatency(t *testing.T) {
	a := testApp(t)
	now := time.Now().Unix()
	items := []Item{{Source: "fast", SourceID: "1", Title: "original", URL: "https://example.test/a?utm_source=x", PublishedAt: now - 120}, {Source: "fast", SourceID: "2", Title: "baseline", PublishedAt: now - 5000, Baseline: true}, {Source: "trend", SourceID: "3", Title: "unknown"}}
	n, c, e := a.Store.SaveItems(items, now)
	if e != nil || n != 3 || c != 0 {
		t.Fatalf("%d %d %v", n, c, e)
	}
	items[0].Title = "corrected"
	n, c, e = a.Store.SaveItems(items, now+60)
	if e != nil || n != 0 || c != 1 {
		t.Fatalf("%d %d %v", n, c, e)
	}
	var first, version int64
	var address string
	if e = a.Store.DB.QueryRow(`SELECT first_seen,version,url FROM news WHERE source_id='1'`).Scan(&first, &version, &address); e != nil {
		t.Fatal(e)
	}
	if first != now || version != 2 || address != "https://example.test/a" {
		t.Fatalf("%d %d %s", first, version, address)
	}
	stats, e := a.Store.Latencies(now - 1)
	if e != nil {
		t.Fatal(e)
	}
	if len(stats) != 2 || stats[0].Samples != 1 || stats[0].P95 != 120 || stats[1].Unknown != 1 {
		t.Fatalf("%+v", stats)
	}
	var revisions int
	if e = a.Store.DB.QueryRow(`SELECT COUNT(*) FROM news_revisions`).Scan(&revisions); e != nil || revisions != 1 {
		t.Fatalf("%d %v", revisions, e)
	}
}
func TestTrendRadarReadOnlyAndMidnight(t *testing.T) {
	root := t.TempDir()
	if e := os.Mkdir(filepath.Join(root, "news"), 0700); e != nil {
		t.Fatal(e)
	}
	zone, _ := time.LoadLocation("Asia/Shanghai")
	now := time.Date(2026, 10, 27, 0, 1, 0, 0, zone)
	for _, date := range []string{"2026-10-26", "2026-10-27"} {
		path := filepath.Join(root, "news", date+".db")
		db, e := sql.Open("sqlite", path)
		if e != nil {
			t.Fatal(e)
		}
		_, e = db.Exec(`CREATE TABLE news_items(id INTEGER,title TEXT,platform_id TEXT,url TEXT,first_crawl_time TEXT,last_crawl_time TEXT); INSERT INTO news_items VALUES(1,'headline','cls-telegraph','https://example.test/news','00-00','00-00')`)
		db.Close()
		if e != nil {
			t.Fatal(e)
		}
	}
	items, e := ReadTrendRadar(root, now)
	if e != nil {
		t.Fatal(e)
	}
	if len(items) != 2 || items[1].PublishedAt != 0 || items[1].FirstSeen != now.Add(-time.Minute).Unix() {
		t.Fatalf("%+v", items)
	}
	a := testApp(t)
	n, _, e := a.Store.SaveItems(items, now.Unix())
	if e != nil || n != 1 {
		t.Fatalf("cross-day duplicate: %d %v", n, e)
	}
	if _, e = ReadTrendRadar(t.TempDir(), now); e == nil {
		t.Fatal("missing source silently accepted")
	}
}
func TestCollectorBaselineAndGap(t *testing.T) {
	a := testApp(t)
	id := 1
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, `{"data":{"items":[{"id":%d,"content_text":"<b>消息</b>","uri":"https://example.test/a","display_time":%d}]}}`, id, time.Now().Unix()-20)
	}))
	defer server.Close()
	p := a.CollectWallstreet(context.Background(), server.URL)
	if !p.Success || p.Added != 1 {
		t.Fatalf("%+v", p)
	}
	stats, e := a.Store.Latencies(0)
	if e != nil || len(stats) != 0 {
		t.Fatalf("baseline leaked: %+v %v", stats, e)
	}
	id = 2
	p = a.CollectWallstreet(context.Background(), server.URL)
	if !p.Success || p.Gap == "" || p.Added != 1 {
		t.Fatalf("%+v", p)
	}
}
func TestCollectorFailures(t *testing.T) {
	for _, body := range []string{`{"data":{"items":[]}}`, `not json`} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, body) }))
		_, e := FetchWallstreet(context.Background(), http.DefaultClient, server.URL)
		server.Close()
		if e == nil {
			t.Fatal("malformed source accepted")
		}
	}
}

type fakeTransport func(*http.Request) (*http.Response, error)

func (f fakeTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func insertDelivery(t *testing.T, a *App, expires int64) (int64, string) {
	t.Helper()
	_, pub, e := webpush.GenerateVAPIDKeys()
	if e != nil {
		t.Fatal(e)
	}
	sub := webpush.Subscription{Endpoint: "https://fcm.googleapis.com/fcm/send/test", Keys: webpush.Keys{P256dh: pub, Auth: base64.RawURLEncoding.EncodeToString(make([]byte, 16))}}
	b, _ := json.Marshal(sub)
	res, e := a.Store.DB.Exec(`INSERT INTO subscriptions(endpoint,payload,label,created) VALUES(?,?,'test',?)`, sub.Endpoint, string(b), time.Now().Unix())
	if e != nil {
		t.Fatal(e)
	}
	device, _ := res.LastInsertId()
	receipt := token()
	res, e = a.Store.DB.Exec(`INSERT INTO deliveries(subscription_id,scenario,status,due,expires,receipt_hash,receipt_token) VALUES(?,'locked','queued',?,?,?,?)`, device, time.Now().Unix()-1, expires, digest(receipt), receipt)
	if e != nil {
		t.Fatal(e)
	}
	id, _ := res.LastInsertId()
	return id, receipt
}

func TestPushAcceptedIsNotReceived(t *testing.T) {
	a := testApp(t)
	id, receipt := insertDelivery(t, a, time.Now().Add(time.Minute).Unix())
	a.Client = &http.Client{Transport: fakeTransport(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 201, Body: io.NopCloser(strings.NewReader("")), Header: make(http.Header)}, nil
	})}
	if e := a.SendDue(context.Background()); e != nil {
		t.Fatal(e)
	}
	var status string
	var received int
	if e := a.Store.DB.QueryRow(`SELECT status,received_at FROM deliveries WHERE id=?`, id).Scan(&status, &received); e != nil || status != "accepted" || received != 0 {
		t.Fatalf("%s %d %v", status, received, e)
	}
	for _, tok := range []string{strings.Repeat("a", 64), receipt} {
		body := fmt.Sprintf(`{"id":%d,"token":%q,"kind":"received"}`, id, tok)
		w := perform(a, httptest.NewRequest("POST", prefix+"push/receipt", strings.NewReader(body)))
		want := 404
		if tok == receipt {
			want = 200
		}
		if w.Code != want {
			t.Fatalf("%d: %s", w.Code, w.Body.String())
		}
	}
	w := perform(a, authed(t, a, "GET", "status", ""))
	if strings.Contains(w.Body.String(), receipt) || strings.Contains(w.Body.String(), a.Config.VAPIDPrivate) {
		t.Fatal("status exposes credential")
	}
}
func TestPushExpiredAndGone(t *testing.T) {
	for _, expired := range []bool{true, false} {
		t.Run(fmt.Sprint(expired), func(t *testing.T) {
			a := testApp(t)
			expiry := time.Now().Add(time.Minute).Unix()
			if expired {
				expiry = time.Now().Add(-time.Second).Unix()
			}
			id, _ := insertDelivery(t, a, expiry)
			calls := 0
			a.Client = &http.Client{Transport: fakeTransport(func(r *http.Request) (*http.Response, error) {
				calls++
				return &http.Response{StatusCode: 410, Body: io.NopCloser(strings.NewReader(""))}, nil
			})}
			if e := a.SendDue(context.Background()); e != nil {
				t.Fatal(e)
			}
			var state string
			var active int
			if e := a.Store.DB.QueryRow(`SELECT d.status,s.active FROM deliveries d JOIN subscriptions s ON s.id=d.subscription_id WHERE d.id=?`, id).Scan(&state, &active); e != nil {
				t.Fatal(e)
			}
			if expired && (calls != 0 || state != "expired") {
				t.Fatal(state, calls)
			}
			if !expired && (active != 0 || state != "failed") {
				t.Fatal(state, active)
			}
		})
	}
}
func TestPushRetriesAndRecovery(t *testing.T) {
	a := testApp(t)
	id, _ := insertDelivery(t, a, time.Now().Add(5*time.Minute).Unix())
	a.Client = &http.Client{Transport: fakeTransport(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 503, Body: io.NopCloser(strings.NewReader(""))}, nil
	})}
	if e := a.SendDue(context.Background()); e != nil {
		t.Fatal(e)
	}
	var state string
	var attempts int
	var due int64
	if e := a.Store.DB.QueryRow(`SELECT status,attempts,due FROM deliveries WHERE id=?`, id).Scan(&state, &attempts, &due); e != nil {
		t.Fatal(e)
	}
	if state != "queued" || attempts != 1 || due <= time.Now().Unix() {
		t.Fatal(state, attempts, due)
	}
	dir := t.TempDir()
	store, e := OpenStore(dir)
	if e != nil {
		t.Fatal(e)
	}
	_, e = store.DB.Exec(`INSERT INTO subscriptions(id,endpoint,payload,label,created) VALUES(1,'x','{}','x',0); INSERT INTO deliveries(subscription_id,scenario,status,due,expires,receipt_hash,receipt_token) VALUES(1,'locked','sending',0,9999999999,'x','x')`)
	if e != nil {
		t.Fatal(e)
	}
	store.DB.Close()
	store, e = OpenStore(dir)
	if e != nil {
		t.Fatal(e)
	}
	defer store.DB.Close()
	if e = store.DB.QueryRow(`SELECT status FROM deliveries`).Scan(&state); e != nil || state != "queued" {
		t.Fatal(state, e)
	}
}
func TestRetentionAndStaticIsolation(t *testing.T) {
	a := testApp(t)
	now := time.Now()
	_, e := a.Store.DB.Exec(`INSERT INTO sessions VALUES('old',1); INSERT INTO resources VALUES(1,1,1,1,1,1,0)`)
	if e != nil {
		t.Fatal(e)
	}
	if e = a.Store.Prune(now); e != nil {
		t.Fatal(e)
	}
	var n int
	if e = a.Store.DB.QueryRow(`SELECT COUNT(*) FROM resources`).Scan(&n); e != nil || n != 0 {
		t.Fatal(n, e)
	}
	if e = os.WriteFile(filepath.Join(a.Web, "index.html"), []byte("shell"), 0600); e != nil {
		t.Fatal(e)
	}
	w := perform(a, httptest.NewRequest("GET", "/finevent/index.html", nil))
	if w.Code != 200 || w.Body.String() != "shell" {
		t.Fatal(w.Code, w.Body.String())
	}
	w = perform(a, httptest.NewRequest("GET", "/finevent/api/missing", nil))
	if w.Code == 200 {
		t.Fatal("API returned application shell")
	}
}
