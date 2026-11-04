package probe

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"time"

	webpush "github.com/SherClockHolmes/webpush-go"
)

func (a *App) SendDue(ctx context.Context) error {
	now := time.Now().Unix()
	d, e := a.claimDelivery(now)
	if e != nil || d == nil {
		return e
	}
	id, subscriptionID, expires := d.ID, d.SubscriptionID, d.Expires
	payload, receipt, scenario, attempts := d.Payload, d.Receipt, d.Scenario, d.Attempts
	var sub webpush.Subscription
	if e = json.Unmarshal([]byte(payload), &sub); e != nil {
		_, e = a.Store.DB.Exec(`UPDATE deliveries SET status='failed',error='设备订阅数据无效',receipt_token='' WHERE id=?`, id)
		return e
	}
	title, body, target, topic := "finevent · 送达验证", "测试场景："+scenario+"。点击返回并记录实际显示情况。", "/finevent/?delivery="+fmt.Sprint(id), fmt.Sprintf("probe-%d", id)
	if d.Scenario == "calendar" && d.Key != "" {
		title = "finevent · " + d.Title
		body = d.Body
		target = "/finevent/#event=" + d.EventID
		topic = "cal-" + d.Key[:28]
	}
	if d.Scenario == "news" {
		title = "finevent · " + string([]rune(d.Title)[:min(len([]rune(d.Title)), 120)])
		body, target, topic = d.Body, fmt.Sprintf("/finevent/#news=%d", d.NewsID), "news-"+d.Key[:27]
	}
	message, _ := json.Marshal(map[string]any{"id": id, "expires": expires, "title": title, "body": body, "url": target, "receipt_token": receipt})
	response, e := webpush.SendNotificationWithContext(ctx, message, &sub, &webpush.Options{HTTPClient: a.Client, Subscriber: a.Config.Contact, VAPIDPublicKey: a.Config.VAPIDPublic, VAPIDPrivateKey: a.Config.VAPIDPrivate, TTL: int(expires - now), Urgency: webpush.UrgencyHigh, Topic: topic})
	status := 0
	if response != nil {
		status = response.StatusCode
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		response.Body.Close()
	}
	if e == nil && status >= 200 && status < 300 {
		_, e = a.Store.DB.Exec(`UPDATE deliveries SET status='accepted',accepted_at=?,error='',receipt_token='' WHERE id=?`, time.Now().Unix(), id)
		return e
	}
	if status == 404 || status == 410 {
		if _, e2 := a.Store.DB.Exec(`UPDATE subscriptions SET active=0 WHERE id=?`, subscriptionID); e2 != nil {
			return e2
		}
	}
	// Transport errors can mean an accepted request whose response was lost. The service worker uses a stable tag.
	messageError := fmt.Sprintf("push HTTP %d", status)
	if e != nil {
		messageError = "推送网络请求失败"
	}
	next := time.Now().Unix() + int64(15*(1<<min(attempts, 4)))
	if (status == 0 || status == 429 || status >= 500) && attempts < 4 && next < expires {
		_, e = a.Store.DB.Exec(`UPDATE deliveries SET status='queued',due=?,error=? WHERE id=?`, next, messageError, id)
	} else {
		_, e = a.Store.DB.Exec(`UPDATE deliveries SET status='failed',error=?,receipt_token='' WHERE id=?`, messageError, id)
	}
	return e
}
