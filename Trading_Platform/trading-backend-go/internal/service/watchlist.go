package service

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"time"

	"github.com/tradingapp/trading-backend/internal/apperr"
	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/repository"
)

// WatchlistService replaces WatchlistService.java.
type WatchlistService struct {
	db        *sql.DB
	watchlist *repository.WatchlistRepository
	symbols   *repository.SymbolRepository
}

func NewWatchlistService(
	db *sql.DB,
	watchlist *repository.WatchlistRepository,
	symbols *repository.SymbolRepository,
) *WatchlistService {
	return &WatchlistService{db: db, watchlist: watchlist, symbols: symbols}
}

// Get lists the user's watchlist, newest first.
func (s *WatchlistService) Get(ctx context.Context, user *models.User) ([]dto.WatchlistItemResponse, error) {
	entries, err := s.watchlist.FindByUser(ctx, s.db, user.ID)
	if err != nil {
		return nil, err
	}

	items := make([]dto.WatchlistItemResponse, 0, len(entries))
	for _, e := range entries {
		items = append(items, toWatchlistItem(e))
	}
	return items, nil
}

// Add puts a symbol on the user's watchlist.
func (s *WatchlistService) Add(ctx context.Context, user *models.User, ticker string) (*dto.WatchlistItemResponse, error) {
	upper := strings.ToUpper(ticker)

	symbol, err := s.symbols.FindByTicker(ctx, s.db, upper)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, apperr.NotFound("Symbol not found: " + ticker)
		}
		return nil, err
	}

	exists, err := s.watchlist.ExistsByUserAndTicker(ctx, s.db, user.ID, upper)
	if err != nil {
		return nil, err
	}
	if exists {
		return nil, apperr.NotFound(ticker + " is already in your watchlist")
	}

	entry := models.WatchlistEntry{
		UserID:  user.ID,
		Symbol:  *symbol,
		AddedAt: time.Now().UTC(),
	}
	if err := s.watchlist.Insert(ctx, s.db, &entry); err != nil {
		return nil, err
	}

	item := toWatchlistItem(entry)
	return &item, nil
}

// Remove drops a symbol from the user's watchlist.
func (s *WatchlistService) Remove(ctx context.Context, user *models.User, ticker string) error {
	upper := strings.ToUpper(ticker)

	exists, err := s.watchlist.ExistsByUserAndTicker(ctx, s.db, user.ID, upper)
	if err != nil {
		return err
	}
	if !exists {
		return apperr.NotFound(ticker + " is not in your watchlist")
	}

	return s.watchlist.DeleteByUserAndTicker(ctx, s.db, user.ID, upper)
}

func toWatchlistItem(e models.WatchlistEntry) dto.WatchlistItemResponse {
	return dto.WatchlistItemResponse{
		Ticker:    e.Symbol.Ticker,
		Name:      e.Symbol.Name,
		Exchange:  e.Symbol.Exchange,
		AssetType: e.Symbol.AssetType,
		AddedAt:   e.AddedAt,
	}
}
