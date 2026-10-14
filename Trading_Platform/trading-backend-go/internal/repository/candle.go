package repository

import (
	"context"
	"fmt"
	"strings"

	"github.com/shopspring/decimal"

	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/storage"
)

// CandleRepository replaces CandleRepository.java.
type CandleRepository struct{}

func NewCandleRepository() *CandleRepository { return &CandleRepository{} }

// FindByTickerAndInterval mirrors findByTickerAndIntervalOrderByTimestampAsc.
func (r *CandleRepository) FindByTickerAndInterval(ctx context.Context, q storage.Querier, ticker, interval string) ([]models.Candle, error) {
	rows, err := q.QueryContext(ctx,
		`SELECT id, ticker, interval_type, timestamp,
		        open::text, high::text, low::text, close::text, volume::text
		 FROM candles
		 WHERE ticker = $1 AND interval_type = $2
		 ORDER BY timestamp ASC`, ticker, interval)
	if err != nil {
		return nil, fmt.Errorf("find candles: %w", err)
	}
	defer rows.Close()

	candles := make([]models.Candle, 0)
	for rows.Next() {
		var (
			c                                   models.Candle
			openS, highS, lowS, closeS, volumeS string
		)
		if err := rows.Scan(&c.ID, &c.Ticker, &c.Interval, &c.Timestamp,
			&openS, &highS, &lowS, &closeS, &volumeS); err != nil {
			return nil, fmt.Errorf("scan candle: %w", err)
		}

		// The driver returns timestamptz in the local zone; Java serialised
		// Instants as UTC, so normalise to keep the JSON identical.
		c.Timestamp = c.Timestamp.UTC()

		targets := []struct {
			raw string
			dst *decimal.Decimal
		}{
			{openS, &c.Open},
			{highS, &c.High},
			{lowS, &c.Low},
			{closeS, &c.Close},
			{volumeS, &c.Volume},
		}
		for _, t := range targets {
			value, err := parseDecimal(t.raw)
			if err != nil {
				return nil, fmt.Errorf("parse candle value %q: %w", t.raw, err)
			}
			*t.dst = value
		}

		candles = append(candles, c)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate candles: %w", err)
	}

	return candles, nil
}

// InsertMany bulk-inserts candles, skipping rows that already exist for the
// (ticker, interval, timestamp) unique key.
func (r *CandleRepository) InsertMany(ctx context.Context, q storage.Querier, candles []models.Candle) error {
	if len(candles) == 0 {
		return nil
	}

	const columnsPerRow = 8
	placeholders := make([]string, 0, len(candles))
	args := make([]any, 0, len(candles)*columnsPerRow)

	for i, c := range candles {
		base := i * columnsPerRow
		// The OHLCV parameters are passed as text and cast, so the exact
		// decimal string reaches Postgres untouched by driver conversions.
		placeholders = append(placeholders, fmt.Sprintf(
			"($%d, $%d, $%d, $%d::text::numeric, $%d::text::numeric, $%d::text::numeric, $%d::text::numeric, $%d::text::numeric)",
			base+1, base+2, base+3, base+4, base+5, base+6, base+7, base+8))
		args = append(args,
			c.Ticker, c.Interval, c.Timestamp,
			c.Open.String(), c.High.String(), c.Low.String(), c.Close.String(), c.Volume.String())
	}

	query := `INSERT INTO candles (ticker, interval_type, timestamp, open, high, low, close, volume)
	          VALUES ` + strings.Join(placeholders, ", ") + `
	          ON CONFLICT (ticker, interval_type, timestamp) DO NOTHING`

	if _, err := q.ExecContext(ctx, query, args...); err != nil {
		return fmt.Errorf("insert candles: %w", err)
	}
	return nil
}
