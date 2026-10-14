package service

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/shopspring/decimal"

	"github.com/tradingapp/trading-backend/internal/config"
	"github.com/tradingapp/trading-backend/internal/external"
	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/repository"
)

// candleCacheTTL matches the 5 minute Redis TTL used before.
const candleCacheTTL = 5 * time.Minute

// CandleService replaces CandleService.java, keeping the same
// Redis -> Postgres -> provider lookup order.
type CandleService struct {
	db      *sql.DB
	candles *repository.CandleRepository
	symbols *repository.SymbolRepository
	redis   *redis.Client
	http    *external.Client
	cfg     *config.Config
}

func NewCandleService(
	db *sql.DB,
	candles *repository.CandleRepository,
	symbols *repository.SymbolRepository,
	rdb *redis.Client,
	httpClient *external.Client,
	cfg *config.Config,
) *CandleService {
	return &CandleService{db: db, candles: candles, symbols: symbols, redis: rdb, http: httpClient, cfg: cfg}
}

// GetCandles resolves OHLCV data for a ticker and interval.
func (s *CandleService) GetCandles(ctx context.Context, ticker, interval string) ([]models.Candle, error) {
	cacheKey := fmt.Sprintf("candles:%s:%s", ticker, interval)

	// 1. Redis.
	if cached, err := s.redis.Get(ctx, cacheKey).Result(); err == nil {
		var candles []models.Candle
		if err := json.Unmarshal([]byte(cached), &candles); err == nil {
			slog.Info("candle cache hit", "key", cacheKey)
			return candles, nil
		}
		slog.Warn("failed to decode cached candles", "key", cacheKey)
	} else if !errors.Is(err, redis.Nil) {
		slog.Warn("redis lookup failed", "key", cacheKey, "error", err)
	}

	// 2. Postgres.
	candles, err := s.candles.FindByTickerAndInterval(ctx, s.db, ticker, interval)
	if err != nil {
		return nil, err
	}
	if len(candles) > 0 {
		slog.Info("candle db hit", "key", cacheKey)
		s.cache(ctx, cacheKey, candles)
		return candles, nil
	}

	// 3. Provider, chosen by asset type.
	slog.Info("fetching candles from provider", "key", cacheKey)

	symbol, err := s.symbols.FindByTicker(ctx, s.db, ticker)
	if err != nil && !errors.Is(err, repository.ErrNotFound) {
		return nil, err
	}

	if symbol != nil && symbol.AssetType == models.AssetCrypto {
		candles = s.fetchFromBinance(ctx, ticker, interval)
	} else {
		candles = s.fetchFromAlphaVantage(ctx, ticker)
	}

	if len(candles) == 0 {
		return candles, nil
	}

	if err := s.candles.InsertMany(ctx, s.db, candles); err != nil {
		slog.Error("failed to persist candles", "ticker", ticker, "error", err)
	} else if stored, err := s.candles.FindByTickerAndInterval(ctx, s.db, ticker, interval); err != nil {
		slog.Warn("failed to reload persisted candles", "ticker", ticker, "error", err)
	} else if len(stored) > 0 {
		// Return the persisted rows so this response carries the same ids and
		// ordering a later cache or database hit would produce.
		candles = stored
	}

	s.cache(ctx, cacheKey, candles)

	return candles, nil
}

// fetchFromAlphaVantage loads daily candles. Failures degrade to an empty
// result, as they did before, so the chart renders empty instead of erroring.
func (s *CandleService) fetchFromAlphaVantage(ctx context.Context, ticker string) []models.Candle {
	url := fmt.Sprintf("%s/query?function=TIME_SERIES_DAILY&symbol=%s&outputsize=compact&apikey=%s",
		s.cfg.AlphaVantageBaseURL, ticker, s.cfg.AlphaVantageAPIKey)

	var response external.AlphaVantageCandleResponse
	if err := s.http.GetJSON(ctx, url, &response); err != nil {
		slog.Error("alpha vantage request failed", "ticker", ticker, "error", err)
		return nil
	}

	if len(response.TimeSeries) == 0 {
		slog.Warn("no candle data from alpha vantage", "ticker", ticker)
		return nil
	}

	candles := make([]models.Candle, 0, len(response.TimeSeries))
	for day, data := range response.TimeSeries {
		timestamp, err := time.ParseInLocation("2006-01-02", day, time.UTC)
		if err != nil {
			slog.Warn("skipping unparseable alpha vantage date", "value", day)
			continue
		}

		candle, err := buildCandle(ticker, "1D", timestamp,
			data.Open, data.High, data.Low, data.Close, data.Volume)
		if err != nil {
			slog.Warn("skipping unparseable alpha vantage candle", "ticker", ticker, "error", err)
			continue
		}

		candles = append(candles, candle)
	}

	sort.Slice(candles, func(i, j int) bool {
		return candles[i].Timestamp.Before(candles[j].Timestamp)
	})

	slog.Info("fetched candles from alpha vantage", "ticker", ticker, "count", len(candles))
	return candles
}

