package probe

import "database/sql"

type claimedDelivery struct {
	ID, SubscriptionID, Expires, NewsID                   int64
	Payload, Receipt, Scenario, Key, EventID, Title, Body string
	Attempts                                              int
}

func (a *App) claimDelivery(now int64) (*claimedDelivery, error) {
	tx, err := a.Store.DB.Begin()
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	if _, err = tx.Exec(`UPDATE deliveries SET status='expired',receipt_token='' WHERE status IN ('queued','sending') AND expires<=?`, now); err != nil {
		return nil, err
	}
	d := &claimedDelivery{}
	var active int
	err = tx.QueryRow(`SELECT d.id,d.subscription_id,s.payload,s.active,d.receipt_token,d.scenario,d.expires,d.attempts,COALESCE(j.key,''),COALESCE(j.event_id,''),COALESCE(j.title,''),COALESCE(j.body,'') FROM deliveries d JOIN subscriptions s ON s.id=d.subscription_id LEFT JOIN calendar_reminder_jobs j ON j.delivery_id=d.id WHERE d.status='queued' AND d.due<=? AND d.expires>? ORDER BY d.due,d.id LIMIT 1`, now, now).Scan(&d.ID, &d.SubscriptionID, &d.Payload, &active, &d.Receipt, &d.Scenario, &d.Expires, &d.Attempts, &d.Key, &d.EventID, &d.Title, &d.Body)
	if err == sql.ErrNoRows {
		return nil, tx.Commit()
	}
	if err != nil {
		return nil, err
	}
	valid := active == 1
	if valid && d.Scenario == "calendar" {
		valid, err = validCalendarDelivery(tx, d, now)
		if err != nil {
			return nil, err
		}
	}
	if valid && d.Scenario == "news" {
		valid, err = validNewsDelivery(tx, d, now)
		if err != nil {
			return nil, err
		}
	}
	if !valid {
		state, message := "cancelled", "事件、关注或设备已变更"
		if d.Scenario != "calendar" && d.Scenario != "news" {
			state, message = "failed", "设备订阅已停用"
		}
		_, err = tx.Exec(`UPDATE deliveries SET status=?,receipt_token='',error=? WHERE id=?`, state, message, d.ID)
		if err != nil {
			return nil, err
		}
		return nil, tx.Commit()
	}
	_, err = tx.Exec(`UPDATE deliveries SET status='sending',attempts=attempts+1 WHERE id=?`, d.ID)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return d, nil
}
