package probe

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"slices"
	"sort"
	"time"
)

type trendRequest struct {
	Section  string          `json:"section,omitempty"`
	Revision string          `json:"revision,omitempty"`
	Value    json.RawMessage `json:"value,omitempty"`
	Undo     bool            `json:"undo,omitempty"`
}

type trendReply struct {
	Status int             `json:"status"`
	Error  string          `json:"error"`
	Data   json.RawMessage `json:"data"`
}

func (a *App) trendRequest(ctx context.Context, op string, request trendRequest) (trendReply, error) {
	var reply trendReply
	if a.TrendSettingsSocket == "" {
		return reply, errors.New("unconfigured settings socket")
	}
	deadline := time.Now().Add(6 * time.Second)
	conn, err := (&net.Dialer{Deadline: deadline}).DialContext(ctx, "unix", a.TrendSettingsSocket)
	if err != nil {
		return reply, err
	}
	defer conn.Close()
	if err = conn.SetDeadline(deadline); err != nil {
		return reply, err
	}
	err = json.NewEncoder(conn).Encode(struct {
		Op string `json:"op"`
		trendRequest
	}{op, request})
	if err == nil {
		err = json.NewDecoder(io.LimitReader(conn, 2<<20)).Decode(&reply)
	}
	return reply, err
}

func (a *App) trendSettingsRoutes(m *http.ServeMux) {
	handler := func(w http.ResponseWriter, r *http.Request) {
		var req trendRequest
		op := "read"
		if r.Method == http.MethodPut {
			op = "write"
			if decodeLimit(w, r, &req, 1<<20) != nil || (req.Section != "sources" && req.Section != "keywords") || len(req.Revision) != 64 {
				fail(w, 400, "配置请求无效，请重新读取。")
				return
			}
		}
		reply, err := a.trendRequest(r.Context(), op, req)
		if err != nil {
			fail(w, 503, "TrendRadar 配置服务不可用，请核对服务器连接。")
			return
		}
		switch reply.Status {
		case 200:
			writeJSON(w, 200, reply.Data)
		case 400, 409, 503:
			fail(w, reply.Status, reply.Error)
		default:
			fail(w, 503, "TrendRadar 配置响应无效。")
		}
	}
	m.HandleFunc("GET "+prefix+"news/trendradar-settings", handler)
	m.HandleFunc("PUT "+prefix+"news/trendradar-settings", handler)
}

type sourcePeriod struct {
	Start   float64  `json:"start"`
	End     *float64 `json:"end"`
	IDs     []string `json:"ids"`
	Catalog []string `json:"catalog"`
}

func enabledAt(periods []sourcePeriod, at int64) map[string]bool {
	for i := len(periods) - 1; i >= 0; i-- {
		p := periods[i]
		if p.Start <= float64(at) && (p.End == nil || float64(at) < *p.End) {
			ids := make(map[string]bool, len(p.IDs))
			for _, id := range p.IDs {
				ids[id] = true
			}
			return ids
		}
	}
	return nil
}

func (a *App) sourcePeriods(ctx context.Context) ([]sourcePeriod, error) {
	reply, err := a.trendRequest(ctx, "periods", trendRequest{})
	if err != nil || reply.Status != 200 {
		return nil, errors.New("source configuration unavailable")
	}
	var periods []sourcePeriod
	if err = json.Unmarshal(reply.Data, &periods); err != nil || len(periods) == 0 {
		return nil, errors.New("source configuration unavailable")
	}
	return periods, nil
}

func applySourcePeriods(health *TrendHealth, periods []sourcePeriod) {
	current := enabledAt(periods, health.ServerTime+1)
	if current == nil {
		health.ConfigError = "当前来源配置缺少生效时间记录。"
		return
	}
	launch := health.LatestCycle - health.LatestCycle%1800
	expected := enabledAt(periods, launch)
	health.ConfigKnown = true
	seen := map[string]bool{}
	for _, s := range health.Sources {
		seen[s.ID] = true
	}
	for _, id := range periods[len(periods)-1].Catalog {
		if !seen[id] {
			health.Sources = append(health.Sources, TrendSource{ID: id, State: "missing", LastStatus: "unknown"})
		}
	}
	for i := range health.Sources {
		s := &health.Sources[i]
		if !current[s.ID] {
			unexpected := expected != nil && !expected[s.ID] && health.LatestCycle > 0 &&
				s.LastAttempt == health.LatestCycle && s.State != "missing"
			s.State = "disabled"
			if unexpected {
				s.State = "unexpected"
			}
			s.Stale = false
			continue
		}
		since := periods[len(periods)-1].Start
		for j := len(periods) - 2; j >= 0; j-- {
			p := periods[j]
			if p.End == nil || *p.End != since || !slices.Contains(p.IDs, s.ID) {
				break
			}
			since = p.Start
		}
		if float64(launch) < since {
			if float64(health.ServerTime)-since > trendStaleSeconds {
				s.State = "missing"
			} else {
				s.State = "pending"
				s.Stale = false
			}
		}
	}
	sort.Slice(health.Sources, func(i, j int) bool { return health.Sources[i].ID < health.Sources[j].ID })
}
