package probe

import (
	"crypto/ecdh"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"finevent/internal/calendar"

	webpush "github.com/SherClockHolmes/webpush-go"
	"golang.org/x/crypto/bcrypt"
)

const prefix = "/finevent/api/v1/"

type App struct {
	Config            Config
	Store             *Store
	Client            *http.Client
	CalendarClient    *http.Client
	Web               string
	CollectionEnabled bool
	TrendEnabled      bool
	ProbeRequestPath  string
	AIConfig          AIConfig
	AIClient          *http.Client
	analysisWake      chan struct{}
	aiProbeMu         sync.Mutex
	aiProbeWorkers    sync.WaitGroup
	loginMu           sync.Mutex
	loginAttempts     int
	loginWindow       time.Time
}

func NewApp(c Config, s *Store, web string) *App {
	return &App{Config: c, Store: s, Web: web, analysisWake: make(chan struct{}, 1), CalendarClient: calendar.NewHTTPClient(), Client: &http.Client{Timeout: 15 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}}
}
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		log.Printf("response: %v", err)
	}
}
func fail(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}
func decode(w http.ResponseWriter, r *http.Request, v any) error {
	return decodeLimit(w, r, v, 16384)
}
func decodeLimit(w http.ResponseWriter, r *http.Request, v any, limit int64) error {
	r.Body = http.MaxBytesReader(w, r.Body, limit)
	d := json.NewDecoder(r.Body)
	d.DisallowUnknownFields()
	if err := d.Decode(v); err != nil {
		return err
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		return errors.New("extra JSON")
	}
	return nil
}
func (a *App) signedIn(r *http.Request) bool {
	c, e := r.Cookie("finevent_session")
	if e != nil {
		return false
	}
	var n int
	e = a.Store.DB.QueryRow(`SELECT COUNT(*) FROM sessions WHERE token=? AND expires>?`, digest(c.Value), time.Now().Unix()).Scan(&n)
	return e == nil && n == 1
}
func (a *App) internal(w http.ResponseWriter, err error) {
	log.Printf("request failed: %v", err)
	fail(w, 500, "服务暂时不可用，请稍后重试")
}

func (a *App) Handler() http.Handler {
	m := http.NewServeMux()
	m.HandleFunc("GET "+prefix+"session", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]bool{"authenticated": a.signedIn(r)})
	})
	m.HandleFunc("POST "+prefix+"login", a.login)
	m.HandleFunc("POST "+prefix+"logout", a.logout)
	m.HandleFunc("GET "+prefix+"status", a.status)
	m.HandleFunc("GET "+prefix+"news/collection", a.collectionHistory)
	m.HandleFunc("POST "+prefix+"push/subscriptions", a.subscribe)
	m.HandleFunc("DELETE "+prefix+"push/subscriptions/{id}", a.unsubscribe)
	m.HandleFunc("POST "+prefix+"push/tests", a.queueTest)
	m.HandleFunc("POST "+prefix+"push/results/{id}", a.manualResult)
	m.HandleFunc("POST "+prefix+"push/receipt", a.receipt)
	a.calendarRoutes(m)
	a.reminderRoutes(m)
	a.newsRoutes(m)
	a.sourceHealthRoutes(m)
	a.newsPushRoutes(m)
	a.analysisRoutes(m)
	m.HandleFunc("GET /finevent/healthz", func(w http.ResponseWriter, r *http.Request) {
		if e := a.Store.DB.PingContext(r.Context()); e != nil {
			fail(w, 503, "unhealthy")
			return
		}
		writeJSON(w, 200, map[string]string{"status": "ok", "stage": "verification"})
	})
	m.HandleFunc("GET /finevent/", a.static)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "same-origin")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; worker-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
		if strings.HasPrefix(r.URL.Path, prefix) {
			w.Header().Set("Cache-Control", "no-store")
			public := r.URL.Path == prefix+"login" || r.URL.Path == prefix+"session" || r.URL.Path == prefix+"push/receipt"
			if !public && !a.signedIn(r) {
				fail(w, 401, "请先登录")
				return
			}
			if r.Method != "GET" && r.Method != "HEAD" && r.URL.Path != prefix+"push/receipt" && r.Header.Get("Origin") != a.Config.Origin {
				fail(w, 403, "请求来源不匹配")
				return
			}
		}
		m.ServeHTTP(w, r)
	})
}

