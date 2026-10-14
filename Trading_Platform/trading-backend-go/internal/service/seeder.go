package service

import (
	"context"
	"database/sql"
	"fmt"
	"log/slog"

	"github.com/tradingapp/trading-backend/internal/config"
	"github.com/tradingapp/trading-backend/internal/external"
	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/repository"
)

// StockTickers and CryptoTickers mirror the lists DataSeeder.java seeded and
// MarketDataService.java subscribed to.
var (
	// JPM, PEP and TM have pre-trained prediction models (ml_service), so they
	// are seeded too.
	StockTickers = []string{"AAPL", "GOOGL", "TSLA", "MSFT", "AMZN", "JPM", "PEP", "TM"}

	CryptoTickers = []string{
		"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
		"ADAUSDT", "DOGEUSDT", "MATICUSDT", "DOTUSDT", "LINKUSDT",
		"AVAXUSDT", "UNIUSDT", "LTCUSDT", "ATOMUSDT", "XLMUSDT",
	}
)

// SeederService replaces DataSeeder.java. It runs once at startup and skips
// tickers that already exist.
type SeederService struct {
	db      *sql.DB
	symbols *repository.SymbolRepository
	http    *external.Client
	cfg     *config.Config
}

func NewSeederService(
	db *sql.DB,
	symbols *repository.SymbolRepository,
	httpClient *external.Client,
	cfg *config.Config,
) *SeederService {
	return &SeederService{db: db, symbols: symbols, http: httpClient, cfg: cfg}
}

// Run seeds stock and crypto symbols. Individual failures are logged and
// skipped so a provider outage cannot stop the service from booting.
func (s *SeederService) Run(ctx context.Context) {
	slog.Info("symbol seeding started")
	s.seedStocks(ctx)
	s.seedCrypto(ctx)
	slog.Info("symbol seeding completed")
}

func (s *SeederService) seedStocks(ctx context.Context) {
	for _, ticker := range StockTickers {
		exists, err := s.symbols.ExistsByTicker(ctx, s.db, ticker)
		if err != nil {
			slog.Error("failed to check symbol", "ticker", ticker, "error", err)
			continue
		}
		if exists {
			slog.Debug("symbol already present, skipping", "ticker", ticker)
			continue
		}

		url := fmt.Sprintf("%s/search?q=%s&token=%s", s.cfg.FinnhubBaseURL, ticker, s.cfg.FinnhubAPIKey)

		var response external.FinnhubSymbolResponse
		if err := s.http.GetJSON(ctx, url, &response); err != nil {
			slog.Error("failed to seed stock symbol", "ticker", ticker, "error", err)
			continue
		}

		for _, hit := range response.Result {
			if hit.Symbol != ticker {
				continue
			}

			exchange := hit.PrimaryExchange
			if exchange == "" {
				exchange = "NASDAQ"
			}

			symbol := &models.Symbol{
				Ticker:    hit.Symbol,
				Name:      hit.Description,
				Exchange:  exchange,
				AssetType: models.AssetStock,
				Active:    true,
			}
			if err := s.symbols.Insert(ctx, s.db, symbol); err != nil {
				slog.Error("failed to save stock symbol", "ticker", ticker, "error", err)
				break
			}

			slog.Info("saved stock symbol", "ticker", ticker)
			break
		}
	}
}

func (s *SeederService) seedCrypto(ctx context.Context) {
	for _, ticker := range CryptoTickers {
		exists, err := s.symbols.ExistsByTicker(ctx, s.db, ticker)
		if err != nil {
			slog.Error("failed to check symbol", "ticker", ticker, "error", err)
			continue
		}
		if exists {
			slog.Debug("symbol already present, skipping", "ticker", ticker)
			continue
		}

		url := fmt.Sprintf("%s/exchangeInfo?symbol=%s", s.cfg.BinanceBaseURL, ticker)

		var response external.BinanceSymbolResponse
		if err := s.http.GetJSON(ctx, url, &response); err != nil {
			slog.Error("failed to seed crypto symbol", "ticker", ticker, "error", err)
			continue
		}

		for _, hit := range response.Symbols {
			if hit.Symbol != ticker || hit.Status != "TRADING" {
				continue
			}

			symbol := &models.Symbol{
				Ticker:    hit.Symbol,
				Name:      hit.BaseAsset + " / " + hit.QuoteAsset,
				Exchange:  "BINANCE",
				AssetType: models.AssetCrypto,
				Active:    true,
			}
			if err := s.symbols.Insert(ctx, s.db, symbol); err != nil {
				slog.Error("failed to save crypto symbol", "ticker", ticker, "error", err)
				break
			}

			slog.Info("saved crypto symbol", "ticker", ticker)
			break
		}
	}
}
