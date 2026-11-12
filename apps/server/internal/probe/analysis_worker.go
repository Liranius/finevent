package probe

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"time"
)

func (a *App) wakeAnalysis() {
	select {
	case a.analysisWake <- struct{}{}:
	default:
	}
}

func (a *App) runAnalysis(ctx context.Context) {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		case <-a.analysisWake:
		}
		if err := a.Store.PlanAnalysis(ctx, a.AIConfig, time.Now().Unix()); err != nil && ctx.Err() == nil {
			log.Print("AI scheduling failed; no credentials logged")
		}
		if err := a.ProcessAnalysis(ctx); err != nil && ctx.Err() == nil {
			log.Print("AI task persistence failed; reservation retained")
		}
	}
}

func (a *App) ProcessAnalysis(ctx context.Context) error {
	// The one-shot verification probe and business worker share a single invocation slot.
	if !a.aiProbeMu.TryLock() {
		return nil
	}
	defer a.aiProbeMu.Unlock()
	c := a.AIConfig
	now := time.Now()
	if !c.valid(now) {
		return nil
	}
	zone, _ := time.LoadLocation("Asia/Shanghai")
	month := now.In(zone).Format("2006-01")
	if now.Add(time.Minute).In(zone).Format("2006-01") != month {
		return nil
	}
	tx, err := a.Store.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var enabled bool
	var pause int64
	if err = tx.QueryRow(`SELECT enabled,not_before FROM ai_settings WHERE singleton=1`).Scan(&enabled, &pause); err != nil {
		return err
	}
	if !enabled || pause > now.Unix() {
		return nil
	}
	if _, err = tx.Exec(`UPDATE ai_jobs SET status='config_changed',error='模型或提示词配置已变化，请对所需报道重新请求解读' WHERE status IN ('queued','budget_wait') AND (model<>? OR prompt_version<>?)`, c.Model, analysisPromptVersion); err != nil {
		return err
	}
	j, err := scanAIJob(tx.QueryRow(`SELECT `+aiJobColumns+` FROM ai_jobs WHERE status IN ('queued','budget_wait') AND expires>? AND model=? AND prompt_version=? ORDER BY created,id LIMIT 1`, now.Unix(), c.Model, analysisPromptVersion))
	if errors.Is(err, sql.ErrNoRows) {
		return tx.Commit()
	}
	if err != nil {
		return err
	}
	for _, e := range j.Evidence {
		changed, eErr := evidenceChanged(tx, e)
		if eErr != nil {
			return eErr
		}
		if changed {
			_, err = tx.Exec(`UPDATE ai_jobs SET status='superseded',error='排队期间来源已修订，请请求最新解读' WHERE id=?`, j.ID)
			if err != nil {
				return err
			}
			return tx.Commit()
		}
	}
	body := analysisBody(j.Model, j.Evidence)
	reserve := (int64(len(body))+1024)*c.Pricing.Input + analysisOutputTokens*c.Pricing.Output
	// Never persist the credential in per-call configuration snapshots.
	costConfig := c
	costConfig.Key = ""
	pricing, _ := json.Marshal(costConfig)
	res, err := tx.Exec(`INSERT INTO ai_calls(job_id,month,started,status,reserved_nano,pricing) SELECT ?,?,?,'running',?,? WHERE COALESCE((SELECT SUM(COALESCE(cost_upper_nano,reserved_nano)) FROM ai_budget_entries WHERE month=?),0)+?<=?`, j.ID, month, now.Unix(), reserve, string(pricing), month, reserve, monthlyBudgetNano)
	if err != nil {
		return err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if n == 0 {
		_, err = tx.Exec(`UPDATE ai_jobs SET status='budget_wait',error='本月剩余预算不足，等待下月额度；新闻采集继续' WHERE id=?`, j.ID)
		if err != nil {
			return err
		}
		return tx.Commit()
	}
	callID, err := res.LastInsertId()
	if err != nil {
		return err
	}
	_, err = tx.Exec(`UPDATE ai_jobs SET status='running',attempts=attempts+1,error='' WHERE id=?`, j.ID)
	if err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	return a.executeAnalysis(ctx, j, callID, c, body, reserve)
}

func (a *App) executeAnalysis(parent context.Context, j AIJob, callID int64, c AIConfig, body []byte, reserve int64) error {
	ctx, cancel := context.WithTimeout(parent, 45*time.Second)
	defer cancel()
	begin := time.Now()
	status, message := "request_failed", "调用未完成；费用预留保留，可核对后手动重试"
	code := 0
	var cost, prompt, cached, completion any
	basis := "reserved_peak"
	result := ""
	client := a.AIClient
	if client == nil {
		t := http.DefaultTransport.(*http.Transport).Clone()
		t.Proxy = nil
		defer t.CloseIdleConnections()
		client = &http.Client{Transport: t, Timeout: 45 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, deepseekEndpoint, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.Key)
	if _, err = a.Store.DB.ExecContext(ctx, `INSERT INTO ai_call_timings(call_id,started_ms) VALUES(?,?)`, callID, time.Now().UnixMilli()); err != nil {
		return err
	}
	// In-memory timestamp excludes the timing-row write; only a completed row proves an interval.
	startedMS := time.Now().UnixMilli()
	response, callErr := client.Do(req)
	completedMS := time.Now().UnixMilli()
	if callErr == nil {
		defer response.Body.Close()
		code = response.StatusCode
		if code != 200 {
			status = "http_error"
			message = "模型服务拒绝或暂时失败；费用预留保留，未自动重试"
		} else {
			raw, e := io.ReadAll(io.LimitReader(response.Body, 512*1024+1))
			completedMS = time.Now().UnixMilli()
			var decoded probeResponse
			if e != nil || len(raw) > 512*1024 || json.Unmarshal(raw, &decoded) != nil {
				status = "usage_unknown"
				message = "响应不完整，用量未确认；保留费用预留"
			} else {
				u := decoded.Usage
				if u == nil || u.Prompt == nil || u.Completion == nil || *u.Prompt < 0 || *u.Prompt > 1000000 || *u.Completion < 0 || *u.Completion > 1000000 {
					status = "usage_unknown"
					message = "模型未返回有效用量；保留费用预留"
				} else {
					hit := int64(0)
					if u.Cached != nil {
						hit = *u.Cached
					}
					if hit < 0 || hit > *u.Prompt {
						status = "usage_unknown"
						message = "缓存用量异常；保留费用预留"
					} else {
						price, b := c.priceFor(begin, time.Now())
						basis = b
						if u.Cached == nil {
							basis += "_cache_unknown"
						}
						charged := (*u.Prompt-hit)*price.Input + hit*price.Cached + *u.Completion*price.Output
						cost, prompt, cached, completion = charged, *u.Prompt, hit, *u.Completion
						status = "invalid_output"
						message = "解读格式、完整性或引用未通过校验；费用已按返回用量估算"
						if charged > reserve || *u.Completion > analysisOutputTokens {
							status = "reservation_exceeded"
							message = "用量超过本次预留上限，AI 已暂停，请核对价格与用量"
						} else if len(decoded.Choices) == 1 && decoded.Choices[0].Finish == "stop" {
							if v, validationErr := validateAIResult(decoded.Choices[0].Message.Content, j.Evidence); validationErr == nil {
								b, _ := json.Marshal(v)
								result = string(b)
								status = "succeeded"
								message = ""
							}
						}
					}
				}
			}
		}
	}
	// Persist even after request cancellation; a lost response must not release a reservation.
	tx, err := a.Store.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.Exec(`UPDATE ai_call_timings SET started_ms=?,completed_ms=? WHERE call_id=?`, startedMS, completedMS, callID); err != nil {
		return err
	}
	_, err = tx.Exec(`UPDATE ai_calls SET completed=?,status=?,cost_upper_nano=?,prompt_tokens=?,cached_tokens=?,completion_tokens=?,http_status=?,price_basis=? WHERE id=?`, time.Now().Unix(), status, cost, prompt, cached, completion, code, basis, callID)
	if err != nil {
		return err
	}
	_, err = tx.Exec(`UPDATE ai_jobs SET status=?,completed=?,result=?,error=?,expires=MAX(expires,?) WHERE id=?`, status, time.Now().Unix(), result, message, time.Now().AddDate(1, 0, 0).Unix(), j.ID)
	if err != nil {
		return err
	}
	if status != "succeeded" {
		_, err = tx.Exec(`UPDATE ai_settings SET not_before=? WHERE singleton=1`, time.Now().Add(5*time.Minute).Unix())
		if err != nil {
			return err
		}
	}
	if status == "reservation_exceeded" || code == 401 || code == 402 || code == 403 {
		_, err = tx.Exec(`UPDATE ai_settings SET enabled=0 WHERE singleton=1`)
		if err != nil {
			return err
		}
	}
	return tx.Commit()
}
