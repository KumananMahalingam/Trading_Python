package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/storage"
)

// AlertRepository replaces AlertRepository.java.
type AlertRepository struct{}

func NewAlertRepository() *AlertRepository { return &AlertRepository{} }

const alertColumns = `id, user_id, ticker, target_price::text, condition, status, created_at, triggered_at`

func scanAlert(row interface{ Scan(...any) error }) (*models.Alert, error) {
	var (
		a           models.Alert
		targetPrice string
		triggeredAt sql.NullTime
	)
	if err := row.Scan(&a.ID, &a.UserID, &a.Ticker, &targetPrice,
		&a.Condition, &a.Status, &a.CreatedAt, &triggeredAt); err != nil {
		return nil, err
	}

	price, err := parseDecimal(targetPrice)
	if err != nil {
		return nil, fmt.Errorf("parse alert target price: %w", err)
	}
	a.TargetPrice = price

	// Normalise to UTC so the JSON matches the Instants Java emitted.
	a.CreatedAt = a.CreatedAt.UTC()

	if triggeredAt.Valid {
		t := triggeredAt.Time.UTC()
		a.TriggeredAt = &t
	}

	return &a, nil
}

// FindByUser mirrors findByUserOrderByCreatedAtDesc.
func (r *AlertRepository) FindByUser(ctx context.Context, q storage.Querier, userID int64) ([]models.Alert, error) {
	rows, err := q.QueryContext(ctx,
		`SELECT `+alertColumns+` FROM alerts WHERE user_id = $1 ORDER BY created_at DESC`, userID)
	if err != nil {
		return nil, fmt.Errorf("find alerts by user: %w", err)
	}
	defer rows.Close()

	return collectAlerts(rows)
}

// FindByTickerAndStatus mirrors findByTickerAndStatus.
func (r *AlertRepository) FindByTickerAndStatus(ctx context.Context, q storage.Querier, ticker string, status models.AlertStatus) ([]models.Alert, error) {
	rows, err := q.QueryContext(ctx,
		`SELECT `+alertColumns+` FROM alerts WHERE ticker = $1 AND status = $2`, ticker, string(status))
	if err != nil {
		return nil, fmt.Errorf("find alerts by ticker and status: %w", err)
	}
	defer rows.Close()

	return collectAlerts(rows)
}

// FindByID mirrors findById.
func (r *AlertRepository) FindByID(ctx context.Context, q storage.Querier, id int64) (*models.Alert, error) {
	row := q.QueryRowContext(ctx, `SELECT `+alertColumns+` FROM alerts WHERE id = $1`, id)

	alert, err := scanAlert(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("find alert by id: %w", err)
	}
	return alert, nil
}

// Insert persists a new alert, stamping created_at like @PrePersist did.
func (r *AlertRepository) Insert(ctx context.Context, q storage.Querier, a *models.Alert) error {
	if a.Status == "" {
		a.Status = models.AlertActive
	}

	err := q.QueryRowContext(ctx,
		`INSERT INTO alerts (user_id, ticker, target_price, condition, status, created_at, triggered_at)
		 VALUES ($1, $2, $3::text::numeric, $4, $5, $6, $7)
		 RETURNING id`,
		a.UserID, a.Ticker, a.TargetPrice.String(), string(a.Condition), string(a.Status),
		a.CreatedAt, a.TriggeredAt).Scan(&a.ID)
	if err != nil {
		return fmt.Errorf("insert alert: %w", err)
	}
	return nil
}

// UpdateStatus persists a status transition (cancel or trigger).
func (r *AlertRepository) UpdateStatus(ctx context.Context, q storage.Querier, id int64, status models.AlertStatus, triggeredAt *time.Time) error {
	_, err := q.ExecContext(ctx,
		`UPDATE alerts SET status = $1, triggered_at = $2 WHERE id = $3`,
		string(status), triggeredAt, id)
	if err != nil {
		return fmt.Errorf("update alert status: %w", err)
	}
	return nil
}

// DeleteByID mirrors deleteById.
func (r *AlertRepository) DeleteByID(ctx context.Context, q storage.Querier, id int64) error {
	if _, err := q.ExecContext(ctx, `DELETE FROM alerts WHERE id = $1`, id); err != nil {
		return fmt.Errorf("delete alert: %w", err)
	}
	return nil
}

func collectAlerts(rows *sql.Rows) ([]models.Alert, error) {
	alerts := make([]models.Alert, 0)
	for rows.Next() {
		a, err := scanAlert(rows)
		if err != nil {
			return nil, fmt.Errorf("scan alert: %w", err)
		}
		alerts = append(alerts, *a)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate alerts: %w", err)
	}
	return alerts, nil
}
