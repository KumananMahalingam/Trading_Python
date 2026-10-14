// Command server boots the trading backend.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/tradingapp/trading-backend/internal/config"
	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/external"
	"github.com/tradingapp/trading-backend/internal/handler"
	"github.com/tradingapp/trading-backend/internal/repository"
	"github.com/tradingapp/trading-backend/internal/security"
	"github.com/tradingapp/trading-backend/internal/server"
	"github.com/tradingapp/trading-backend/internal/service"
	"github.com/tradingapp/trading-backend/internal/storage"
	"github.com/tradingapp/trading-backend/internal/ws"
)

func main() {
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo})))

	if err := run(); err != nil {
		slog.Error("fatal error", "error", err)
		os.Exit(1)
	}
}

func run() error {
	// Emit decimals as JSON numbers, the way Jackson serialised BigDecimal.
	dto.ConfigureJSONEncoding()

	cfg, err := config.Load()
	if err != nil {
		return err
	}

	// Cancelled on SIGINT/SIGTERM; shuts down the market data streams.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	db, err := storage.OpenPostgres(ctx, cfg)
	if err != nil {
		return err
	}
	defer db.Close()
	slog.Info("connected to postgres")

	rdb, err := storage.OpenRedis(ctx, cfg)
	if err != nil {
		return err
	}
	defer rdb.Close()
	slog.Info("connected to redis")

	// Repositories.
	users := repository.NewUserRepository()
	symbols := repository.NewSymbolRepository()
	candles := repository.NewCandleRepository()
	watchlists := repository.NewWatchlistRepository()
	alerts := repository.NewAlertRepository()
	trades := repository.NewTradeRepository()
	holdings := repository.NewHoldingRepository()

	// Infrastructure.
	tokens := security.NewTokenService(cfg.JWTSecret, cfg.AccessTokenTTL, cfg.RefreshTokenTTL)
	httpClient := external.NewClient()
	broker := ws.NewBroker()

	// Services.
	authService := service.NewAuthService(db, users, tokens, rdb, cfg.RefreshTokenTTL)
	symbolService := service.NewSymbolService(db, symbols)
	candleService := service.NewCandleService(db, candles, symbols, rdb, httpClient, cfg)
	newsService := service.NewNewsService(db, symbols, rdb, httpClient, cfg)
	watchlistService := service.NewWatchlistService(db, watchlists, symbols)
	portfolioService := service.NewPortfolioService(db, holdings)
	tradeService := service.NewTradeService(db, trades, holdings, users)
	alertService := service.NewAlertService(db, alerts)
	alertChecker := service.NewAlertCheckerService(db, alerts, broker)

	var mlClient service.PredictionClient
	if cfg.MLInternalToken != "" {
		mlClient = external.NewMLClient(cfg.MLServiceURL, cfg.MLInternalToken, cfg.MLTimeout)
		slog.Info("ml predictions enabled", "url", cfg.MLServiceURL)
	} else {
		slog.Warn("ML_INTERNAL_TOKEN not set; prediction endpoints will answer 503")
	}
	predictionService := service.NewPredictionService(mlClient, symbolService)

	// Startup work that used to hang off @PostConstruct.
	if cfg.SeedSymbols {
		go service.NewSeederService(db, symbols, httpClient, cfg).Run(ctx)
	}
	if cfg.StreamMarketData {
		service.NewMarketDataService(cfg, broker, alertChecker).Start(ctx)
	}

	router := server.New(cfg, tokens, authService, server.Handlers{
		Auth:       handler.NewAuthHandler(authService),
		Market:     handler.NewMarketHandler(symbolService, candleService, newsService),
		Account:    handler.NewAccountHandler(watchlistService, portfolioService, tradeService, alertService),
		Prediction: handler.NewPredictionHandler(predictionService),
	}, broker)

	httpServer := &http.Server{
		Addr:              ":" + cfg.Port,
		Handler:           router,
		ReadHeaderTimeout: 10 * time.Second,
		// No WriteTimeout: SockJS streaming responses are long-lived.
		IdleTimeout: 120 * time.Second,
	}

	serverErrors := make(chan error, 1)
	go func() {
		slog.Info("http server listening", "addr", httpServer.Addr)
		if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			serverErrors <- err
		}
	}()

	select {
	case err := <-serverErrors:
		return err
	case <-ctx.Done():
		slog.Info("shutdown signal received")
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	if err := httpServer.Shutdown(shutdownCtx); err != nil {
		return err
	}

	slog.Info("shutdown complete")
	return nil
}