// fetchFromBinance loads klines for crypto tickers.
func (s *CandleService) fetchFromBinance(ctx context.Context, ticker, interval string) []models.Candle {
	url := fmt.Sprintf("%s/klines?symbol=%s&interval=%s&limit=100",
		s.cfg.BinanceBaseURL, ticker, toBinanceInterval(interval))

	// Klines are heterogeneous arrays, so each field is decoded explicitly.
	// external.Number accepts both the quoted and bare numeric forms and keeps
	// full precision either way.
	var raw [][]json.RawMessage
	if err := s.http.GetJSON(ctx, url, &raw); err != nil {
		slog.Error("binance klines request failed", "ticker", ticker, "error", err)
		return nil
	}

	if len(raw) == 0 {
		slog.Warn("no candle data from binance", "ticker", ticker)
		return nil
	}

	candles := make([]models.Candle, 0, len(raw))
	for _, kline := range raw {
		if len(kline) < 6 {
			continue
		}

		var openTimeMillis int64
		if err := json.Unmarshal(kline[0], &openTimeMillis); err != nil {
			slog.Warn("skipping kline with bad open time", "ticker", ticker)
			continue
		}

		values := make([]external.Number, 5)
		malformed := false
		for i := range values {
			if err := json.Unmarshal(kline[i+1], &values[i]); err != nil {
				malformed = true
				break
			}
		}
		if malformed {
			slog.Warn("skipping kline with bad OHLCV values", "ticker", ticker)
			continue
		}

		candles = append(candles, models.Candle{
			Ticker:    ticker,
			Interval:  interval,
			Timestamp: time.UnixMilli(openTimeMillis).UTC(),
			Open:      values[0].Decimal,
			High:      values[1].Decimal,
			Low:       values[2].Decimal,
			Close:     values[3].Decimal,
			Volume:    values[4].Decimal,
		})
	}

	slog.Info("fetched candles from binance", "ticker", ticker, "count", len(candles))
	return candles
}

func (s *CandleService) cache(ctx context.Context, key string, candles []models.Candle) {
	payload, err := json.Marshal(candles)
	if err != nil {
		slog.Warn("failed to encode candles for cache", "key", key, "error", err)
		return
	}

	if err := s.redis.Set(ctx, key, payload, candleCacheTTL).Err(); err != nil {
		slog.Warn("failed to cache candles", "key", key, "error", err)
	}
}

func buildCandle(ticker, interval string, timestamp time.Time, open, high, low, close, volume string) (models.Candle, error) {
	parsed := make([]decimal.Decimal, 5)
	for i, raw := range []string{open, high, low, close, volume} {
		value, err := decimal.NewFromString(raw)
		if err != nil {
			return models.Candle{}, fmt.Errorf("parse %q: %w", raw, err)
		}
		parsed[i] = value
	}

	return models.Candle{
		Ticker:    ticker,
		Interval:  interval,
		Timestamp: timestamp,
		Open:      parsed[0],
		High:      parsed[1],
		Low:       parsed[2],
		Close:     parsed[3],
		Volume:    parsed[4],
	}, nil
}

// toBinanceInterval maps the app's interval labels onto Binance's.
func toBinanceInterval(interval string) string {
	switch interval {
	case "1min":
		return "1m"
	case "5min":
		return "5m"
	case "15min":
		return "15m"
	case "30min":
		return "30m"
	case "1H":
		return "1h"
	case "4H":
		return "4h"
	case "1D":
		return "1d"
	case "1W":
		return "1w"
	default:
		return "1d"
	}
}
