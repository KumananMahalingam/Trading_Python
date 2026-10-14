package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/tradingapp/trading-backend/internal/config"
	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/external"
	"github.com/tradingapp/trading-backend/internal/handler"
	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/security"
	"github.com/tradingapp/trading-backend/internal/service"
	"github.com/tradingapp/trading-backend/internal/ws"
)

const testSecret = "test-secret-that-is-at-least-32-bytes-long"

// stubLoader resolves any token subject to a fixed user.
type stubLoader struct{}

func (stubLoader) FindUserByEmail(context.Context, string) (*models.User, error) {
	return &models.User{ID: 1, Username: "tester", Email: "tester@example.com"}, nil
}

func newTestRouter(t *testing.T) (http.Handler, *security.TokenService) {
	t.Helper()
	gin.SetMode(gin.TestMode)

	cfg := &config.Config{
		Port:            "8080",
		JWTSecret:       testSecret,
		AccessTokenTTL:  time.Hour,
		RefreshTokenTTL: 24 * time.Hour,
		AllowedOrigins:  []string{"http://localhost:5173"},
	}

	tokens := security.NewTokenService(cfg.JWTSecret, cfg.AccessTokenTTL, cfg.RefreshTokenTTL)

	// Services are not exercised by these routing assertions. The prediction
	// service is real but backed by a fake ML client, so routing can be checked
	// end to end.
	handlers := Handlers{
		Auth:       handler.NewAuthHandler(nil),
		Market:     handler.NewMarketHandler(nil, nil, nil),
		Account:    handler.NewAccountHandler(nil, nil, nil, nil),
		Prediction: handler.NewPredictionHandler(service.NewPredictionService(routeML{}, routeSymbols{})),
	}

	// A route conflict would panic here rather than returning.
	return New(cfg, tokens, stubLoader{}, handlers, ws.NewBroker()), tokens
}

func TestHealthEndpoint(t *testing.T) {
	router, _ := newTestRouter(t)

	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))

	if rec.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rec.Code)
	}
}

func TestProtectedRouteRejectsMissingToken(t *testing.T) {
	router, _ := newTestRouter(t)

	for _, path := range []string{"/api/portfolio", "/api/watchlist", "/api/alerts", "/api/user/me"} {
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, path, nil))

		if rec.Code != http.StatusUnauthorized {
			t.Errorf("%s status = %d, want 401", path, rec.Code)
		}
	}
}

func TestAuthRoutesArePublic(t *testing.T) {
	router, _ := newTestRouter(t)

	// An empty body fails validation, which proves the request reached the
	// handler instead of being rejected by the auth middleware.
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader("{}"))
	req.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400 (validation), body = %s", rec.Code, rec.Body.String())
	}
}

// TestSymbolSearchRouteWinsOverTickerWildcard guards the ordering of
// /api/symbols/search against /api/symbols/:ticker.
func TestSymbolSearchRouteWinsOverTickerWildcard(t *testing.T) {
	router, tokens := newTestRouter(t)

	token, err := tokens.GenerateAccessToken("tester@example.com")
	if err != nil {
		t.Fatalf("failed to mint token: %v", err)
	}

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/symbols/search?q=", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	router.ServeHTTP(rec, req)

	// The search handler rejects an empty q with 400 before touching any
	// service. Routing to the :ticker handler instead would hit a nil service
	// and surface as 500.
	if rec.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400; search route was not matched. body = %s", rec.Code, rec.Body.String())
	}
}

// TestCORSPreflightAllowsAuthorizedRequests is the check that the browser will
// actually let the frontend send its bearer token cross-origin.
func TestCORSPreflightAllowsAuthorizedRequests(t *testing.T) {
	router, _ := newTestRouter(t)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodOptions, "/api/portfolio", nil)
	req.Header.Set("Origin", "http://localhost:5173")
	req.Header.Set("Access-Control-Request-Method", http.MethodGet)
	req.Header.Set("Access-Control-Request-Headers", "authorization")
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusNoContent && rec.Code != http.StatusOK {
		t.Fatalf("preflight status = %d, want 204 or 200", rec.Code)
	}

	if origin := rec.Header().Get("Access-Control-Allow-Origin"); origin != "http://localhost:5173" {
		t.Errorf("Access-Control-Allow-Origin = %q, want the request origin", origin)
	}
	if credentials := rec.Header().Get("Access-Control-Allow-Credentials"); credentials != "true" {
		t.Errorf("Access-Control-Allow-Credentials = %q, want true", credentials)
	}

	allowedHeaders := strings.ToLower(rec.Header().Get("Access-Control-Allow-Headers"))
	if !strings.Contains(allowedHeaders, "authorization") {
		t.Errorf("Access-Control-Allow-Headers = %q, must include authorization", allowedHeaders)
	}
}

func TestCORSRejectsUnknownOrigin(t *testing.T) {
	router, _ := newTestRouter(t)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	req.Header.Set("Origin", "http://evil.example.com")
	router.ServeHTTP(rec, req)

	if origin := rec.Header().Get("Access-Control-Allow-Origin"); origin != "" {
		t.Errorf("Access-Control-Allow-Origin = %q, want empty for a disallowed origin", origin)
	}
}

