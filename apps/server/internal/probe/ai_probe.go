package probe

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"regexp"
	"strings"
	"time"
)

const probeReserveNano int64 = 20_000_000 // CNY 0.02; uncertain usage remains reserved.
const monthlyBudgetNano int64 = 30_000_000_000
const probeOutputTokens = 1024
const deepseekEndpoint = "https://api.deepseek.com/chat/completions"

type ProbePricing struct {
	Version string `json:"version"`
	Input   int64  `json:"input_nano_per_token"`
	Cached  int64  `json:"cached_nano_per_token"`
	Output  int64  `json:"output_nano_per_token"`
}

type AIProbeRequest struct {
	ID      string       `json:"id"`
	Key     string       `json:"api_key"`
	Expires int64        `json:"expires"`
	Pricing ProbePricing `json:"pricing"`
}

var probeID = regexp.MustCompile(`^[a-zA-Z0-9-]{8,80}$`)

func probeBody() []byte {
	// A synthetic fixture only. No credentials, portfolio data or production news leave the service.
	fixture := "测试资料 fixture-1：某虚构地区宣布下季度对工业节能设备采购给予补贴，具体规模及执行细则尚未公布。该资料仅用于软件验证。"
	request := map[string]any{
		"model": "deepseek-flash", "stream": false, "max_tokens": probeOutputTokens,
		"thinking":        map[string]string{"type": "disabled"},
		"response_format": map[string]string{"type": "json_object"},
		"messages": []map[string]string{
			{"role": "system", "content": `仅根据测试资料返回 JSON，不调用工具。格式示例：{"test_only":true,"summary":"事实摘要","conditions":["成立条件"],"evidence":["fixture-1"]}。说明不确定因素，不编造数值。`},
			{"role": "user", "content": fixture},
		},
	}
	body, _ := json.Marshal(request)
	return body
}

func (a *App) reserveAIProbe(r AIProbeRequest) (bool, error) {
	now := time.Now()
	p := r.Pricing
	if !probeID.MatchString(r.ID) || len(r.Key) < 8 || len(r.Key) > 512 || strings.ContainsAny(r.Key, "\r\n") || r.Expires <= now.Unix() || r.Expires > now.Add(15*time.Minute).Unix() {
		return false, errors.New("invalid or expired AI probe request")
	}
	if p.Version == "" || len(p.Version) > 64 || p.Input <= 0 || p.Input > 100000 || p.Cached < 0 || p.Cached > p.Input || p.Output <= 0 || p.Output > 100000 {
		return false, errors.New("invalid AI probe pricing")
	}
	// Byte count plus generous message framing bounds the small fixed text input.
	if int64(len(probeBody())+1024)*p.Input+probeOutputTokens*p.Output > probeReserveNano {
		return false, errors.New("AI probe reservation too small for configured prices")
	}
	zone, _ := time.LoadLocation("Asia/Shanghai")
	pricing, _ := json.Marshal(p)
	// One atomic INSERT both enforces the monthly allowance and prevents retry after a restart.
	result, err := a.Store.DB.Exec(`INSERT OR IGNORE INTO ai_probe_runs(id,month,started,status,reserved_nano,pricing)
SELECT ?,?,?, 'reserved',?,? WHERE
COALESCE((SELECT SUM(COALESCE(cost_upper_nano,reserved_nano)) FROM ai_budget_entries WHERE month=?),0)+?<=?`,
		r.ID, now.In(zone).Format("2006-01"), now.Unix(), probeReserveNano, string(pricing), now.In(zone).Format("2006-01"), probeReserveNano, monthlyBudgetNano)
	if err != nil {
		return false, err
	}
	n, err := result.RowsAffected()
	if err == nil && n == 1 {
		_, err = a.Store.DB.Exec(`INSERT INTO ai_probe_timings(id) VALUES(?)`, r.ID)
	}
	return n == 1, err
}

