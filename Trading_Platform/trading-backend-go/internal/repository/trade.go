package repository

import (
	"context"
	"fmt"

	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/storage"
)

// TradeRepository replaces TradeRepository.java.
type TradeRepository struct{}

func NewTradeRepository() *TradeRepository { return &TradeRepository{} }

// FindByUser mirrors findByUserOrderByExecutedAtDesc.
func (r *TradeRepository) FindByUser(ctx context.Context, q storage.Querier, userID int64) ([]models.Trade, error) {
	rows, err := q.QueryContext(ctx,
		`SELECT id, user_id, ticker, type,
		        quantity::text, price::text, total_amount::text, executed_at
		 FROM trades
		 WHERE user_id = $1
		 ORDER BY executed_at DESC`, userID)
	if err != nil {
		return nil, fmt.Errorf("find trades: %w", err)
	}
	defer rows.Close()

	trades := make([]models.Trade, 0)
	for rows.Next() {
		var (
			t                               models.Trade
			quantityS, priceS, totalAmountS string
		)
		if err := rows.Scan(&t.ID, &t.UserID, &t.Ticker, &t.Type,
			&quantityS, &priceS, &totalAmountS, &t.ExecutedAt); err != nil {
			return nil, fmt.Errorf("scan trade: %w", err)
		}

		// Normalise to UTC so the JSON matches the Instants Java emitted.
		t.ExecutedAt = t.ExecutedAt.UTC()

		if t.Quantity, err = parseDecimal(quantityS); err != nil {
			return nil, fmt.Errorf("parse trade quantity: %w", err)
		}
		if t.Price, err = parseDecimal(priceS); err != nil {
			return nil, fmt.Errorf("parse trade price: %w", err)
		}
		if t.TotalAmount, err = parseDecimal(totalAmountS); err != nil {
			return nil, fmt.Errorf("parse trade total amount: %w", err)
		}

		trades = append(trades, t)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate trades: %w", err)
	}

	return trades, nil
}

// Insert records an executed trade, stamping executed_at like @PrePersist.
func (r *TradeRepository) Insert(ctx context.Context, q storage.Querier, t *models.Trade) error {
	err := q.QueryRowContext(ctx,
		`INSERT INTO trades (user_id, ticker, type, quantity, price, total_amount, executed_at)
		 VALUES ($1, $2, $3, $4::text::numeric, $5::text::numeric, $6::text::numeric, $7)
		 RETURNING id`,
		t.UserID, t.Ticker, string(t.Type),
		t.Quantity.String(), t.Price.String(), t.TotalAmount.String(), t.ExecutedAt).Scan(&t.ID)
	if err != nil {
		return fmt.Errorf("insert trade: %w", err)
	}
	return nil
}
