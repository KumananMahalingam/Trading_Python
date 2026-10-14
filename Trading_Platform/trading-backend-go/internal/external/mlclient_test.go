package external

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

const testToken = "shared-secret"

const modelJSON = `{
  "ticker": "AAPL", "asset_type": "STOCK", "has_news": true, "status": "ready",
  "trained_at": "2026-10-14T16:41:52+00:00", "data_start": "2024-11-08", "data_end": "2026-10-13",
  "peers": ["MSFT"], "alpha_source": "groq", "news_articles": 3919,
  "prediction": {"as_of_date": "2026-10-13", "target_date": "2026-10-14", "last_close": 341.27,
    "predicted_return": -0.0003, "sigma": 0.002, "predicted_close": 341.17, "lower": 339.8,
    "upper": 342.5, "band_sigmas": 2.0, "direction": "DOWN", "generated_at": "2026-10-14T16:41:52+00:00",
    "model_trained_at": "2026-10-14T16:41:52+00:00"},
  "backtest": {"start": "2026-07-08", "end": "2026-10-13", "band_sigmas": 2.0,
    "points": [{"date": "2026-07-08", "base_close": 200.0, "actual_close": 201.0, "predicted_return": 0.001,
      "sigma": 0.01, "predicted_close": 200.2, "lower": 196.2, "upper": 204.2}],
    "metrics": {"n": 1, "directional_accuracy": 100.0, "up_precision": 100.0, "down_precision": null,
      "predicted_up_pct": 100.0, "majority_baseline": 100.0, "mae": 0.004}},
  "active_job": null,
  "estimates": {"train_seconds": 1267, "refresh_seconds": 67, "queue_seconds": 0}
}`

const jobJSON = `{"id": "0123456789abcdef0123456789abcdef", "ticker": "AAPL", "kind": "train", "status": "queued",
  "stage": null, "stage_progress": 0, "progress": 0, "message": null, "error": null, "queue_position": 2,
  "eta_seconds": 1300, "created_at": "2026-10-14T16:00:00+00:00", "started_at": null, "finished_at": null`

type recorded struct {
	method, path, token, contentType string
	body                             map[string]string
}

