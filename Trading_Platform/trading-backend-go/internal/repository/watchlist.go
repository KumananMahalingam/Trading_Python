package repository

import (
	"context"
	"fmt"

	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/storage"
)

// WatchlistRepository replaces WatchlistRepository.java.
type WatchlistRepository struct{}

func NewWatchlistRepository() *WatchlistRepository { return &WatchlistRepository{} }

// FindByUser mirrors findByUserOrderByAddedAtDesc, eagerly joining the symbol
// the way the EAGER @ManyToOne did.
func (r *WatchlistRepository) FindByUser(ctx context.Context, q storage.Querier, userID int64) ([]models.WatchlistEntry, error) {
	rows, err := q.QueryContext(ctx,
		`SELECT w.id, w.user_id, w.added_at,
		        s.id, s.ticker, s.name, s.exchange, s.asset_type, s.is_active
		 FROM watchlist w
		 JOIN symbols s ON s.id = w.symbol_id
		 WHERE w.user_id = $1
		 ORDER BY w.added_at DESC`, userID)
	if err != nil {
		return nil, fmt.Errorf("find watchlist: %w", err)
	}
	defer rows.Close()

	entries := make([]models.WatchlistEntry, 0)
	for rows.Next() {
		var e models.WatchlistEntry
		if err := rows.Scan(&e.ID, &e.UserID, &e.AddedAt,
			&e.Symbol.ID, &e.Symbol.Ticker, &e.Symbol.Name, &e.Symbol.Exchange,
			&e.Symbol.AssetType, &e.Symbol.Active); err != nil {
			return nil, fmt.Errorf("scan watchlist entry: %w", err)
		}

		// Normalise to UTC so the JSON matches the Instants Java emitted.
		e.AddedAt = e.AddedAt.UTC()

		entries = append(entries, e)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate watchlist: %w", err)
	}

	return entries, nil
}

// ExistsByUserAndTicker mirrors existsByUserAndSymbolTicker.
func (r *WatchlistRepository) ExistsByUserAndTicker(ctx context.Context, q storage.Querier, userID int64, ticker string) (bool, error) {
	var exists bool
	err := q.QueryRowContext(ctx,
		`SELECT EXISTS (
		    SELECT 1 FROM watchlist w
		    JOIN symbols s ON s.id = w.symbol_id
		    WHERE w.user_id = $1 AND s.ticker = $2
		 )`, userID, ticker).Scan(&exists)
	if err != nil {
		return false, fmt.Errorf("check watchlist entry: %w", err)
	}
	return exists, nil
}

// Insert adds an entry, stamping added_at like the @PrePersist hook did.
func (r *WatchlistRepository) Insert(ctx context.Context, q storage.Querier, e *models.WatchlistEntry) error {
	err := q.QueryRowContext(ctx,
		`INSERT INTO watchlist (user_id, symbol_id, added_at)
		 VALUES ($1, $2, $3)
		 RETURNING id`,
		e.UserID, e.Symbol.ID, e.AddedAt).Scan(&e.ID)
	if err != nil {
		return fmt.Errorf("insert watchlist entry: %w", err)
	}
	return nil
}

// DeleteByUserAndTicker mirrors deleteByUserAndSymbolTicker.
func (r *WatchlistRepository) DeleteByUserAndTicker(ctx context.Context, q storage.Querier, userID int64, ticker string) error {
	_, err := q.ExecContext(ctx,
		`DELETE FROM watchlist
		 WHERE user_id = $1
		   AND symbol_id IN (SELECT id FROM symbols WHERE ticker = $2)`, userID, ticker)
	if err != nil {
		return fmt.Errorf("delete watchlist entry: %w", err)
	}
	return nil
}
