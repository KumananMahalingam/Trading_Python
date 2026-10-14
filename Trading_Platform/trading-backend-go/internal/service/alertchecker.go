package service

import (
	"context"
	"database/sql"
	"fmt"
	"log/slog"
	"time"

	"github.com/shopspring/decimal"

	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/repository"
)

// Publisher is the subset of the WebSocket broker the services need.
type Publisher interface {
	Publish(destination string, payload any)
}

// AlertCheckerService replaces AlertCheckerService.java. It runs on every
// incoming price tick, so it stays allocation-light and returns quickly.
type AlertCheckerService struct {
	db        *sql.DB
	alerts    *repository.AlertRepository
	publisher Publisher
}

func NewAlertCheckerService(db *sql.DB, alerts *repository.AlertRepository, publisher Publisher) *AlertCheckerService {
	return &AlertCheckerService{db: db, alerts: alerts, publisher: publisher}
}

// Check triggers any ACTIVE alert whose condition the price now satisfies.
func (s *AlertCheckerService) Check(ctx context.Context, ticker string, currentPrice decimal.Decimal) {
	active, err := s.alerts.FindByTickerAndStatus(ctx, s.db, ticker, models.AlertActive)
	if err != nil {
		slog.Error("failed to load active alerts", "ticker", ticker, "error", err)
		return
	}

	for _, alert := range active {
		if !shouldTrigger(alert, currentPrice) {
			continue
		}

		triggeredAt := time.Now().UTC()
		if err := s.alerts.UpdateStatus(ctx, s.db, alert.ID, models.AlertTriggered, &triggeredAt); err != nil {
			slog.Error("failed to mark alert triggered", "alertId", alert.ID, "error", err)
			continue
		}

		alert.Status = models.AlertTriggered
		alert.TriggeredAt = &triggeredAt

		s.publisher.Publish(fmt.Sprintf("/topic/alerts/%d", alert.UserID), ToAlertResponse(alert))

		slog.Info("alert triggered",
			"ticker", ticker,
			"condition", alert.Condition,
			"target", alert.TargetPrice.String(),
			"price", currentPrice.String(),
		)
	}
}

func shouldTrigger(alert models.Alert, price decimal.Decimal) bool {
	switch alert.Condition {
	case models.ConditionAbove:
		return price.GreaterThanOrEqual(alert.TargetPrice)
	case models.ConditionBelow:
		return price.LessThanOrEqual(alert.TargetPrice)
	default:
		return false
	}
}
