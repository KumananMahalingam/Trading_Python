package dto_test

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/shopspring/decimal"

	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/models"
)

// The frontend is unchanged, so these tests pin the JSON shapes it reads.
func TestMain(m *testing.M) {
	dto.ConfigureJSONEncoding()
	m.Run()
}

func marshal(t *testing.T, value any) map[string]any {
	t.Helper()

	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded map[string]any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v (payload %s)", err, raw)
	}
	return decoded
}

// TestCandleJSONContract covers GET /api/candles/{ticker}, which ChartPage
// reads as c.timestamp, c.open, c.high, c.low, c.close and c.volume.
func TestCandleJSONContract(t *testing.T) {
	candle := models.Candle{
		ID:        7,
		Ticker:    "BTCUSDT",
		Interval:  "1D",
		Timestamp: time.Date(2026, 9, 22, 0, 0, 0, 0, time.UTC),
		Open:      decimal.RequireFromString("64000.123456"),
		High:      decimal.RequireFromString("65000"),
		Low:       decimal.RequireFromString("63000"),
		Close:     decimal.RequireFromString("64500.5"),
		Volume:    decimal.RequireFromString("1234.56"),
	}

	decoded := marshal(t, candle)

	for _, key := range []string{"id", "ticker", "interval", "timestamp", "open", "high", "low", "close", "volume"} {
		if _, ok := decoded[key]; !ok {
			t.Errorf("candle JSON is missing the %q field", key)
		}
	}

	// Numbers decode into float64; a quoted decimal would arrive as a string.
	if _, ok := decoded["open"].(float64); !ok {
		t.Errorf("open should serialise as a JSON number, got %T", decoded["open"])
	}

	// new Date(c.timestamp) in the frontend needs a parseable date string.
	timestamp, ok := decoded["timestamp"].(string)
	if !ok {
		t.Fatalf("timestamp should serialise as a string, got %T", decoded["timestamp"])
	}
	if _, err := time.Parse(time.RFC3339, timestamp); err != nil {
		t.Errorf("timestamp %q is not RFC3339: %v", timestamp, err)
	}
}

// TestCandleUpdateTimestampIsNumeric matters because ChartPage passes
// data.timestamp straight to lightweight-charts as a time value.
func TestCandleUpdateTimestampIsNumeric(t *testing.T) {
	decoded := marshal(t, dto.CandleUpdateResponse{
		Ticker:    "BTCUSDT",
		Interval:  "1H",
		Timestamp: 1758499200,
		Open:      decimal.RequireFromString("1"),
		High:      decimal.RequireFromString("2"),
		Low:       decimal.RequireFromString("0.5"),
		Close:     decimal.RequireFromString("1.5"),
		Volume:    decimal.RequireFromString("10"),
	})

	value, ok := decoded["timestamp"].(float64)
	if !ok {
		t.Fatalf("timestamp should be a JSON number, got %T", decoded["timestamp"])
	}
	if int64(value) != 1758499200 {
		t.Errorf("timestamp = %v, want 1758499200", value)
	}
}

// TestAuthResponseHidesRefreshToken mirrors the @JsonIgnore on the Java DTO:
// the refresh token must only travel in the HTTP-only cookie.
func TestAuthResponseHidesRefreshToken(t *testing.T) {
	decoded := marshal(t, dto.AuthResponse{
		AccessToken:  "access",
		RefreshToken: "refresh",
		Username:     "tester",
		Email:        "tester@example.com",
	})

	if _, present := decoded["refreshToken"]; present {
		t.Error("refreshToken must not appear in the response body")
	}
	for _, key := range []string{"accessToken", "username", "email"} {
		if _, ok := decoded[key]; !ok {
			t.Errorf("auth response is missing the %q field", key)
		}
	}
}

// TestTradeHistoryOmitsBalance matches the null balanceAfter the Java service
// returned for history entries.
func TestTradeHistoryOmitsBalance(t *testing.T) {
	decoded := marshal(t, dto.TradeResponse{
		ID:          1,
		Ticker:      "AAPL",
		Type:        models.TradeBuy,
		Quantity:    decimal.RequireFromString("2"),
		Price:       decimal.RequireFromString("100"),
		TotalAmount: decimal.RequireFromString("200"),
		ExecutedAt:  time.Now().UTC(),
	})

	if value := decoded["balanceAfter"]; value != nil {
		t.Errorf("balanceAfter = %v, want null", value)
	}
	if decoded["type"] != "BUY" {
		t.Errorf("type = %v, want the BUY string", decoded["type"])
	}
}

// TestPortfolioAndWatchlistFieldNames checks the camelCase keys the frontend reads.
func TestPortfolioAndWatchlistFieldNames(t *testing.T) {
	portfolio := marshal(t, dto.PortfolioResponse{
		Balance: decimal.RequireFromString("100000.00"),
		Holdings: []dto.HoldingResponse{{
			Ticker:         "AAPL",
			Quantity:       decimal.RequireFromString("2"),
			AvgBuyPrice:    decimal.RequireFromString("100"),
			InvestedAmount: decimal.RequireFromString("200"),
		}},
	})

	if _, ok := portfolio["balance"].(float64); !ok {
		t.Errorf("balance should be a JSON number, got %T", portfolio["balance"])
	}

	holdings, ok := portfolio["holdings"].([]any)
	if !ok || len(holdings) != 1 {
		t.Fatalf("holdings did not serialise as a one element array: %#v", portfolio["holdings"])
	}

	holding := holdings[0].(map[string]any)
	for _, key := range []string{"ticker", "quantity", "avgBuyPrice", "investedAmount"} {
		if _, ok := holding[key]; !ok {
			t.Errorf("holding is missing the %q field", key)
		}
	}

	watchlist := marshal(t, dto.WatchlistItemResponse{
		Ticker:    "BTCUSDT",
		Name:      "BTC / USDT",
		Exchange:  "BINANCE",
		AssetType: models.AssetCrypto,
		AddedAt:   time.Now().UTC(),
	})
	for _, key := range []string{"ticker", "name", "exchange", "assetType", "addedAt"} {
		if _, ok := watchlist[key]; !ok {
			t.Errorf("watchlist item is missing the %q field", key)
		}
	}
	if watchlist["assetType"] != "CRYPTO" {
		t.Errorf("assetType = %v, want CRYPTO", watchlist["assetType"])
	}
}

// TestSymbolFieldNames covers GET /api/symbols.
func TestSymbolFieldNames(t *testing.T) {
	decoded := marshal(t, models.Symbol{
		ID: 1, Ticker: "AAPL", Name: "Apple Inc", Exchange: "NASDAQ",
		AssetType: models.AssetStock, Active: true,
	})

	for _, key := range []string{"id", "ticker", "name", "exchange", "assetType", "active"} {
		if _, ok := decoded[key]; !ok {
			t.Errorf("symbol is missing the %q field", key)
		}
	}
}

// TestUserNeverSerialisesPassword guards against leaking the hash.
func TestUserNeverSerialisesPassword(t *testing.T) {
	decoded := marshal(t, models.User{
		ID: 1, Username: "tester", Email: "tester@example.com",
		Password: "$2a$10$notarealhash", Role: models.RoleUser,
		Balance: decimal.RequireFromString("100000.00"),
	})

	if _, present := decoded["password"]; present {
		t.Error("password must never be serialised")
	}
}
