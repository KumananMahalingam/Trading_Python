package service

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/gorilla/websocket"
	"github.com/shopspring/decimal"

	"github.com/tradingapp/trading-backend/internal/config"
	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/external"
)

// reconnectDelay matches the 5 second retry the Java service used.
const reconnectDelay = 5 * time.Second

// klineIntervals are the Binance kline streams subscribed to, and the labels
// the frontend uses for each.
var klineIntervals = map[string]string{
	"1h": "1H",
	"4h": "4H",
	"1d": "1D",
	"1w": "1W",
}

// AlertChecker evaluates a price tick against the stored alerts.
type AlertChecker interface {
	Check(ctx context.Context, ticker string, price decimal.Decimal)
}

// MarketDataService replaces MarketDataService.java. It keeps long-lived
// WebSocket connections to Binance and Finnhub, republishing every tick to the
// STOMP broker and feeding the alert checker.
type MarketDataService struct {
	cfg       *config.Config
	publisher Publisher
	alerts    AlertChecker
}

func NewMarketDataService(cfg *config.Config, publisher Publisher, alerts AlertChecker) *MarketDataService {
	return &MarketDataService{cfg: cfg, publisher: publisher, alerts: alerts}
}

// Start launches both upstream connections and returns immediately. Each
// connection reconnects until ctx is cancelled.
func (s *MarketDataService) Start(ctx context.Context) {
	go s.runWithRetry(ctx, "binance", s.connectBinance)

	if s.cfg.FinnhubAPIKey == "" {
		slog.Warn("FINNHUB_API_KEY is empty, skipping Finnhub price stream")
		return
	}
	go s.runWithRetry(ctx, "finnhub", s.connectFinnhub)
}

// runWithRetry keeps a connection alive, backing off between attempts.
func (s *MarketDataService) runWithRetry(ctx context.Context, name string, connect func(context.Context) error) {
	for {
		if ctx.Err() != nil {
			return
		}

		if err := connect(ctx); err != nil && ctx.Err() == nil {
			slog.Error("market data stream ended", "provider", name, "error", err)
		}

		select {
		case <-ctx.Done():
			return
		case <-time.After(reconnectDelay):
			slog.Info("reconnecting market data stream", "provider", name)
		}
	}
}

// dial opens a WebSocket with a bounded handshake timeout.
func dial(ctx context.Context, url string) (*websocket.Conn, error) {
	dialer := websocket.Dialer{
		HandshakeTimeout: 15 * time.Second,
		// Binance's combined stream payloads can be sizeable.
		ReadBufferSize:  1 << 16,
		WriteBufferSize: 1 << 14,
	}

	conn, resp, err := dialer.DialContext(ctx, url, nil)
	if err != nil {
		if resp != nil {
			return nil, fmt.Errorf("dial %s: %w (status %d)", url, err, resp.StatusCode)
		}
		return nil, fmt.Errorf("dial: %w", err)
	}

	return conn, nil
}

// connectBinance subscribes to the combined ticker and kline streams.
func (s *MarketDataService) connectBinance(ctx context.Context) error {
	streams := make([]string, 0, len(CryptoTickers)*(1+len(klineIntervals)))
	for _, ticker := range CryptoTickers {
		lower := strings.ToLower(ticker)
		streams = append(streams, lower+"@ticker")
		for binanceInterval := range klineIntervals {
			streams = append(streams, lower+"@kline_"+binanceInterval)
		}
	}

	url := fmt.Sprintf("%s/stream?streams=%s", s.cfg.BinanceWSURL, strings.Join(streams, "/"))

	conn, err := dial(ctx, url)
	if err != nil {
		return err
	}
	defer conn.Close()

	slog.Info("connected to Binance WebSocket", "streams", len(streams))

	go closeOnContextDone(ctx, conn)

	for {
		_, payload, err := conn.ReadMessage()
		if err != nil {
			return fmt.Errorf("read binance message: %w", err)
		}
		s.handleBinanceMessage(ctx, payload)
	}
}

// binanceEnvelope is the combined-stream wrapper.
type binanceEnvelope struct {
	Stream string          `json:"stream"`
	Data   json.RawMessage `json:"data"`
}

// rawObject is a JSON object whose keys are read with exact, case-sensitive
// matching.
//
// This matters because Binance uses single-letter keys where case carries
// meaning: in a kline "t" is the open time and "T" the close time, "v" the base
// volume and "V" the taker buy volume; in a ticker "c" is the last price and "C"
// the statistics close time. Go's struct decoding falls back to a
// case-insensitive match, so a struct tagged `json:"t"` also accepts "T" and the
// later key silently wins. Jackson's path("t") was exact, so decoding through
// this map preserves the original behaviour.
type rawObject map[string]json.RawMessage

func (o rawObject) text(key string) string {
	raw, ok := o[key]
	if !ok {
		return ""
	}
	var value string
	if err := json.Unmarshal(raw, &value); err != nil {
		return ""
	}
	return value
}

func (o rawObject) number(key string) (external.Number, bool) {
	raw, ok := o[key]
	if !ok {
		return external.Number{}, false
	}
	var value external.Number
	if err := json.Unmarshal(raw, &value); err != nil {
		return external.Number{}, false
	}
	return value, true
}

func (o rawObject) int64(key string) (int64, bool) {
	raw, ok := o[key]
	if !ok {
		return 0, false
	}
	var value int64
	if err := json.Unmarshal(raw, &value); err != nil {
		return 0, false
	}
	return value, true
}

func (o rawObject) object(key string) (rawObject, bool) {
	raw, ok := o[key]
	if !ok {
		return nil, false
	}
	var value rawObject
	if err := json.Unmarshal(raw, &value); err != nil {
		return nil, false
	}
	return value, true
}