func newServer(t *testing.T, status int, body string, rec *recorded) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if rec != nil {
			rec.method, rec.path, rec.token = r.Method, r.URL.EscapedPath(), r.Header.Get("X-Internal-Token")
			rec.contentType = r.Header.Get("Content-Type")
			if raw, _ := io.ReadAll(r.Body); len(raw) > 0 {
				_ = json.Unmarshal(raw, &rec.body)
			}
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestGetModelDecodesAndSendsToken(t *testing.T) {
	var rec recorded
	srv := newServer(t, http.StatusOK, modelJSON, &rec)
	model, err := NewMLClient(srv.URL+"/", testToken, time.Second).GetModel(context.Background(), "AAPL")
	if err != nil {
		t.Fatalf("GetModel: %v", err)
	}
	if rec.method != http.MethodGet || rec.path != "/models/AAPL" || rec.token != testToken {
		t.Errorf("request = %s %s token %q", rec.method, rec.path, rec.token)
	}
	if model.Status != "ready" || model.AssetType != "STOCK" || !model.HasNews || *model.NewsArticles != 3919 {
		t.Errorf("model header fields wrong: %+v", model)
	}
	if model.Prediction == nil || model.Prediction.PredictedClose != 341.17 || model.Prediction.Direction != "DOWN" {
		t.Errorf("prediction = %+v", model.Prediction)
	}
	bt := model.Backtest
	if bt == nil || len(bt.Points) != 1 || bt.Points[0].ActualClose != 201.0 || bt.Metrics.DownPrecision != nil {
		t.Errorf("backtest = %+v", bt)
	}
	if model.Estimates.TrainSeconds != 1267 || model.ActiveJob != nil {
		t.Errorf("estimates/active job wrong: %+v %+v", model.Estimates, model.ActiveJob)
	}

	// re-encoded with the API's camelCase names
	raw, _ := json.Marshal(model)
	for _, key := range []string{`"assetType"`, `"predictedClose"`, `"actualClose"`, `"directionalAccuracy"`, `"trainSeconds"`} {
		if !strings.Contains(string(raw), key) {
			t.Errorf("encoded model missing %s: %s", key, raw)
		}
	}
}

func TestGetModelEscapesTicker(t *testing.T) {
	var rec recorded
	srv := newServer(t, http.StatusOK, modelJSON, &rec)
	_, _ = NewMLClient(srv.URL, testToken, time.Second).GetModel(context.Background(), "BRK.B/../x")
	if rec.path != "/models/BRK.B%2F..%2Fx" {
		t.Errorf("path = %s, want the ticker escaped as one segment", rec.path)
	}
}

func TestSubmitJob(t *testing.T) {
	for _, tc := range []struct {
		status  int
		created bool
	}{{http.StatusAccepted, true}, {http.StatusOK, false}} {
		var rec recorded
		srv := newServer(t, tc.status, jobJSON+`, "created": `+map[bool]string{true: "true", false: "false"}[tc.created]+`}`, &rec)
		job, created, err := NewMLClient(srv.URL, testToken, time.Second).SubmitJob(context.Background(), "AAPL", "train", "42")
		if err != nil {
			t.Fatalf("SubmitJob: %v", err)
		}
		if rec.method != http.MethodPost || rec.path != "/jobs" || rec.contentType != "application/json" {
			t.Errorf("request = %s %s %s", rec.method, rec.path, rec.contentType)
		}
		if rec.body["ticker"] != "AAPL" || rec.body["kind"] != "train" || rec.body["user_id"] != "42" {
			t.Errorf("body = %v", rec.body)
		}
		if created != tc.created || job.ID != "0123456789abcdef0123456789abcdef" || *job.QueuePosition != 2 {
			t.Errorf("created=%v job=%+v", created, job)
		}
	}
}

func TestErrorMapping(t *testing.T) {
	cases := []struct {
		name    string
		status  int
		body    string
		wantML  int // expected MLError status; 0 = ErrMLUnavailable
		wantMsg string
		hasJob  bool
	}{
		{"bad request", 400, `{"detail": "No trained model for NVDA yet; run a retrain first"}`, 400, "No trained model for NVDA", false},
		{"not found", 404, `{"detail": "Job not found"}`, 404, "Job not found", false},
		{"conflict", 409, `{"detail": "You already have a train job for AAPL in progress", "job": ` + jobJSON + `}}`, 409, "AAPL", true},
		{"bad token", 401, `{"detail": "Invalid or missing internal token"}`, 0, "", false},
		{"server error", 500, `oops`, 0, "", false},
		{"bad json", 200, `not json`, 0, "", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newServer(t, tc.status, tc.body, nil)
			_, err := NewMLClient(srv.URL, testToken, time.Second).GetJob(context.Background(), "x")
			var mlErr *MLError
			if tc.wantML == 0 {
				if !errors.Is(err, ErrMLUnavailable) {
					t.Fatalf("err = %v, want ErrMLUnavailable", err)
				}
				return
			}
			if !errors.As(err, &mlErr) || mlErr.Status != tc.wantML || !strings.Contains(mlErr.Detail, tc.wantMsg) {
				t.Fatalf("err = %#v", err)
			}
			if (mlErr.Job != nil) != tc.hasJob {
				t.Errorf("job = %+v, want present=%v", mlErr.Job, tc.hasJob)
			}
		})
	}
}

func TestUnreachableAndTimeout(t *testing.T) {
	// nothing listening
	_, err := NewMLClient("http://127.0.0.1:1", testToken, time.Second).GetModel(context.Background(), "AAPL")
	if !errors.Is(err, ErrMLUnavailable) {
		t.Errorf("connection refused: err = %v", err)
	}

	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(300 * time.Millisecond)
	}))
	defer slow.Close()
	_, err = NewMLClient(slow.URL, testToken, 50*time.Millisecond).GetModel(context.Background(), "AAPL")
	if !errors.Is(err, ErrMLUnavailable) {
		t.Errorf("timeout: err = %v", err)
	}
}
