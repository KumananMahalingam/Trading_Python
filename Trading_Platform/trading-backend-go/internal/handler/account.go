package handler

import (
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
	"github.com/shopspring/decimal"

	"github.com/tradingapp/trading-backend/internal/apperr"
	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/middleware"
	"github.com/tradingapp/trading-backend/internal/service"
)

// AccountHandler serves the authenticated endpoints: profile, watchlist,
// portfolio, trades and alerts.
type AccountHandler struct {
	watchlist *service.WatchlistService
	portfolio *service.PortfolioService
	trades    *service.TradeService
	alerts    *service.AlertService
}

func NewAccountHandler(
	watchlist *service.WatchlistService,
	portfolio *service.PortfolioService,
	trades *service.TradeService,
	alerts *service.AlertService,
) *AccountHandler {
	return &AccountHandler{watchlist: watchlist, portfolio: portfolio, trades: trades, alerts: alerts}
}

// Me handles GET /api/user/me.
func (h *AccountHandler) Me(c *gin.Context) {
	user := middleware.CurrentUser(c)

	c.JSON(http.StatusOK, dto.UserProfileResponse{
		ID:       user.ID,
		Username: user.Username,
		Email:    user.Email,
	})
}

// GetWatchlist handles GET /api/watchlist.
func (h *AccountHandler) GetWatchlist(c *gin.Context) {
	items, err := h.watchlist.Get(c.Request.Context(), middleware.CurrentUser(c))
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, items)
}

// AddToWatchlist handles POST /api/watchlist/{ticker}.
func (h *AccountHandler) AddToWatchlist(c *gin.Context) {
	item, err := h.watchlist.Add(c.Request.Context(), middleware.CurrentUser(c), c.Param("ticker"))
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, item)
}

// RemoveFromWatchlist handles DELETE /api/watchlist/{ticker}.
func (h *AccountHandler) RemoveFromWatchlist(c *gin.Context) {
	if err := h.watchlist.Remove(c.Request.Context(), middleware.CurrentUser(c), c.Param("ticker")); err != nil {
		apperr.Respond(c, err)
		return
	}
	c.Status(http.StatusNoContent)
}

// GetPortfolio handles GET /api/portfolio.
func (h *AccountHandler) GetPortfolio(c *gin.Context) {
	portfolio, err := h.portfolio.Get(c.Request.Context(), middleware.CurrentUser(c))
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, portfolio)
}

// Buy handles POST /api/trades/buy.
func (h *AccountHandler) Buy(c *gin.Context) {
	req, err := bindTradeRequest(c)
	if err != nil {
		apperr.Respond(c, err)
		return
	}

	response, err := h.trades.Buy(c.Request.Context(), middleware.CurrentUser(c), *req)
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, response)
}

// Sell handles POST /api/trades/sell.
func (h *AccountHandler) Sell(c *gin.Context) {
	req, err := bindTradeRequest(c)
	if err != nil {
		apperr.Respond(c, err)
		return
	}

	response, err := h.trades.Sell(c.Request.Context(), middleware.CurrentUser(c), *req)
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, response)
}

// TradeHistory handles GET /api/trades/history.
func (h *AccountHandler) TradeHistory(c *gin.Context) {
	trades, err := h.trades.History(c.Request.Context(), middleware.CurrentUser(c))
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, trades)
}

// ListAlerts handles GET /api/alerts.
func (h *AccountHandler) ListAlerts(c *gin.Context) {
	alerts, err := h.alerts.List(c.Request.Context(), middleware.CurrentUser(c))
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, alerts)
}

// CreateAlert handles POST /api/alerts.
func (h *AccountHandler) CreateAlert(c *gin.Context) {
	var req dto.CreateAlertRequest
	if err := bindJSON(c, &req); err != nil {
		apperr.Respond(c, err)
		return
	}

	// @DecimalMin(value = "0.0", inclusive = false) in the Java DTO.
	if err := requirePositive("targetPrice", req.TargetPrice); err != nil {
		apperr.Respond(c, err)
		return
	}

	alert, err := h.alerts.Create(c.Request.Context(), middleware.CurrentUser(c), req)
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, alert)
}

// CancelAlert handles DELETE /api/alerts/{id}/cancel.
func (h *AccountHandler) CancelAlert(c *gin.Context) {
	id, err := alertID(c)
	if err != nil {
		apperr.Respond(c, err)
		return
	}

	if err := h.alerts.Cancel(c.Request.Context(), middleware.CurrentUser(c), id); err != nil {
		apperr.Respond(c, err)
		return
	}
	c.Status(http.StatusNoContent)
}

// DeleteAlert handles DELETE /api/alerts/{id}.
func (h *AccountHandler) DeleteAlert(c *gin.Context) {
	id, err := alertID(c)
	if err != nil {
		apperr.Respond(c, err)
		return
	}

	if err := h.alerts.Delete(c.Request.Context(), middleware.CurrentUser(c), id); err != nil {
		apperr.Respond(c, err)
		return
	}
	c.Status(http.StatusNoContent)
}

func alertID(c *gin.Context) (int64, error) {
	id, err := strconv.ParseInt(c.Param("id"), 10, 64)
	if err != nil {
		return 0, apperr.BadRequest("id: must be a number")
	}
	return id, nil
}

// bindTradeRequest decodes a trade and enforces the positive quantity and
// price the Java DTO required.
func bindTradeRequest(c *gin.Context) (*dto.TradeRequest, error) {
	var req dto.TradeRequest
	if err := bindJSON(c, &req); err != nil {
		return nil, err
	}

	if err := requirePositive("quantity", req.Quantity); err != nil {
		return nil, err
	}
	if err := requirePositive("price", req.Price); err != nil {
		return nil, err
	}

	return &req, nil
}

func requirePositive(field string, value *decimal.Decimal) error {
	if value == nil {
		return apperr.BadRequest(field + ": is required")
	}
	if !value.IsPositive() {
		return apperr.BadRequest(field + ": must be greater than 0")
	}
	return nil
}