func (a *App) login(w http.ResponseWriter, r *http.Request) {
	a.loginMu.Lock()
	if time.Since(a.loginWindow) > 5*time.Minute {
		a.loginAttempts = 0
		a.loginWindow = time.Now()
	}
	if a.loginAttempts >= 5 {
		a.loginMu.Unlock()
		w.Header().Set("Retry-After", "300")
		fail(w, 429, "尝试过于频繁，请 5 分钟后再试")
		return
	}
	a.loginAttempts++
	a.loginMu.Unlock()
	var input struct {
		Password string `json:"password"`
	}
	if decode(w, r, &input) != nil {
		fail(w, 400, "请求格式不正确")
		return
	}
	if bcrypt.CompareHashAndPassword([]byte(a.Config.PasswordHash), []byte(input.Password)) != nil {
		fail(w, 401, "密码不正确")
		return
	}
	t := token()
	expiry := time.Now().Add(30 * 24 * time.Hour)
	if _, err := a.Store.DB.Exec(`INSERT INTO sessions VALUES(?,?)`, digest(t), expiry.Unix()); err != nil {
		a.internal(w, err)
		return
	}
	a.loginMu.Lock()
	a.loginAttempts = 0
	a.loginMu.Unlock()
	http.SetCookie(w, &http.Cookie{Name: "finevent_session", Value: t, Path: "/finevent/", HttpOnly: true, Secure: strings.HasPrefix(a.Config.Origin, "https:"), SameSite: http.SameSiteStrictMode, Expires: expiry})
	writeJSON(w, 200, map[string]bool{"authenticated": true})
}
func (a *App) logout(w http.ResponseWriter, r *http.Request) {
	if c, e := r.Cookie("finevent_session"); e == nil {
		if _, e = a.Store.DB.Exec(`DELETE FROM sessions WHERE token=?`, digest(c.Value)); e != nil {
			a.internal(w, e)
			return
		}
	}
	http.SetCookie(w, &http.Cookie{Name: "finevent_session", Path: "/finevent/", MaxAge: -1, HttpOnly: true, Secure: strings.HasPrefix(a.Config.Origin, "https:"), SameSite: http.SameSiteStrictMode})
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func validEndpoint(endpoint string) bool {
	u, e := url.Parse(endpoint)
	if e != nil || u.Scheme != "https" || u.User != nil || u.Port() != "" || u.Fragment != "" {
		return false
	}
	host := strings.ToLower(u.Hostname())
	return host == "fcm.googleapis.com" || host == "updates.push.services.mozilla.com" || strings.HasSuffix(host, ".push.services.mozilla.com") || strings.HasSuffix(host, ".notify.windows.com")
}
func (a *App) subscribe(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Label        string               `json:"label"`
		Subscription webpush.Subscription `json:"subscription"`
	}
	if decode(w, r, &input) != nil {
		fail(w, 400, "订阅格式不正确")
		return
	}
	key, e := base64.RawURLEncoding.DecodeString(input.Subscription.Keys.P256dh)
	auth, e2 := base64.RawURLEncoding.DecodeString(input.Subscription.Keys.Auth)
	_, e3 := ecdh.P256().NewPublicKey(key)
	if !validEndpoint(input.Subscription.Endpoint) || e != nil || e2 != nil || e3 != nil || len(auth) != 16 || len(input.Label) > 120 || strings.TrimSpace(input.Label) == "" {
		fail(w, 400, "无效或不支持的浏览器订阅")
		return
	}
	data, _ := json.Marshal(input.Subscription)
	var id int64
	e = a.Store.DB.QueryRow(`INSERT INTO subscriptions(endpoint,payload,label,created) VALUES(?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET payload=excluded.payload,label=excluded.label,active=1 RETURNING id`, input.Subscription.Endpoint, string(data), input.Label, time.Now().Unix()).Scan(&id)
	if e != nil {
		a.internal(w, e)
		return
	}
	writeJSON(w, 200, map[string]int64{"id": id})
}
func (a *App) unsubscribe(w http.ResponseWriter, r *http.Request) {
	if _, e := a.Store.DB.Exec(`UPDATE subscriptions SET active=0 WHERE id=?`, r.PathValue("id")); e != nil {
		a.internal(w, e)
		return
	}
	writeJSON(w, 200, map[string]bool{"ok": true})
}
func (a *App) queueTest(w http.ResponseWriter, r *http.Request) {
	var input struct {
		DeviceID int64  `json:"device_id"`
		Scenario string `json:"scenario"`
		Delay    int    `json:"delay_seconds"`
	}
	if decode(w, r, &input) != nil || input.Delay < 0 || input.Delay > 120 {
		fail(w, 400, "延迟应为 0–120 秒")
		return
	}
	allowed := map[string]bool{"foreground": true, "background": true, "locked": true, "network_switch": true, "reconnect": true}
	if !allowed[input.Scenario] {
		fail(w, 400, "未知测试场景")
		return
	}
	var active int
	if e := a.Store.DB.QueryRow(`SELECT active FROM subscriptions WHERE id=?`, input.DeviceID).Scan(&active); e != nil || active != 1 {
		fail(w, 400, "请先启用设备通知")
		return
	}
	var pending int
	if e := a.Store.DB.QueryRow(`SELECT COUNT(*) FROM deliveries WHERE scenario NOT IN ('calendar','news') AND status IN ('queued','sending')`).Scan(&pending); e != nil {
		a.internal(w, e)
		return
	}
	if pending >= 10 {
		fail(w, 429, "已有较多待发送测试，请等待完成")
		return
	}
	t := token()
	due := time.Now().Unix() + int64(input.Delay)
	res, e := a.Store.DB.Exec(`INSERT INTO deliveries(subscription_id,scenario,status,due,expires,receipt_hash,receipt_token) VALUES(?,?,'queued',?,?,?,?)`, input.DeviceID, input.Scenario, due, due+300, digest(t), t)
	if e != nil {
		a.internal(w, e)
		return
	}
	id, _ := res.LastInsertId()
	writeJSON(w, 201, map[string]int64{"id": id, "due": due})
}
func (a *App) receipt(w http.ResponseWriter, r *http.Request) {
	var input struct {
		ID    int64  `json:"id"`
		Token string `json:"token"`
		Kind  string `json:"kind"`
	}
	if decode(w, r, &input) != nil || len(input.Token) != 64 {
		fail(w, 400, "无效回执")
		return
	}
	column := "received_at"
	if input.Kind == "clicked" {
		column = "clicked_at"
	} else if input.Kind != "received" {
		fail(w, 400, "无效回执类型")
		return
	}
	result, e := a.Store.DB.Exec(fmt.Sprintf(`UPDATE deliveries SET %s=CASE WHEN %s=0 THEN ? ELSE %s END WHERE id=? AND receipt_hash=? AND expires>?`, column, column, column), time.Now().Unix(), input.ID, digest(input.Token), time.Now().Add(-24*time.Hour).Unix())
	if e != nil {
		a.internal(w, e)
		return
	}
	n, _ := result.RowsAffected()
	if n == 0 {
		fail(w, 404, "回执已失效")
		return
	}
	writeJSON(w, 200, map[string]bool{"ok": true})
}
func (a *App) manualResult(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Result string `json:"result"`
	}
	if decode(w, r, &input) != nil || (input.Result != "seen" && input.Result != "not_seen") {
		fail(w, 400, "请选择实际观察结果")
		return
	}
	if _, e := a.Store.DB.Exec(`UPDATE deliveries SET manual_result=? WHERE id=?`, input.Result, r.PathValue("id")); e != nil {
		a.internal(w, e)
		return
	}
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func queryMaps(db *sql.DB, query string, args ...any) ([]map[string]any, error) {
	rows, e := db.Query(query, args...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	cols, e := rows.Columns()
	if e != nil {
		return nil, e
	}
	out := []map[string]any{}
	for rows.Next() {
		values := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range values {
			ptrs[i] = &values[i]
		}
		if e = rows.Scan(ptrs...); e != nil {
			return nil, e
		}
		item := map[string]any{}
		for i, k := range cols {
			item[k] = values[i]
		}
		out = append(out, item)
	}
	return out, rows.Err()
}

func (a *App) status(w http.ResponseWriter, r *http.Request) {
	result := map[string]any{"stage": "verification", "vapid_public_key": a.Config.VAPIDPublic, "collection_enabled": a.CollectionEnabled, "trendradar_enabled": a.TrendEnabled, "server_time": time.Now().Unix()}
	queries := map[string]string{
		"devices":    `SELECT id,label,active,created FROM subscriptions ORDER BY id DESC`,
		"deliveries": `SELECT d.id,s.label,d.scenario,d.status,d.due,d.expires,d.attempts,d.accepted_at,d.received_at,d.clicked_at,d.error,d.manual_result FROM deliveries d JOIN subscriptions s ON s.id=d.subscription_id WHERE d.scenario NOT IN ('calendar','news') ORDER BY d.id DESC LIMIT 30`,
		"polls":      `SELECT * FROM polls ORDER BY id DESC LIMIT 30`,
		"coverage":   `SELECT source,MIN(started) AS first_poll,MAX(completed) AS last_poll,COUNT(*) AS polls,SUM(success) AS successes,SUM(CASE WHEN gap<>'' THEN 1 ELSE 0 END) AS gaps FROM polls GROUP BY source`,
		"news":       `SELECT id,source,title,url,published_at,first_seen,last_seen,version,baseline FROM news ORDER BY last_seen DESC,id DESC LIMIT 20`,
		"resources":  `SELECT * FROM resources ORDER BY at DESC LIMIT 60`,
	}
	for k, q := range queries {
		rows, e := queryMaps(a.Store.DB, q)
		if e != nil {
			a.internal(w, e)
			return
		}
		result[k] = rows
	}
	latencies, e := a.Store.Latencies(time.Now().Add(-7 * 24 * time.Hour).Unix())
	if e != nil {
		a.internal(w, e)
		return
	}
	result["latencies"] = latencies
	writeJSON(w, 200, result)
}

func (a *App) static(w http.ResponseWriter, r *http.Request) {
	name := strings.TrimPrefix(r.URL.Path, "/finevent/")
	if name == "" {
		name = "index.html"
	}
	if !filepath.IsLocal(name) || strings.HasPrefix(name, "api/") {
		http.NotFound(w, r)
		return
	}
	path := filepath.Join(a.Web, filepath.FromSlash(name))
	info, e := os.Stat(path)
	if e != nil || info.IsDir() {
		http.NotFound(w, r)
		return
	}
	if name == "sw.js" || name == "index.html" || name == "precache.json" {
		w.Header().Set("Cache-Control", "no-cache")
	} else if strings.HasPrefix(name, "assets/") {
		w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	}
	if name == "sw.js" {
		w.Header().Set("Service-Worker-Allowed", "/finevent/")
	}
	// ServeContent avoids FileServer's /index.html redirect, needed for the shell's precache.
	f, e := os.Open(path)
	if e != nil {
		http.NotFound(w, r)
		return
	}
	defer f.Close()
	http.ServeContent(w, r, name, info.ModTime(), f)
}
