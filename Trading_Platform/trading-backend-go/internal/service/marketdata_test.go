package service

import (
	"context"
	"sync"
	"testing"

	"github.com/shopspring/decimal"

	"github.com/tradingapp/trading-backend/internal/config"
	"github.com/tradingapp/trading-backend/internal/dto"
)

// recordingAlertChecker stands in for the database-backed checker.
type recordingAlertChecker struct {
	mu      sync.Mutex
	checked []string
}

func (r *recordingAlertChecker) Check(_ context.Context, ticker string, price decimal.Decimal) {
	r.mu.Lock()
	r.checked = append(r.checked, ticker+"@"+price.String())
	r.mu.Unlock()
}

// capturingPublisher records what would be pushed to subscribers.
type capturingPublisher struct {
	mu       sync.Mutex
	messages map[string]any
}

func newCapturingPublisher() *capturingPublisher {
	return &capturingPublisher{messages: map[string]any{}}
}

func (p *capturingPublisher) Publish(destination string, payload any) {
	p.mu.Lock()
	p.messages[destination] = payload
	p.mu.Unlock()
}

func (p *capturingPublisher) get(destination string) (any, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	value, ok := p.messages[destination]
	return value, ok
}

// realBinanceTicker is an actual 24h ticker payload. Note that "c" (last price)
// is followed later by "C" (statistics close time), and that the numeric fields
// arrive unquoted on the stream endpoints.
const realBinanceTicker = `{
  "e":"24hrTicker","E":1790111744012,"s":"BTCUSDT",
  "p":-394.21,"P":-0.455,"w":86500.12,
  "x":86626.80,"c":86232.59,"Q":0.015,
  "b":86232.58,"B":1.234,"a":86232.59,"A":2.345,
  "o":86626.80,"h":87000.00,"l":86000.00,
  "v":12345.678,"q":1069000000.00,
  "O":1790025344012,"C":1790111744012,
  "F":100,"L":200,"n":101
}`

// TestHandleBinanceTickerUsesLastPriceNotCloseTime is the regression guard for
// the case-insensitive JSON matching bug, where "C" overwrote "c" and the price
// became an epoch timestamp.
func TestHandleBinanceTickerUsesLastPriceNotCloseTime(t *testing.T) {
	publisher := newCapturingPublisher()
	svc := NewMarketDataService(&config.Config{}, publisher, &recordingAlertChecker{})

	svc.handleBinanceTicker(context.Background(), []byte(realBinanceTicker))

	payload, ok := publisher.get("/topic/price/BTCUSDT")
	if !ok {
		t.Fatal("nothing was published to /topic/price/BTCUSDT")
	}

	price, ok := payload.(dto.PriceResponse)
	if !ok {
		t.Fatalf("published %T, want dto.PriceResponse", payload)
	}

	if got := price.Price.String(); got != "86232.59" {
		t.Errorf("price = %s, want 86232.59 (the \"c\" field, not \"C\")", got)
	}
	if got := price.Change.String(); got != "-394.21" {
		t.Errorf("change = %s, want -394.21", got)
	}
	if got := price.ChangePercent.String(); got != "-0.455" {
		t.Errorf("changePercent = %s, want -0.455", got)
	}
	if price.Ticker != "BTCUSDT" {
		t.Errorf("ticker = %q, want BTCUSDT", price.Ticker)
	}
}

// realBinanceKline is an actual kline payload. "t" (open time) is followed by
// "T" (close time), "l" (low) is preceded by "L" (last trade id), and "v" (base
// volume) is followed by "V" (taker buy volume).
const realBinanceKline = `{
  "e":"kline","E":1790111744012,"s":"BTCUSDT",
  "k":{
    "t":1790110800000,"T":1790114399999,
    "s":"BTCUSDT","i":"1h",
    "f":100,"L":200,
    "o":86264.01,"c":86232.59,"h":86344.01,"l":86210.72,
    "v":34.76837,"n":1500,"x":false,
    "q":3000000.00,"V":17.11111,"Q":1500000.00,"B":"0"
  }
}`

