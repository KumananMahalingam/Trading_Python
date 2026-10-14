package service

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"time"

	"github.com/tradingapp/trading-backend/internal/apperr"
	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/repository"
)

// AlertService replaces AlertService.java.
type AlertService struct {
	db     *sql.DB
	alerts *repository.AlertRepository
}

func NewAlertService(db *sql.DB, alerts *repository.AlertRepository) *AlertService {
	return &AlertService{db: db, alerts: alerts}
}

// Create stores a new ACTIVE alert for the user.
func (s *AlertService) Create(ctx context.Context, user *models.User, req dto.CreateAlertRequest) (*dto.AlertResponse, error) {
	alert := models.Alert{
		UserID:      user.ID,
		Ticker:      strings.ToUpper(req.Ticker),
		TargetPrice: *req.TargetPrice,
		Condition:   req.Condition,
		Status:      models.AlertActive,
		CreatedAt:   time.Now().UTC(),
	}

	if err := s.alerts.Insert(ctx, s.db, &alert); err != nil {
		return nil, err
	}

	response := ToAlertResponse(alert)
	return &response, nil
}

// List returns the user's alerts, newest first.
func (s *AlertService) List(ctx context.Context, user *models.User) ([]dto.AlertResponse, error) {
	alerts, err := s.alerts.FindByUser(ctx, s.db, user.ID)
	if err != nil {
		return nil, err
	}

	items := make([]dto.AlertResponse, 0, len(alerts))
	for _, a := range alerts {
		items = append(items, ToAlertResponse(a))
	}
	return items, nil
}

// Cancel marks an alert CANCELLED.
func (s *AlertService) Cancel(ctx context.Context, user *models.User, alertID int64) error {
	alert, err := s.ownedAlert(ctx, user, alertID, "cancel")
	if err != nil {
		return err
	}
	return s.alerts.UpdateStatus(ctx, s.db, alert.ID, models.AlertCancelled, alert.TriggeredAt)
}

// Delete removes an alert outright.
func (s *AlertService) Delete(ctx context.Context, user *models.User, alertID int64) error {
	alert, err := s.ownedAlert(ctx, user, alertID, "delete")
	if err != nil {
		return err
	}
	return s.alerts.DeleteByID(ctx, s.db, alert.ID)
}

// ownedAlert loads an alert and enforces that it belongs to the caller.
func (s *AlertService) ownedAlert(ctx context.Context, user *models.User, alertID int64, action string) (*models.Alert, error) {
	alert, err := s.alerts.FindByID(ctx, s.db, alertID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, apperr.NotFound("Alert not found")
		}
		return nil, err
	}

	if alert.UserID != user.ID {
		return nil, apperr.NotFound("Not authorized to " + action + " this alert")
	}
	return alert, nil
}

// ToAlertResponse converts an alert for the wire. It is exported because the
// alert checker publishes the same shape over WebSocket.
func ToAlertResponse(a models.Alert) dto.AlertResponse {
	return dto.AlertResponse{
		ID:          a.ID,
		Ticker:      a.Ticker,
		TargetPrice: a.TargetPrice,
		Condition:   a.Condition,
		Status:      a.Status,
		CreatedAt:   a.CreatedAt,
		TriggeredAt: a.TriggeredAt,
	}
}