func (a *App) startAIProbe(ctx context.Context) string {
	if a.ProbeRequestPath == "" || !a.aiProbeMu.TryLock() {
		return ""
	}
	info, err := os.Lstat(a.ProbeRequestPath)
	if err != nil {
		a.aiProbeMu.Unlock()
		return ""
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 16384 {
		log.Print("AI probe request must be a private regular file <=16 KiB")
		a.aiProbeMu.Unlock()
		return ""
	}
	b, err := os.ReadFile(a.ProbeRequestPath)
	var request AIProbeRequest
	if err == nil {
		err = json.Unmarshal(b, &request)
	}
	// Consume the secret before calling the network. It is never stored in the database or logs.
	if removeErr := os.Remove(a.ProbeRequestPath); removeErr != nil {
		a.aiProbeMu.Unlock()
		log.Print("AI probe secret could not be removed; no request sent")
		return ""
	}
	if err != nil {
		a.aiProbeMu.Unlock()
		log.Print("Invalid AI probe request; no request sent")
		return ""
	}
	ok, err := a.reserveAIProbe(request)
	if err != nil || !ok {
		a.aiProbeMu.Unlock()
		log.Print("AI probe not reserved: invalid request, duplicate ID or exhausted budget")
		return ""
	}
	a.aiProbeWorkers.Add(1)
	started := make(chan struct{})
	go func() {
		defer a.aiProbeWorkers.Done()
		defer a.aiProbeMu.Unlock()
		transport := http.DefaultTransport.(*http.Transport).Clone()
		transport.Proxy = nil
		defer transport.CloseIdleConnections()
		client := &http.Client{Transport: transport, Timeout: 45 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
		a.executeAIProbe(ctx, request, client, func() { close(started) })
	}()
	<-started
	return request.ID
}

func (a *App) collectProbeSource(ctx context.Context, url, probe string) {
	begin := time.Now().UnixMilli()
	p := a.CollectWallstreet(ctx, url)
	end := time.Now().UnixMilli()
	if err := a.Store.SavePoll(p); err != nil {
		log.Printf("save source observation: %v", err)
	}
	if probe != "" {
		if _, err := a.Store.DB.Exec(`UPDATE ai_probe_timings SET collector_started_ms=?,collector_completed_ms=?,collector_success=? WHERE id=?`, begin, end, p.Success, probe); err != nil {
			log.Print("AI probe collector timing could not be saved")
		}
	}
}

type probeResponse struct {
	Choices []struct {
		Finish  string `json:"finish_reason"`
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	} `json:"choices"`
	Usage *struct {
		Prompt     *int64 `json:"prompt_tokens"`
		Cached     *int64 `json:"prompt_cache_hit_tokens"`
		Completion *int64 `json:"completion_tokens"`
	} `json:"usage"`
}

func validProbeContent(content string) bool {
	var v struct {
		TestOnly   bool     `json:"test_only"`
		Summary    string   `json:"summary"`
		Conditions []string `json:"conditions"`
		Evidence   []string `json:"evidence"`
	}
	return json.Unmarshal([]byte(content), &v) == nil && v.TestOnly && strings.TrimSpace(v.Summary) != "" && len(v.Conditions) > 0 && len(v.Evidence) == 1 && v.Evidence[0] == "fixture-1"
}

func (a *App) executeAIProbe(parent context.Context, r AIProbeRequest, client *http.Client, started func()) {
	ctx, cancel := context.WithTimeout(parent, 45*time.Second)
	defer cancel()
	begin := time.Now()
	status, httpStatus := "request_failed", 0
	var cost, prompt, cached, completion any
	var modelStarted int64
	defer func() {
		if _, err := a.Store.DB.Exec(`UPDATE ai_probe_timings SET model_started_ms=?,model_completed_ms=? WHERE id=?`, modelStarted, time.Now().UnixMilli(), r.ID); err != nil {
			log.Print("AI probe model timing could not be saved")
		}
		_, err := a.Store.DB.Exec(`UPDATE ai_probe_runs SET completed=?,status=?,cost_upper_nano=?,prompt_tokens=?,cached_tokens=?,completion_tokens=?,http_status=?,duration_ms=? WHERE id=?`, time.Now().Unix(), status, cost, prompt, cached, completion, httpStatus, time.Since(begin).Milliseconds(), r.ID)
		if err != nil {
			log.Print("AI probe result could not be saved; reservation retained")
		}
	}()
	sampleDone := make(chan struct{})
	go func() {
		defer close(sampleDone)
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			if err := a.SampleResources(); err != nil {
				log.Print("AI probe resource sample failed")
			}
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
			}
		}
	}()
	defer func() { cancel(); <-sampleDone; _ = a.SampleResources() }()
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, deepseekEndpoint, bytes.NewReader(probeBody()))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+r.Key)
	modelStarted = time.Now().UnixMilli()
	started()
	response, err := client.Do(req)
	if err != nil {
		return
	}
	defer response.Body.Close()
	httpStatus = response.StatusCode
	if httpStatus != 200 {
		status = "http_error"
		return
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 512*1024+1))
	if err != nil || len(body) > 512*1024 {
		status = "invalid_response"
		return
	}
	var decoded probeResponse
	if json.Unmarshal(body, &decoded) != nil {
		status = "invalid_response"
		return
	}
	u := decoded.Usage
	if u == nil || u.Prompt == nil || u.Completion == nil || *u.Prompt < 0 || *u.Prompt > 1000000 || *u.Completion < 0 || *u.Completion > 1000000 {
		status = "usage_unknown"
		return
	}
	hit := int64(0)
	if u.Cached != nil {
		hit = *u.Cached
	}
	if hit < 0 || hit > *u.Prompt {
		status = "usage_unknown"
		return
	}
	prompt, cached, completion = *u.Prompt, hit, *u.Completion
	// Conservatively book at the configured peak rates, including cache hits. This is not an invoice.
	charged := (*u.Prompt-hit)*r.Pricing.Input + hit*r.Pricing.Cached + *u.Completion*r.Pricing.Output
	cost = charged
	if *u.Completion > probeOutputTokens {
		status = "output_limit_exceeded"
		return
	}
	if charged > probeReserveNano {
		status = "reservation_exceeded"
		return
	}
	if len(decoded.Choices) != 1 || decoded.Choices[0].Finish != "stop" || !validProbeContent(decoded.Choices[0].Message.Content) {
		status = "invalid_output"
		return
	}
	status = "succeeded"
}
