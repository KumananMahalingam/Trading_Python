// Package server assembles the HTTP surface: the Gin API and the SockJS
// WebSocket endpoint.
package server

import (
	"net/http"
	"time"

	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"

	"github.com/tradingapp/trading-backend/internal/config"
	"github.com/tradingapp/trading-backend/internal/handler"
	"github.com/tradingapp/trading-backend/internal/middleware"
	"github.com/tradingapp/trading-backend/internal/security"
	"github.com/tradingapp/trading-backend/internal/ws"
)

// Handlers groups the HTTP handlers the router needs.
type Handlers struct {
	Auth       *handler.AuthHandler
	Market     *handler.MarketHandler
	Account    *handler.AccountHandler
	Prediction *handler.PredictionHandler
}

// New builds the top-level http.Handler.
//
// The SockJS endpoint is mounted on a plain ServeMux rather than on Gin,
// because SockJS owns a whole path subtree (/ws/info, /ws/{server}/{session}/…)
// that does not map cleanly onto Gin's router.
func New(
	cfg *config.Config,
	tokens *security.TokenService,
	loader middleware.UserLoader,
	handlers Handlers,
	broker *ws.Broker,
) http.Handler {
	api := newGinEngine(cfg, tokens, loader, handlers)

	sockjsHandler := broker.Handler("/ws")

	mux := http.NewServeMux()
	mux.Handle("/ws", sockjsHandler)
	mux.Handle("/ws/", sockjsHandler)
	mux.Handle("/", api)

	return mux
}

func newGinEngine(
	cfg *config.Config,
	tokens *security.TokenService,
	loader middleware.UserLoader,
	handlers Handlers,
) *gin.Engine {
	engine := gin.New()
	engine.Use(middleware.Recovery(), middleware.RequestLogger())

	// Mirrors the CorsConfigurationSource bean.
	//
	// Spring expanded its "*" allowed-header wildcard into the headers the
	// browser actually asked for. A literal "*" is not honoured on credentialed
	// requests, so the headers the frontend sends are listed explicitly;
	// Authorization in particular must be present or every authenticated call
	// fails preflight.
	engine.Use(cors.New(cors.Config{
		AllowOrigins: cfg.AllowedOrigins,
		AllowMethods: []string{
			http.MethodGet, http.MethodPost, http.MethodPut,
			http.MethodDelete, http.MethodOptions,
		},
		AllowHeaders: []string{
			"Authorization",
			"Content-Type",
			"Accept",
			"Origin",
			"X-Requested-With",
		},
		ExposeHeaders:    []string{"Content-Length"},
		AllowCredentials: true,
		MaxAge:           12 * time.Hour,
	}))

	engine.GET("/healthz", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "UP"})
	})

	api := engine.Group("/api")

	// Public: /api/auth/**
	auth := api.Group("/auth")
	{
		auth.POST("/register", handlers.Auth.Register)
		auth.POST("/login", handlers.Auth.Login)
		auth.POST("/refresh", handlers.Auth.Refresh)
		auth.POST("/logout", handlers.Auth.Logout)
	}

	// Everything else requires a bearer token, as anyRequest().authenticated()
	// did before.
	protected := api.Group("")
	protected.Use(middleware.RequireAuth(tokens, loader))
	{
		protected.GET("/user/me", handlers.Account.Me)

		symbols := protected.Group("/symbols")
		{
			// Registered before the :ticker route so the literal wins.
			symbols.GET("/search", handlers.Market.SearchSymbols)
			symbols.GET("", handlers.Market.ListSymbols)
			symbols.GET("/:ticker", handlers.Market.GetSymbol)
		}

		protected.GET("/candles/:ticker", handlers.Market.GetCandles)
		protected.GET("/news/:ticker", handlers.Market.GetNews)

		// ML predictions. Jobs live under their own prefix so a job id can
		// never be mistaken for a ticker.
		protected.GET("/predictions/:ticker", handlers.Prediction.GetPrediction)
		protected.POST("/predictions/:ticker/jobs", handlers.Prediction.StartJob)
		protected.GET("/prediction-jobs/:id", handlers.Prediction.GetJob)

		watchlist := protected.Group("/watchlist")
		{
			watchlist.GET("", handlers.Account.GetWatchlist)
			watchlist.POST("/:ticker", handlers.Account.AddToWatchlist)
			watchlist.DELETE("/:ticker", handlers.Account.RemoveFromWatchlist)
		}

		protected.GET("/portfolio", handlers.Account.GetPortfolio)

		trades := protected.Group("/trades")
		{
			trades.POST("/buy", handlers.Account.Buy)
			trades.POST("/sell", handlers.Account.Sell)
			trades.GET("/history", handlers.Account.TradeHistory)
		}

		alerts := protected.Group("/alerts")
		{
			alerts.GET("", handlers.Account.ListAlerts)
			alerts.POST("", handlers.Account.CreateAlert)
			alerts.DELETE("/:id/cancel", handlers.Account.CancelAlert)
			alerts.DELETE("/:id", handlers.Account.DeleteAlert)
		}
	}

	return engine
}
