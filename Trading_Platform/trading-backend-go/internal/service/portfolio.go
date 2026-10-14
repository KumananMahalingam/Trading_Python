package service

import (
	"context"
	"database/sql"

	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/repository"
)

// PortfolioService replaces PortfolioService.java.
type PortfolioService struct {
	db       *sql.DB
	holdings *repository.HoldingRepository
}

func NewPortfolioService(db *sql.DB, holdings *repository.HoldingRepository) *PortfolioService {
	return &PortfolioService{db: db, holdings: holdings}
}

// Get returns the cash balance plus every holding with its invested amount.
func (s *PortfolioService) Get(ctx context.Context, user *models.User) (*dto.PortfolioResponse, error) {
	holdings, err := s.holdings.FindByUser(ctx, s.db, user.ID)
	if err != nil {
		return nil, err
	}

	items := make([]dto.HoldingResponse, 0, len(holdings))
	for _, h := range holdings {
		items = append(items, dto.HoldingResponse{
			Ticker:         h.Ticker,
			Quantity:       h.Quantity,
			AvgBuyPrice:    h.AvgBuyPrice,
			InvestedAmount: h.Quantity.Mul(h.AvgBuyPrice),
		})
	}

	return &dto.PortfolioResponse{
		Balance:  user.Balance,
		Holdings: items,
	}, nil
}
