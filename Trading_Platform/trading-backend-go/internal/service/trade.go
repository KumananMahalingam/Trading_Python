package service

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/shopspring/decimal"

	"github.com/tradingapp/trading-backend/internal/apperr"
	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/repository"
)

// moneyScale matches the setScale(6, HALF_UP) the Java service applied.
const moneyScale = 6

// TradeService replaces TradeService.java. Buy and sell run in a single
// transaction, and the user row is locked so two concurrent orders cannot both
// spend the same balance.
type TradeService struct {
	db       *sql.DB
	trades   *repository.TradeRepository
	holdings *repository.HoldingRepository
	users    *repository.UserRepository
}

func NewTradeService(
	db *sql.DB,
	trades *repository.TradeRepository,
	holdings *repository.HoldingRepository,
	users *repository.UserRepository,
) *TradeService {
	return &TradeService{db: db, trades: trades, holdings: holdings, users: users}
}

// Buy debits the balance, updates the holding's average price and records the
// trade.
func (s *TradeService) Buy(ctx context.Context, user *models.User, req dto.TradeRequest) (*dto.TradeResponse, error) {
	return s.execute(ctx, user, req, models.TradeBuy)
}

// Sell credits the balance, reduces or clears the holding and records the
// trade.
func (s *TradeService) Sell(ctx context.Context, user *models.User, req dto.TradeRequest) (*dto.TradeResponse, error) {
	return s.execute(ctx, user, req, models.TradeSell)
}

func (s *TradeService) execute(
	ctx context.Context,
	user *models.User,
	req dto.TradeRequest,
	side models.TradeType,
) (*dto.TradeResponse, error) {
	ticker := strings.ToUpper(req.Ticker)
	quantity, price := *req.Quantity, *req.Price
	totalAmount := quantity.Mul(price).Round(moneyScale)

	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("begin trade transaction: %w", err)
	}
	defer tx.Rollback()

	// Re-read under a row lock: the copy on the request context may be stale.
	locked, err := s.users.FindByIDForUpdate(ctx, tx, user.ID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, apperr.NotFound("User not found")
		}
		return nil, err
	}

	var newBalance decimal.Decimal
	switch side {
	case models.TradeBuy:
		if locked.Balance.LessThan(totalAmount) {
			return nil, apperr.NotFound("Insufficient balance to complete this purchase")
		}
		newBalance = locked.Balance.Sub(totalAmount)

		if err := s.applyBuyToHolding(ctx, tx, user.ID, ticker, quantity, totalAmount); err != nil {
			return nil, err
		}

	case models.TradeSell:
		if err := s.applySellToHolding(ctx, tx, user.ID, ticker, quantity); err != nil {
			return nil, err
		}
		newBalance = locked.Balance.Add(totalAmount)

	default:
		return nil, fmt.Errorf("unsupported trade side %q", side)
	}

	if err := s.users.UpdateBalance(ctx, tx, user.ID, newBalance.String()); err != nil {
		return nil, err
	}

	trade := models.Trade{
		UserID:      user.ID,
		Ticker:      ticker,
		Type:        side,
		Quantity:    quantity,
		Price:       price,
		TotalAmount: totalAmount,
		ExecutedAt:  time.Now().UTC(),
	}
	if err := s.trades.Insert(ctx, tx, &trade); err != nil {
		return nil, err
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit trade: %w", err)
	}

	// Keep the in-request user consistent with what was committed.
	user.Balance = newBalance

	response := toTradeResponse(trade, &newBalance)
	return &response, nil
}

// applyBuyToHolding folds the purchase into the weighted average buy price.
func (s *TradeService) applyBuyToHolding(
	ctx context.Context,
	tx *sql.Tx,
	userID int64,
	ticker string,
	quantity, totalAmount decimal.Decimal,
) error {
	holding, err := s.holdings.FindByUserAndTicker(ctx, tx, userID, ticker)
	if errors.Is(err, repository.ErrNotFound) {
		holding = &models.Holding{
			UserID:      userID,
			Ticker:      ticker,
			Quantity:    decimal.Zero,
			AvgBuyPrice: decimal.Zero,
		}
	} else if err != nil {
		return err
	}

	newQuantity, newAvgPrice, err := foldPurchase(holding.Quantity, holding.AvgBuyPrice, quantity, totalAmount)
	if err != nil {
		return err
	}

	holding.Quantity = newQuantity
	holding.AvgBuyPrice = newAvgPrice

	return s.holdings.Upsert(ctx, tx, holding)
}

// foldPurchase recomputes a position's size and weighted average buy price
// after a purchase.
//
// The arithmetic mirrors the Java version exactly: the cost basis is summed
// then divided at scale 6 with half-up rounding, the same as
// BigDecimal.divide(q, 6, RoundingMode.HALF_UP).
func foldPurchase(
	existingQuantity, existingAvgPrice, addedQuantity, addedTotal decimal.Decimal,
) (newQuantity, newAvgPrice decimal.Decimal, err error) {
	newQuantity = existingQuantity.Add(addedQuantity)
	if !newQuantity.IsPositive() {
		return decimal.Zero, decimal.Zero, apperr.BadRequest("quantity: must be greater than 0")
	}

	existingCost := existingQuantity.Mul(existingAvgPrice)
	newAvgPrice = existingCost.Add(addedTotal).DivRound(newQuantity, moneyScale)

	return newQuantity, newAvgPrice, nil
}

// applySellToHolding reduces the position, deleting it once fully sold.
func (s *TradeService) applySellToHolding(
	ctx context.Context,
	tx *sql.Tx,
	userID int64,
	ticker string,
	quantity decimal.Decimal,
) error {
	holding, err := s.holdings.FindByUserAndTicker(ctx, tx, userID, ticker)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return apperr.NotFound("You don't own any " + ticker)
		}
		return err
	}

	if holding.Quantity.LessThan(quantity) {
		return apperr.NotFound(fmt.Sprintf("You only hold %s %s", holding.Quantity.String(), ticker))
	}

	remaining := holding.Quantity.Sub(quantity)
	if remaining.IsZero() {
		return s.holdings.Delete(ctx, tx, holding.ID)
	}

	holding.Quantity = remaining
	return s.holdings.Upsert(ctx, tx, holding)
}

// History returns the user's trades, newest first, without a balance snapshot.
func (s *TradeService) History(ctx context.Context, user *models.User) ([]dto.TradeResponse, error) {
	trades, err := s.trades.FindByUser(ctx, s.db, user.ID)
	if err != nil {
		return nil, err
	}

	items := make([]dto.TradeResponse, 0, len(trades))
	for _, t := range trades {
		items = append(items, toTradeResponse(t, nil))
	}
	return items, nil
}

func toTradeResponse(t models.Trade, balanceAfter *decimal.Decimal) dto.TradeResponse {
	return dto.TradeResponse{
		ID:           t.ID,
		Ticker:       t.Ticker,
		Type:         t.Type,
		Quantity:     t.Quantity,
		Price:        t.Price,
		TotalAmount:  t.TotalAmount,
		ExecutedAt:   t.ExecutedAt,
		BalanceAfter: balanceAfter,
	}
}
