package handler

import (
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"

	"github.com/tradingapp/trading-backend/internal/apperr"
	"github.com/tradingapp/trading-backend/internal/service"
)

// MarketHandler serves the public market data endpoints: symbols, candles and
// news.
type MarketHandler struct {
	symbols *service.SymbolService
	candles *service.CandleService
	news    *service.NewsService
}

func NewMarketHandler(
	symbols *service.SymbolService,
	candles *service.CandleService,
	news *service.NewsService,
) *MarketHandler {
	return &MarketHandler{symbols: symbols, candles: candles, news: news}
}

// ListSymbols handles GET /api/symbols.
func (h *MarketHandler) ListSymbols(c *gin.Context) {
	symbols, err := h.symbols.GetAll(c.Request.Context())
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, symbols)
}

// SearchSymbols handles GET /api/symbols/search?q=.
func (h *MarketHandler) SearchSymbols(c *gin.Context) {
	query := c.Query("q")
	if query == "" {
		apperr.Respond(c, apperr.BadRequest("q: query parameter is required"))
		return
	}

	symbols, err := h.symbols.Search(c.Request.Context(), query)
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, symbols)
}

// GetSymbol handles GET /api/symbols/{ticker}.
func (h *MarketHandler) GetSymbol(c *gin.Context) {
	symbol, err := h.symbols.GetByTicker(c.Request.Context(), c.Param("ticker"))
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, symbol)
}

// GetCandles handles GET /api/candles/{ticker}?interval=1D.
func (h *MarketHandler) GetCandles(c *gin.Context) {
	ticker := strings.ToUpper(c.Param("ticker"))
	interval := c.DefaultQuery("interval", "1D")

	candles, err := h.candles.GetCandles(c.Request.Context(), ticker, interval)
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, candles)
}

// GetNews handles GET /api/news/{ticker}.
func (h *MarketHandler) GetNews(c *gin.Context) {
	articles, err := h.news.GetNews(c.Request.Context(), c.Param("ticker"))
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, articles)
}