func TestSockJSEndpointIsMounted(t *testing.T) {
	router, _ := newTestRouter(t)

	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/ws/info", nil))

	if rec.Code != http.StatusOK {
		t.Errorf("/ws/info status = %d, want 200", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "websocket") {
		t.Errorf("/ws/info body does not look like SockJS info: %s", rec.Body.String())
	}
}

// --- predictions -------------------------------------------------------------

const routeJobID = "0123456789abcdef0123456789abcdef"

type routeSymbols struct{}

func (routeSymbols) GetByTicker(_ context.Context, ticker string) (*models.Symbol, error) {
	return &models.Symbol{Ticker: ticker, AssetType: models.AssetStock}, nil
}

// routeML answers with values that identify which ML call was made.
type routeML struct{}

func (routeML) GetModel(_ context.Context, ticker string) (*dto.PredictionModel, error) {
	return &dto.PredictionModel{Ticker: ticker, Status: "model", Peers: []string{}}, nil
}

func (routeML) SubmitJob(_ context.Context, ticker, kind, userID string) (*dto.PredictionJob, bool, error) {
	if ticker == "MSFT" {
		return nil, false, &external.MLError{Status: http.StatusConflict, Detail: "You already have a train job for AAPL in progress",
			Job: &dto.PredictionJob{ID: routeJobID, Ticker: "AAPL", Kind: "train", Status: "running"}}
	}
	return &dto.PredictionJob{ID: routeJobID, Ticker: ticker, Kind: kind, Status: "submitted-by-" + userID}, ticker == "AAPL", nil
}

func (routeML) GetJob(_ context.Context, id string) (*dto.PredictionJob, error) {
	return &dto.PredictionJob{ID: id, Status: "job"}, nil
}

func authed(t *testing.T, tokens *security.TokenService, method, path, body string) *http.Request {
	t.Helper()
	token, err := tokens.GenerateAccessToken("tester@example.com")
	if err != nil {
		t.Fatalf("mint token: %v", err)
	}
	var req *http.Request
	if body == "" {
		req = httptest.NewRequest(method, path, nil)
	} else {
		req = httptest.NewRequest(method, path, strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("Authorization", "Bearer "+token)
	return req
}

func decodeBody(t *testing.T, rec *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("decode %q: %v", rec.Body.String(), err)
	}
	return out
}

func TestPredictionRoutesRequireAuth(t *testing.T) {
	router, _ := newTestRouter(t)
	for _, tc := range []struct{ method, path string }{
		{http.MethodGet, "/api/predictions/AAPL"},
		{http.MethodPost, "/api/predictions/AAPL/jobs"},
		{http.MethodGet, "/api/prediction-jobs/" + routeJobID},
	} {
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, httptest.NewRequest(tc.method, tc.path, nil))
		if rec.Code != http.StatusUnauthorized {
			t.Errorf("%s %s status = %d, want 401", tc.method, tc.path, rec.Code)
		}
	}
}

func TestPredictionRoutesResolve(t *testing.T) {
	router, tokens := newTestRouter(t)

	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, authed(t, tokens, http.MethodGet, "/api/predictions/aapl", ""))
	if body := decodeBody(t, rec); rec.Code != 200 || body["status"] != "model" || body["ticker"] != "AAPL" {
		t.Errorf("GET prediction: %d %v", rec.Code, body)
	}

	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, authed(t, tokens, http.MethodGet, "/api/prediction-jobs/"+routeJobID, ""))
	if body := decodeBody(t, rec); rec.Code != 200 || body["status"] != "job" || body["id"] != routeJobID {
		t.Errorf("GET job: %d %v", rec.Code, body)
	}

	// created -> 202, user id from the token's user (stubLoader: ID 1)
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, authed(t, tokens, http.MethodPost, "/api/predictions/AAPL/jobs", `{"kind":"train"}`))
	if body := decodeBody(t, rec); rec.Code != http.StatusAccepted || body["created"] != true || body["status"] != "submitted-by-1" {
		t.Errorf("POST new job: %d %v", rec.Code, body)
	}

	// joined -> 200
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, authed(t, tokens, http.MethodPost, "/api/predictions/NVDA/jobs", `{"kind":"refresh"}`))
	if body := decodeBody(t, rec); rec.Code != http.StatusOK || body["created"] != false {
		t.Errorf("POST joined job: %d %v", rec.Code, body)
	}
}

func TestPredictionJobConflictReturnsExistingJob(t *testing.T) {
	router, tokens := newTestRouter(t)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, authed(t, tokens, http.MethodPost, "/api/predictions/MSFT/jobs", `{"kind":"train"}`))
	body := decodeBody(t, rec)
	job, _ := body["job"].(map[string]any)
	if rec.Code != http.StatusConflict || body["status"] != float64(409) || job["ticker"] != "AAPL" {
		t.Errorf("conflict: %d %v", rec.Code, body)
	}
	if msg, _ := body["message"].(string); !strings.Contains(msg, "AAPL") {
		t.Errorf("message = %q", msg)
	}
}

func TestPredictionJobBodyValidation(t *testing.T) {
	router, tokens := newTestRouter(t)
	for body, want := range map[string]string{
		`{}`:                "kind: Kind is required",
		`{"kind":"delete"}`: "kind: Kind must be train or refresh",
		`not json`:          "Validation failed",
	} {
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, authed(t, tokens, http.MethodPost, "/api/predictions/AAPL/jobs", body))
		if got := decodeBody(t, rec)["message"]; rec.Code != http.StatusBadRequest || got != want {
			t.Errorf("body %s: %d %v, want 400 %q", body, rec.Code, got, want)
		}
	}
}
