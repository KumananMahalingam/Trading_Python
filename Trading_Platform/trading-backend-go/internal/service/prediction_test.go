package service

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"testing"

	"github.com/tradingapp/trading-backend/internal/apperr"
	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/external"
	"github.com/tradingapp/trading-backend/internal/models"
)

type fakeSymbols map[string]models.AssetType

func (f fakeSymbols) GetByTicker(_ context.Context, ticker string) (*models.Symbol, error) {
	if at, ok := f[ticker]; ok {
		return &models.Symbol{Ticker: ticker, AssetType: at}, nil
	}
	return nil, apperr.NotFound("Symbol not found: " + ticker)
}

type fakeML struct {
	err       error
	gotTicker string
	gotKind   string
	gotUser   string
	gotJobID  string
	created   bool
}

func (f *fakeML) GetModel(_ context.Context, ticker string) (*dto.PredictionModel, error) {
	f.gotTicker = ticker
	if f.err != nil {
		return nil, f.err
	}
	return &dto.PredictionModel{Ticker: ticker, Status: "none"}, nil
}

func (f *fakeML) SubmitJob(_ context.Context, ticker, kind, userID string) (*dto.PredictionJob, bool, error) {
	f.gotTicker, f.gotKind, f.gotUser = ticker, kind, userID
	if f.err != nil {
		return nil, false, f.err
	}
	return &dto.PredictionJob{ID: "j", Ticker: ticker, Kind: kind, Status: "queued"}, f.created, nil
}

func (f *fakeML) GetJob(_ context.Context, id string) (*dto.PredictionJob, error) {
	f.gotJobID = id
	if f.err != nil {
		return nil, f.err
	}
	return &dto.PredictionJob{ID: id, Status: "running"}, nil
}

var symbols = fakeSymbols{"AAPL": models.AssetStock, "BTCUSDT": models.AssetCrypto, "EURUSD": models.AssetForex}
var user = &models.User{ID: 42}

const jobID = "0123456789abcdef0123456789abcdef"

func statusOf(t *testing.T, err error) int {
	t.Helper()
	var appErr *apperr.Error
	if !errors.As(err, &appErr) {
		t.Fatalf("err = %#v, want *apperr.Error", err)
	}
	return appErr.Status
}

func TestGetPredictionUppercasesAndChecksSymbol(t *testing.T) {
	ml := &fakeML{}
	s := NewPredictionService(ml, symbols)

	model, err := s.GetPrediction(context.Background(), " aapl ")
	if err != nil || model.Ticker != "AAPL" || ml.gotTicker != "AAPL" {
		t.Fatalf("model=%+v err=%v ticker=%q", model, err, ml.gotTicker)
	}
	if _, err := s.GetPrediction(context.Background(), "btcusdt"); err != nil {
		t.Errorf("crypto should be allowed: %v", err)
	}
	if got := statusOf(t, func() error { _, err := s.GetPrediction(context.Background(), "NOPE"); return err }()); got != http.StatusNotFound {
		t.Errorf("unknown symbol status = %d, want 404", got)
	}
	if got := statusOf(t, func() error { _, err := s.GetPrediction(context.Background(), "EURUSD"); return err }()); got != http.StatusBadRequest {
		t.Errorf("forex status = %d, want 400", got)
	}
}

func TestStartJobPassesUserID(t *testing.T) {
	ml := &fakeML{created: true}
	job, created, err := NewPredictionService(ml, symbols).StartJob(context.Background(), "aapl", "refresh", user)
	if err != nil || !created || job.Kind != "refresh" {
		t.Fatalf("job=%+v created=%v err=%v", job, created, err)
	}
	if ml.gotUser != "42" || ml.gotTicker != "AAPL" || ml.gotKind != "refresh" {
		t.Errorf("forwarded %q %q %q", ml.gotTicker, ml.gotKind, ml.gotUser)
	}
}

func TestStartJobValidation(t *testing.T) {
	s := NewPredictionService(&fakeML{}, symbols)
	if got := statusOf(t, func() error { _, _, err := s.StartJob(context.Background(), "AAPL", "train", nil); return err }()); got != http.StatusUnauthorized {
		t.Errorf("no user status = %d", got)
	}
	if got := statusOf(t, func() error { _, _, err := s.StartJob(context.Background(), "AAPL", "delete", user); return err }()); got != http.StatusBadRequest {
		t.Errorf("bad kind status = %d", got)
	}
}

func TestErrorTranslation(t *testing.T) {
	existing := dto.PredictionJob{ID: jobID, Ticker: "MSFT", Kind: "train"}
	cases := []struct {
		name string
		err  error
		want int // 0 = JobConflictError
	}{
		{"conflict", &external.MLError{Status: 409, Detail: "You already have a train job for MSFT in progress", Job: &existing}, 0},
		{"bad request", &external.MLError{Status: 400, Detail: "No trained model for AAPL yet"}, http.StatusBadRequest},
		{"not found", &external.MLError{Status: 404, Detail: "Job not found"}, http.StatusNotFound},
		{"unavailable", fmt.Errorf("%w: connection refused", external.ErrMLUnavailable), http.StatusServiceUnavailable},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, _, err := NewPredictionService(&fakeML{err: tc.err}, symbols).StartJob(context.Background(), "AAPL", "train", user)
			if tc.want == 0 {
				var conflict *JobConflictError
				if !errors.As(err, &conflict) || conflict.Job.ID != jobID || conflict.Message == "" {
					t.Fatalf("err = %#v, want JobConflictError with the existing job", err)
				}
				return
			}
			if got := statusOf(t, err); got != tc.want {
				t.Errorf("status = %d, want %d", got, tc.want)
			}
		})
	}
}

func TestUnavailableMessageHidesInternals(t *testing.T) {
	err := fmt.Errorf("%w: dial tcp 127.0.0.1:8000: refused", external.ErrMLUnavailable)
	_, getErr := NewPredictionService(&fakeML{err: err}, symbols).GetPrediction(context.Background(), "AAPL")
	var appErr *apperr.Error
	if !errors.As(getErr, &appErr) || appErr.Message != "Prediction service unavailable" {
		t.Errorf("err = %#v", getErr)
	}
}

func TestGetJobValidatesID(t *testing.T) {
	ml := &fakeML{}
	s := NewPredictionService(ml, symbols)
	for _, id := range []string{"", "nope", "../../models/AAPL", jobID + "0"} {
		if got := statusOf(t, func() error { _, err := s.GetJob(context.Background(), id); return err }()); got != http.StatusNotFound {
			t.Errorf("id %q status = %d, want 404", id, got)
		}
	}
	if ml.gotJobID != "" {
		t.Errorf("invalid ids must not reach the ML service, got %q", ml.gotJobID)
	}
	if job, err := s.GetJob(context.Background(), jobID); err != nil || job.ID != jobID {
		t.Errorf("job=%+v err=%v", job, err)
	}
}

func TestNotConfigured(t *testing.T) {
	s := NewPredictionService(nil, symbols)
	if got := statusOf(t, func() error { _, err := s.GetPrediction(context.Background(), "AAPL"); return err }()); got != http.StatusServiceUnavailable {
		t.Errorf("GetPrediction status = %d", got)
	}
	if got := statusOf(t, func() error { _, _, err := s.StartJob(context.Background(), "AAPL", "train", user); return err }()); got != http.StatusServiceUnavailable {
		t.Errorf("StartJob status = %d", got)
	}
	if got := statusOf(t, func() error { _, err := s.GetJob(context.Background(), jobID); return err }()); got != http.StatusServiceUnavailable {
		t.Errorf("GetJob status = %d", got)
	}
}
