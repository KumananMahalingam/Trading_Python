package external

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/tradingapp/trading-backend/internal/dto"
)

// ErrMLUnavailable means the ML service could not be reached or answered in an
// unexpected way (connection refused, timeout, 5xx, bad token, bad JSON).
var ErrMLUnavailable = errors.New("ml service unavailable")

// MLError is a 4xx answer from the ML service whose Detail is safe to show to
// users. Job is set on 409 (the user's job already in progress).
type MLError struct {
	Status int
	Detail string
	Job    *dto.PredictionJob
}

func (e *MLError) Error() string { return fmt.Sprintf("ml service %d: %s", e.Status, e.Detail) }

// MLClient talks to the local Python prediction service (ml_service/app.py).
type MLClient struct {
	baseURL string
	token   string
	http    *http.Client
}

// NewMLClient builds a client. The timeout bounds every request; the ML
// service answers from disk and SQLite, so a few seconds is plenty.
func NewMLClient(baseURL, token string, timeout time.Duration) *MLClient {
	return &MLClient{
		baseURL: strings.TrimRight(baseURL, "/"),
		token:   token,
		http:    &http.Client{Timeout: timeout},
	}
}

// GetModel returns the stored prediction, backtest and active job for ticker.
func (c *MLClient) GetModel(ctx context.Context, ticker string) (*dto.PredictionModel, error) {
	var out mlModel
	if _, err := c.do(ctx, http.MethodGet, "/models/"+url.PathEscape(ticker), nil, &out); err != nil {
		return nil, err
	}
	return out.toDTO(), nil
}

// SubmitJob queues a job. created is false when an identical job was joined.
func (c *MLClient) SubmitJob(ctx context.Context, ticker, kind, userID string) (*dto.PredictionJob, bool, error) {
	body := map[string]string{"ticker": ticker, "kind": kind, "user_id": userID}
	var out struct {
		mlJob
		Created bool `json:"created"`
	}
	if _, err := c.do(ctx, http.MethodPost, "/jobs", body, &out); err != nil {
		return nil, false, err
	}
	job := out.mlJob.toDTO()
	return &job, out.Created, nil
}

// GetJob returns a job's status and progress.
func (c *MLClient) GetJob(ctx context.Context, id string) (*dto.PredictionJob, error) {
	var out mlJob
	if _, err := c.do(ctx, http.MethodGet, "/jobs/"+url.PathEscape(id), nil, &out); err != nil {
		return nil, err
	}
	job := out.toDTO()
	return &job, nil
}

func (c *MLClient) do(ctx context.Context, method, path string, body, out any) (int, error) {
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return 0, fmt.Errorf("encode request: %w", err)
		}
		reader = bytes.NewReader(raw)
	}

	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, reader)
	if err != nil {
		return 0, fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("X-Internal-Token", c.token)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return 0, fmt.Errorf("%w: %v", ErrMLUnavailable, err)
	}
	defer resp.Body.Close()

	raw, err := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes))
	if err != nil {
		return resp.StatusCode, fmt.Errorf("%w: read response: %v", ErrMLUnavailable, err)
	}

	switch {
	case resp.StatusCode >= 200 && resp.StatusCode < 300:
		if err := json.Unmarshal(raw, out); err != nil {
			return resp.StatusCode, fmt.Errorf("%w: decode response: %v", ErrMLUnavailable, err)
		}
		return resp.StatusCode, nil
	case resp.StatusCode == http.StatusBadRequest, resp.StatusCode == http.StatusNotFound,
		resp.StatusCode == http.StatusConflict:
		var payload struct {
			Detail string `json:"detail"`
			Job    *mlJob `json:"job"`
		}
		_ = json.Unmarshal(raw, &payload)
		mlErr := &MLError{Status: resp.StatusCode, Detail: payload.Detail}
		if payload.Job != nil {
			job := payload.Job.toDTO()
			mlErr.Job = &job
		}
		return resp.StatusCode, mlErr
	default:
		// 401 here means the shared token is misconfigured; callers treat it
		// like any other outage rather than leaking it to users.
		return resp.StatusCode, fmt.Errorf("%w: status %d", ErrMLUnavailable, resp.StatusCode)
	}
}

// ---------------------------------------------------------------------------
// Wire format of the ML service (snake_case). The structs have the same
// fields as their dto counterparts so they convert directly; Go ignores tags
// in struct conversions.
// ---------------------------------------------------------------------------

