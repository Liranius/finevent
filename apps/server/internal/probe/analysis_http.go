package probe

import (
	"database/sql"
	"errors"
	"net/http"
	"strconv"
	"time"
)

func (a *App) analysisRoutes(m *http.ServeMux) {
	m.HandleFunc("GET "+prefix+"ai", func(w http.ResponseWriter, r *http.Request) {
		v, err := a.AIOverview(r.Context())
		if err != nil {
			a.internal(w, err)
			return
		}
		writeJSON(w, 200, v)
	})
	m.HandleFunc("PUT "+prefix+"ai/settings", func(w http.ResponseWriter, r *http.Request) {
		var v AISettings
		if decode(w, r, &v) != nil {
			fail(w, 400, "解读设置格式无效")
			return
		}
		if v.Enabled && !a.AIConfig.valid(time.Now()) {
			fail(w, 409, "服务端尚未配置有效密钥和价格；请在服务器终端配置")
			return
		}
		if err := a.Store.SaveAISettings(r.Context(), v, time.Now().Unix()); err != nil {
			a.internal(w, err)
			return
		}
		writeJSON(w, 200, v)
	})
	m.HandleFunc("GET "+prefix+"ai/analyses/{id}", func(w http.ResponseWriter, r *http.Request) {
		v, err := a.Store.Analysis(r.Context(), r.PathValue("id"))
		if errors.Is(err, sql.ErrNoRows) {
			fail(w, 404, "解读不存在或已超过保留期")
			return
		}
		if err != nil {
			a.internal(w, err)
			return
		}
		writeJSON(w, 200, v)
	})
	m.HandleFunc("GET "+prefix+"news/{id}/analysis", func(w http.ResponseWriter, r *http.Request) {
		id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
		if err != nil || id <= 0 {
			fail(w, 400, "报道编号无效")
			return
		}
		v, err := a.Store.NewsAnalysis(r.Context(), id)
		if err != nil {
			a.internal(w, err)
			return
		}
		writeJSON(w, 200, map[string]any{"job": v})
	})
	m.HandleFunc("POST "+prefix+"news/{id}/analysis", func(w http.ResponseWriter, r *http.Request) {
		id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
		if err != nil || id <= 0 {
			fail(w, 400, "报道编号无效")
			return
		}
		var input struct {
			Retry bool `json:"retry"`
		}
		if decode(w, r, &input) != nil {
			fail(w, 400, "解读请求无效")
			return
		}
		key, err := a.Store.QueueAnalysis(r.Context(), id, a.AIConfig, input.Retry, time.Now().Unix())
		if errors.Is(err, ErrAIDisabled) || errors.Is(err, ErrAIQueue) {
			fail(w, 409, err.Error())
			return
		}
		if errors.Is(err, sql.ErrNoRows) {
			fail(w, 404, "报道不存在或已超过保留期")
			return
		}
		if err != nil {
			a.internal(w, err)
			return
		}
		a.wakeAnalysis()
		writeJSON(w, 202, map[string]string{"id": key})
	})
}
