package service

import (
	"context"
	"database/sql"
	"errors"
	"strings"

	"github.com/tradingapp/trading-backend/internal/apperr"
	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/repository"
)

// SymbolService replaces SymbolService.java.
type SymbolService struct {
	db      *sql.DB
	symbols *repository.SymbolRepository
}

func NewSymbolService(db *sql.DB, symbols *repository.SymbolRepository) *SymbolService {
	return &SymbolService{db: db, symbols: symbols}
}

// GetAll returns every active symbol.
func (s *SymbolService) GetAll(ctx context.Context) ([]models.Symbol, error) {
	return s.symbols.FindActive(ctx, s.db)
}

// Search matches on ticker or name, case-insensitively.
func (s *SymbolService) Search(ctx context.Context, query string) ([]models.Symbol, error) {
	return s.symbols.Search(ctx, s.db, query)
}

// GetByTicker resolves one symbol, uppercasing the input like before.
func (s *SymbolService) GetByTicker(ctx context.Context, ticker string) (*models.Symbol, error) {
	symbol, err := s.symbols.FindByTicker(ctx, s.db, strings.ToUpper(ticker))
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, apperr.NotFound("Symbol not found: " + ticker)
		}
		return nil, err
	}
	return symbol, nil
}