type mlPrediction struct {
	AsOfDate        string  `json:"as_of_date"`
	TargetDate      string  `json:"target_date"`
	LastClose       float64 `json:"last_close"`
	PredictedReturn float64 `json:"predicted_return"`
	Sigma           float64 `json:"sigma"`
	PredictedClose  float64 `json:"predicted_close"`
	Lower           float64 `json:"lower"`
	Upper           float64 `json:"upper"`
	BandSigmas      float64 `json:"band_sigmas"`
	Direction       string  `json:"direction"`
	GeneratedAt     string  `json:"generated_at"`
	ModelTrainedAt  *string `json:"model_trained_at"`
}

type mlBacktestPoint struct {
	Date            string  `json:"date"`
	BaseClose       float64 `json:"base_close"`
	ActualClose     float64 `json:"actual_close"`
	PredictedReturn float64 `json:"predicted_return"`
	Sigma           float64 `json:"sigma"`
	PredictedClose  float64 `json:"predicted_close"`
	Lower           float64 `json:"lower"`
	Upper           float64 `json:"upper"`
}

type mlBacktestMetrics struct {
	N                   int      `json:"n"`
	DirectionalAccuracy *float64 `json:"directional_accuracy"`
	UpPrecision         *float64 `json:"up_precision"`
	DownPrecision       *float64 `json:"down_precision"`
	PredictedUpPct      *float64 `json:"predicted_up_pct"`
	MajorityBaseline    *float64 `json:"majority_baseline"`
	MAE                 *float64 `json:"mae"`
}

type mlBacktest struct {
	Start      *string           `json:"start"`
	End        *string           `json:"end"`
	BandSigmas float64           `json:"band_sigmas"`
	Points     []mlBacktestPoint `json:"points"`
	Metrics    mlBacktestMetrics `json:"metrics"`
}

type mlJob struct {
	ID            string  `json:"id"`
	Ticker        string  `json:"ticker"`
	Kind          string  `json:"kind"`
	Status        string  `json:"status"`
	Stage         *string `json:"stage"`
	StageProgress float64 `json:"stage_progress"`
	Progress      float64 `json:"progress"`
	Message       *string `json:"message"`
	Error         *string `json:"error"`
	QueuePosition *int    `json:"queue_position"`
	EtaSeconds    *int    `json:"eta_seconds"`
	CreatedAt     string  `json:"created_at"`
	StartedAt     *string `json:"started_at"`
	FinishedAt    *string `json:"finished_at"`
}

type mlEstimates struct {
	TrainSeconds   int `json:"train_seconds"`
	RefreshSeconds int `json:"refresh_seconds"`
	QueueSeconds   int `json:"queue_seconds"`
}

type mlModel struct {
	Ticker       string        `json:"ticker"`
	AssetType    string        `json:"asset_type"`
	HasNews      bool          `json:"has_news"`
	Status       string        `json:"status"`
	TrainedAt    *string       `json:"trained_at"`
	DataStart    *string       `json:"data_start"`
	DataEnd      *string       `json:"data_end"`
	Peers        []string      `json:"peers"`
	AlphaSource  *string       `json:"alpha_source"`
	NewsArticles *int          `json:"news_articles"`
	Prediction   *mlPrediction `json:"prediction"`
	Backtest     *mlBacktest   `json:"backtest"`
	ActiveJob    *mlJob        `json:"active_job"`
	Estimates    mlEstimates   `json:"estimates"`
}

func (j mlJob) toDTO() dto.PredictionJob { return dto.PredictionJob(j) }

func (m mlModel) toDTO() *dto.PredictionModel {
	out := &dto.PredictionModel{
		Ticker:       m.Ticker,
		AssetType:    m.AssetType,
		HasNews:      m.HasNews,
		Status:       m.Status,
		TrainedAt:    m.TrainedAt,
		DataStart:    m.DataStart,
		DataEnd:      m.DataEnd,
		Peers:        m.Peers,
		AlphaSource:  m.AlphaSource,
		NewsArticles: m.NewsArticles,
		Estimates:    dto.PredictionEstimates(m.Estimates),
	}
	if out.Peers == nil {
		out.Peers = []string{}
	}
	if m.Prediction != nil {
		p := dto.NextDayPrediction(*m.Prediction)
		out.Prediction = &p
	}
	if m.Backtest != nil {
		points := make([]dto.BacktestPoint, len(m.Backtest.Points))
		for i, pt := range m.Backtest.Points {
			points[i] = dto.BacktestPoint(pt)
		}
		out.Backtest = &dto.Backtest{
			Start:      m.Backtest.Start,
			End:        m.Backtest.End,
			BandSigmas: m.Backtest.BandSigmas,
			Points:     points,
			Metrics:    dto.BacktestMetrics(m.Backtest.Metrics),
		}
	}
	if m.ActiveJob != nil {
		job := m.ActiveJob.toDTO()
		out.ActiveJob = &job
	}
	return out
}
