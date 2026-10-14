package service

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"regexp"
	"strconv"
	"strings"

	"github.com/tradingapp/trading-backend/internal/apperr"
	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/external"
	"github.com/tradingapp/trading-backend/internal/models"
)

// PredictionClient is the subset of external.MLClient the service uses.
type PredictionClient interface {
	GetModel(ctx context.Context, ticker string) (*dto.PredictionModel, error)
	SubmitJob(ctx context.Context, ticker, kind, userID string) (*dto.PredictionJob, bool, error)
	GetJob(ctx context.Context, id string) (*dto.PredictionJob, error)
}

// SymbolLookup resolves a ticker to a known symbol (SymbolService satisfies it).
type SymbolLookup interface {
	GetByTicker(ctx context.Context, ticker string) (*models.Symbol, error)
}

// JobConflictError is returned when the user already has a different job in
// progress. Job is that job.
type JobConflictError struct {
	Message string
	Job     dto.PredictionJob
}

func (e *JobConflictError) Error() string { return e.Message }

var jobIDPattern = regexp.MustCompile(`^[0-9a-f]{32}$`)

// PredictionService fronts the local ML service. Users are authenticated here;
// the ML service only trusts the shared token and the user id passed along.
type PredictionService struct {
	client  PredictionClient
	symbols SymbolLookup
}

// NewPredictionService builds the service. A nil client means the ML service
// is not configured; every call then answers 503.
func NewPredictionService(client PredictionClient, symbols SymbolLookup) *PredictionService {
	return &PredictionService{client: client, symbols: symbols}
}

// GetPrediction returns the stored prediction, backtest and any active job.
func (s *PredictionService) GetPrediction(ctx context.Context, ticker string) (*dto.PredictionModel, error) {
	upper, err := s.checkTicker(ctx, ticker)
	if err != nil {
		return nil, err
	}
	model, err := s.client.GetModel(ctx, upper)
	if err != nil {
		return nil, translate(err, "get prediction")
	}
	return model, nil
}

// StartJob queues a train or refresh run for ticker on behalf of user, or
// joins the identical job that is already queued.
func (s *PredictionService) StartJob(ctx context.Context, ticker, kind string, user *models.User) (*dto.PredictionJob, bool, error) {
	if user == nil {
		return nil, false, apperr.Unauthorized("Not authenticated")
	}
	if kind != "train" && kind != "refresh" {
		return nil, false, apperr.BadRequest("kind: Kind must be train or refresh")
	}
	upper, err := s.checkTicker(ctx, ticker)
	if err != nil {
		return nil, false, err
	}
	job, created, err := s.client.SubmitJob(ctx, upper, kind, strconv.FormatInt(user.ID, 10))
	if err != nil {
		return nil, false, translate(err, "start prediction job")
	}
	return job, created, nil
}

// GetJob returns a job's progress. Job ids are unguessable random hex strings.
func (s *PredictionService) GetJob(ctx context.Context, id string) (*dto.PredictionJob, error) {
	if s.client == nil {
		return nil, notConfigured()
	}
	if !jobIDPattern.MatchString(id) {
		return nil, apperr.NotFound("Prediction job not found")
	}
	job, err := s.client.GetJob(ctx, id)
	if err != nil {
		return nil, translate(err, "get prediction job")
	}
	return job, nil
}

func (s *PredictionService) checkTicker(ctx context.Context, ticker string) (string, error) {
	if s.client == nil {
		return "", notConfigured()
	}
	upper := strings.ToUpper(strings.TrimSpace(ticker))
	symbol, err := s.symbols.GetByTicker(ctx, upper)
	if err != nil {
		return "", err // apperr.NotFound for unknown symbols
	}
	if symbol.AssetType != models.AssetStock && symbol.AssetType != models.AssetCrypto {
		return "", apperr.BadRequest("Predictions are not available for " + upper)
	}
	return upper, nil
}

func notConfigured() error {
	return apperr.New(http.StatusServiceUnavailable, "Prediction service is not configured")
}

// translate maps ML client errors onto user-facing HTTP errors.
func translate(err error, op string) error {
	var mlErr *external.MLError
	if errors.As(err, &mlErr) {
		switch mlErr.Status {
		case http.StatusConflict:
			conflict := &JobConflictError{Message: mlErr.Detail}
			if mlErr.Job != nil {
				conflict.Job = *mlErr.Job
			}
			return conflict
		case http.StatusNotFound:
			return apperr.NotFound("Prediction job not found")
		default:
			return apperr.BadRequest(mlErr.Detail)
		}
	}
	slog.Error("prediction service call failed", "op", op, "error", err)
	return apperr.New(http.StatusServiceUnavailable, "Prediction service unavailable")
}
