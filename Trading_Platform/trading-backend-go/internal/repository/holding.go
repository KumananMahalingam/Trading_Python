package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/storage"
)

// HoldingRepository replaces HoldingRepository.java.
type HoldingRepository struct{}

func NewHoldingRepository() *HoldingRepository { return &HoldingRepository{} }

const holdingColumns = `id, user_id, ticker, quantity::text, avg_buy_price::text`

func scanHolding(row interface{ Scan(...any) error }) (*models.Holding, error) {
	var (
		h                    models.Holding
		quantityS, avgPriceS string
	)
	if err := row.Scan(&h.ID, &h.UserID, &h.Ticker, &quantityS, &avgPriceS); err != nil {
		return nil, err
	}

	quantity, err := parseDecimal(quantityS)
	if err != nil {
		return nil, fmt.Errorf("parse holding quantity: %w", err)
	}
	avgPrice, err := parseDecimal(avgPriceS)
	if err != nil {
		return nil, fmt.Errorf("parse holding avg price: %w", err)
	}

	h.Quantity, h.AvgBuyPrice = quantity, avgPrice
	return &h, nil
}

// FindByUser mirrors findByUserOrderByTickerAsc.
func (r *HoldingRepository) FindByUser(ctx context.Context, q storage.Querier, userID int64) ([]models.Holding, error) {
	rows, err := q.QueryContext(ctx,
		`SELECT `+holdingColumns+` FROM holdings WHERE user_id = $1 ORDER BY ticker ASC`, userID)
	if err != nil {
		return nil, fmt.Errorf("find holdings: %w", err)
	}
	defer rows.Close()

	holdings := make([]models.Holding, 0)
	for rows.Next() {
		h, err := scanHolding(rows)
		if err != nil {
			return nil, fmt.Errorf("scan holding: %w", err)
		}
		holdings = append(holdings, *h)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate holdings: %w", err)
	}

	return holdings, nil
}

// FindByUserAndTicker mirrors findByUserAndTicker.
func (r *HoldingRepository) FindByUserAndTicker(ctx context.Context, q storage.Querier, userID int64, ticker string) (*models.Holding, error) {
	row := q.QueryRowContext(ctx,
		`SELECT `+holdingColumns+` FROM holdings WHERE user_id = $1 AND ticker = $2`, userID, ticker)

	holding, err := scanHolding(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("find holding: %w", err)
	}
	return holding, nil
}

// Upsert writes the holding, replacing it when the user already holds the
// ticker. This covers both the insert and update paths of the JPA save().
func (r *HoldingRepository) Upsert(ctx context.Context, q storage.Querier, h *models.Holding) error {
	err := q.QueryRowContext(ctx,
		`INSERT INTO holdings (user_id, ticker, quantity, avg_buy_price)
		 VALUES ($1, $2, $3::text::numeric, $4::text::numeric)
		 ON CONFLICT (user_id, ticker)
		 DO UPDATE SET quantity = EXCLUDED.quantity, avg_buy_price = EXCLUDED.avg_buy_price
		 RETURNING id`,
		h.UserID, h.Ticker, h.Quantity.String(), h.AvgBuyPrice.String()).Scan(&h.ID)
	if err != nil {
		return fmt.Errorf("upsert holding: %w", err)
	}
	return nil
}

// Delete removes a holding that has been fully sold.
func (r *HoldingRepository) Delete(ctx context.Context, q storage.Querier, id int64) error {
	if _, err := q.ExecContext(ctx, `DELETE FROM holdings WHERE id = $1`, id); err != nil {
		return fmt.Errorf("delete holding: %w", err)
	}
	return nil
}
