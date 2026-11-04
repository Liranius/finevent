package calendar

import (
	"encoding/json"
	"time"
)

// Claim one immediate retry of the exact pre-fix failure. Preserve its evidence
// rather than resetting the cursor or pretending the source has synchronized.
// The durable claim also prevents restarts from repeatedly bypassing the interval.
// TODO: Remove this compatibility retry after the repaired production release is
// accepted and the old binary is retired; retain its table as audit evidence.
func (s *Store) claimRolloverRetry(source Source) (bool, error) {
	if source.ID != "nyfed" || source.Error != "纽约联储当前页月份未更新，保留已有日程" || source.LastSuccess != 0 || source.Count != 0 {
		return false, nil
	}
	previous, err := json.Marshal(source)
	if err != nil {
		return false, err
	}
	result, err := s.DB.Exec(`INSERT OR IGNORE INTO calendar_source_repairs(id,source,previous_state,requested_at)
SELECT 'nyfed-early-month-rollover-v1',id,?,? FROM calendar_sources
WHERE id='nyfed' AND last_attempt=? AND last_success=0 AND count=0 AND error=?`, string(previous), time.Now().Unix(), source.LastAttempt, source.Error)
	if err != nil {
		return false, err
	}
	count, err := result.RowsAffected()
	return count == 1, err
}