// TestHandleBinanceKlineUsesOpenTimeAndBaseVolume guards the same class of bug
// on the kline stream, where "T" overwrote the open time and "V" the volume.
func TestHandleBinanceKlineUsesOpenTimeAndBaseVolume(t *testing.T) {
	publisher := newCapturingPublisher()
	svc := NewMarketDataService(&config.Config{}, publisher, &recordingAlertChecker{})

	svc.handleBinanceKline([]byte(realBinanceKline))

	payload, ok := publisher.get("/topic/candle/BTCUSDT/1H")
	if !ok {
		t.Fatal("nothing was published to /topic/candle/BTCUSDT/1H")
	}

	candle, ok := payload.(dto.CandleUpdateResponse)
	if !ok {
		t.Fatalf("published %T, want dto.CandleUpdateResponse", payload)
	}

	// 1790110800000 ms / 1000 = 1790110800 s, the start of the hour.
	if candle.Timestamp != 1790110800 {
		t.Errorf("timestamp = %d, want 1790110800 (open time \"t\", not close time \"T\")", candle.Timestamp)
	}
	if got := candle.Volume.String(); got != "34.76837" {
		t.Errorf("volume = %s, want 34.76837 (the \"v\" field, not \"V\")", got)
	}
	if got := candle.Low.String(); got != "86210.72" {
		t.Errorf("low = %s, want 86210.72 (the \"l\" field, not \"L\")", got)
	}
	if got := candle.Open.String(); got != "86264.01" {
		t.Errorf("open = %s, want 86264.01", got)
	}
	if got := candle.Close.String(); got != "86232.59" {
		t.Errorf("close = %s, want 86232.59", got)
	}
	// The interval label must be mapped to the frontend's casing.
	if candle.Interval != "1H" {
		t.Errorf("interval = %q, want 1H", candle.Interval)
	}
}

// TestHandleBinanceMessageRoutesByStream checks the combined-stream envelope.
func TestHandleBinanceMessageRoutesByStream(t *testing.T) {
	publisher := newCapturingPublisher()
	svc := NewMarketDataService(&config.Config{}, publisher, &recordingAlertChecker{})

	envelope := func(stream, data string) []byte {
		return []byte(`{"stream":"` + stream + `","data":` + data + `}`)
	}

	svc.handleBinanceMessage(context.Background(), envelope("btcusdt@ticker", realBinanceTicker))
	if _, ok := publisher.get("/topic/price/BTCUSDT"); !ok {
		t.Error("ticker stream did not publish a price")
	}

	svc.handleBinanceMessage(context.Background(), envelope("btcusdt@kline_1h", realBinanceKline))
	if _, ok := publisher.get("/topic/candle/BTCUSDT/1H"); !ok {
		t.Error("kline stream did not publish a candle")
	}
}

// TestHandleFinnhubMessageUsesLastTrade covers the stock price path.
func TestHandleFinnhubMessageUsesLastTrade(t *testing.T) {
	publisher := newCapturingPublisher()
	svc := NewMarketDataService(&config.Config{}, publisher, &recordingAlertChecker{})

	payload := `{"type":"trade","data":[
	  {"s":"AAPL","p":229.11,"t":1790111744012,"v":10},
	  {"s":"AAPL","p":229.87,"t":1790111744500,"v":5}
	]}`

	svc.handleFinnhubMessage(context.Background(), []byte(payload))

	published, ok := publisher.get("/topic/price/AAPL")
	if !ok {
		t.Fatal("nothing was published to /topic/price/AAPL")
	}

	price := published.(dto.PriceResponse)
	// The most recent trade in the batch wins.
	if got := price.Price.String(); got != "229.87" {
		t.Errorf("price = %s, want 229.87 (the last trade)", got)
	}
}

// TestHandleFinnhubMessageIgnoresNonTradeTypes covers ping and error frames.
func TestHandleFinnhubMessageIgnoresNonTradeTypes(t *testing.T) {
	publisher := newCapturingPublisher()
	svc := NewMarketDataService(&config.Config{}, publisher, &recordingAlertChecker{})

	for _, payload := range []string{
		`{"type":"ping"}`,
		`{"type":"trade","data":[]}`,
		`not json at all`,
	} {
		svc.handleFinnhubMessage(context.Background(), []byte(payload))
	}

	if len(publisher.messages) != 0 {
		t.Errorf("published %d messages, want none", len(publisher.messages))
	}
}