func (s *MarketDataService) handleBinanceMessage(ctx context.Context, payload []byte) {
	var envelope binanceEnvelope
	if err := json.Unmarshal(payload, &envelope); err != nil {
		slog.Error("failed to decode binance message", "error", err)
		return
	}
	if len(envelope.Data) == 0 {
		return
	}

	switch {
	case strings.Contains(envelope.Stream, "@ticker"):
		s.handleBinanceTicker(ctx, envelope.Data)
	case strings.Contains(envelope.Stream, "@kline"):
		s.handleBinanceKline(envelope.Data)
	}
}

func (s *MarketDataService) handleBinanceTicker(ctx context.Context, data []byte) {
	var ticker rawObject
	if err := json.Unmarshal(data, &ticker); err != nil {
		slog.Error("failed to decode binance ticker", "error", err)
		return
	}

	symbol := strings.ToUpper(ticker.text("s"))
	if symbol == "" {
		return
	}

	// "c" is the last price. "C" is a timestamp and must not be confused for it.
	price, ok := ticker.number("c")
	if !ok {
		slog.Warn("binance ticker had no usable last price", "symbol", symbol)
		return
	}

	// A missing change field is not fatal; it defaults to zero as before.
	change, _ := ticker.number("p")
	changePercent, _ := ticker.number("P")

	s.publisher.Publish("/topic/price/"+symbol, dto.PriceResponse{
		Ticker:        symbol,
		Price:         price.Decimal,
		Change:        change.Decimal,
		ChangePercent: changePercent.Decimal,
		Timestamp:     time.Now().UTC(),
	})

	s.alerts.Check(ctx, symbol, price.Decimal)
}

func (s *MarketDataService) handleBinanceKline(data []byte) {
	var event rawObject
	if err := json.Unmarshal(data, &event); err != nil {
		slog.Error("failed to decode binance kline event", "error", err)
		return
	}

	k, ok := event.object("k")
	if !ok {
		return
	}

	symbol := strings.ToUpper(k.text("s"))
	if symbol == "" {
		return
	}

	binanceInterval := k.text("i")
	appInterval, ok := klineIntervals[binanceInterval]
	if !ok {
		appInterval = binanceInterval
	}

	// "t" is the open time; "T" is the close time.
	openTimeMillis, ok := k.int64("t")
	if !ok {
		slog.Warn("binance kline had no open time", "symbol", symbol)
		return
	}

	// "v" is the base asset volume; "V" is only the taker buy portion.
	values := make([]decimal.Decimal, 0, 5)
	for _, key := range []string{"o", "h", "l", "c", "v"} {
		value, ok := k.number(key)
		if !ok {
			slog.Warn("binance kline missing field", "symbol", symbol, "field", key)
			return
		}
		values = append(values, value.Decimal)
	}

	s.publisher.Publish(fmt.Sprintf("/topic/candle/%s/%s", symbol, appInterval), dto.CandleUpdateResponse{
		Ticker:    symbol,
		Interval:  appInterval,
		Timestamp: openTimeMillis / 1000, // lightweight-charts expects Unix seconds.
		Open:      values[0],
		High:      values[1],
		Low:       values[2],
		Close:     values[3],
		Volume:    values[4],
	})
}

// connectFinnhub streams trades for the seeded stock tickers.
func (s *MarketDataService) connectFinnhub(ctx context.Context) error {
	url := fmt.Sprintf("%s?token=%s", s.cfg.FinnhubWSURL, s.cfg.FinnhubAPIKey)

	conn, err := dial(ctx, url)
	if err != nil {
		return err
	}
	defer conn.Close()

	for _, symbol := range StockTickers {
		message := fmt.Sprintf(`{"type":"subscribe","symbol":%q}`, symbol)
		if err := conn.WriteMessage(websocket.TextMessage, []byte(message)); err != nil {
			return fmt.Errorf("subscribe to %s: %w", symbol, err)
		}
		slog.Info("subscribed to Finnhub symbol", "symbol", symbol)
	}

	slog.Info("connected to Finnhub WebSocket")

	go closeOnContextDone(ctx, conn)

	for {
		_, payload, err := conn.ReadMessage()
		if err != nil {
			return fmt.Errorf("read finnhub message: %w", err)
		}
		s.handleFinnhubMessage(ctx, payload)
	}
}

// finnhubMessage is the trade envelope pushed by Finnhub.
type finnhubMessage struct {
	Type string `json:"type"`
	Data []struct {
		Symbol string          `json:"s"`
		Price  external.Number `json:"p"`
	} `json:"data"`
}

func (s *MarketDataService) handleFinnhubMessage(ctx context.Context, payload []byte) {
	var message finnhubMessage
	if err := json.Unmarshal(payload, &message); err != nil {
		slog.Error("failed to decode finnhub message", "error", err)
		return
	}

	if message.Type != "trade" || len(message.Data) == 0 {
		return
	}

	// Only the most recent trade in the batch matters, as before.
	last := message.Data[len(message.Data)-1]
	if last.Symbol == "" {
		return
	}

	symbol := strings.ToUpper(last.Symbol)
	price := last.Price.Decimal

	s.publisher.Publish("/topic/price/"+symbol, dto.PriceResponse{
		Ticker:        symbol,
		Price:         price,
		Change:        decimal.Zero,
		ChangePercent: decimal.Zero,
		Timestamp:     time.Now().UTC(),
	})

	s.alerts.Check(ctx, symbol, price)
}

// closeOnContextDone unblocks the read loop when the server shuts down.
func closeOnContextDone(ctx context.Context, conn *websocket.Conn) {
	<-ctx.Done()
	_ = conn.Close()
}
