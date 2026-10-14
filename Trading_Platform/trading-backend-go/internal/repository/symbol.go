package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/storage"
)

// SymbolRepository replaces SymbolRepository.java.
type SymbolRepository struct{}

func NewSymbolRepository() *SymbolRepository { return &SymbolRepository{} }

const symbolColumns = `id, ticker, name, exchange, asset_type, is_active`

func scanSymbol(row interface{ Scan(...any) error }) (*models.Symbol, error) {
	var s models.Symbol
	if err := row.Scan(&s.ID, &s.Ticker, &s.Name, &s.Exchange, &s.AssetType, &s.Active); err != nil {
		return nil, err
	}
	return &s, nil
}

// FindByTicker mirrors findByTicker.
func (r *SymbolRepository) FindByTicker(ctx context.Context, q storage.Querier, ticker string) (*models.Symbol, error) {
	row := q.QueryRowContext(ctx, `SELECT `+symbolColumns+` FROM symbols WHERE ticker = $1`, ticker)

	symbol, err := scanSymbol(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("find symbol by ticker: %w", err)
	}
	return symbol, nil
}

// FindActive mirrors findByActiveTrue.
func (r *SymbolRepository) FindActive(ctx context.Context, q storage.Querier) ([]models.Symbol, error) {
	rows, err := q.QueryContext(ctx, `SELECT `+symbolColumns+` FROM symbols WHERE is_active = TRUE ORDER BY id`)
	if err != nil {
		return nil, fmt.Errorf("find active symbols: %w", err)
	}
	defer rows.Close()

	return collectSymbols(rows)
}

// Search mirrors findByTickerContainingIgnoreCaseOrNameContainingIgnoreCase.
func (r *SymbolRepository) Search(ctx context.Context, q storage.Querier, query string) ([]models.Symbol, error) {
	pattern := "%" + query + "%"

	rows, err := q.QueryContext(ctx,
		`SELECT `+symbolColumns+` FROM symbols
		 WHERE ticker ILIKE $1 OR name ILIKE $1
		 ORDER BY id`, pattern)
	if err != nil {
		return nil, fmt.Errorf("search symbols: %w", err)
	}
	defer rows.Close()

	return collectSymbols(rows)
}

// ExistsByTicker mirrors existsByTicker.
func (r *SymbolRepository) ExistsByTicker(ctx context.Context, q storage.Querier, ticker string) (bool, error) {
	var exists bool
	err := q.QueryRowContext(ctx, `SELECT EXISTS (SELECT 1 FROM symbols WHERE ticker = $1)`, ticker).Scan(&exists)
	if err != nil {
		return false, fmt.Errorf("check symbol ticker: %w", err)
	}
	return exists, nil
}

// Insert persists a seeded symbol, ignoring races on the unique ticker.
func (r *SymbolRepository) Insert(ctx context.Context, q storage.Querier, s *models.Symbol) error {
	_, err := q.ExecContext(ctx,
		`INSERT INTO symbols (ticker, name, exchange, asset_type, is_active)
		 VALUES ($1, $2, $3, $4, $5)
		 ON CONFLICT (ticker) DO NOTHING`,
		s.Ticker, s.Name, s.Exchange, string(s.AssetType), s.Active)
	if err != nil {
		return fmt.Errorf("insert symbol: %w", err)
	}
	return nil
}

func collectSymbols(rows *sql.Rows) ([]models.Symbol, error) {
	symbols := make([]models.Symbol, 0)
	for rows.Next() {
		s, err := scanSymbol(rows)
		if err != nil {
			return nil, fmt.Errorf("scan symbol: %w", err)
		}
		symbols = append(symbols, *s)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate symbols: %w", err)
	}
	return symbols, nil
}
