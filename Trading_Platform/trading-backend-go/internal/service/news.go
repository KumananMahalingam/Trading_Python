package service

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/tradingapp/trading-backend/internal/config"
	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/external"
	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/repository"
)

const (
	// newsCacheTTL matches the 15 minute Redis TTL used before.
	newsCacheTTL = 15 * time.Minute
	// maxNewsArticles matches the previous limit(10).
	maxNewsArticles = 10
)

// NewsService replaces NewsService.java.
type NewsService struct {
	db      *sql.DB
	symbols *repository.SymbolRepository
	redis   *redis.Client
	http    *external.Client
	cfg     *config.Config
}

func NewNewsService(
	db *sql.DB,
	symbols *repository.SymbolRepository,
	rdb *redis.Client,
	httpClient *external.Client,
	cfg *config.Config,
) *NewsService {
	return &NewsService{db: db, symbols: symbols, redis: rdb, http: httpClient, cfg: cfg}
}

// GetNews returns cached or freshly fetched headlines for a ticker. Crypto
// tickers fall back to the general crypto feed, as Finnhub has no per-symbol
// crypto news.
func (s *NewsService) GetNews(ctx context.Context, ticker string) ([]dto.NewsArticleResponse, error) {
	upper := strings.ToUpper(ticker)
	cacheKey := "news:" + upper

	if cached, err := s.redis.Get(ctx, cacheKey).Result(); err == nil {
		var articles []dto.NewsArticleResponse
		if err := json.Unmarshal([]byte(cached), &articles); err == nil {
			return articles, nil
		}
		slog.Warn("failed to decode cached news", "ticker", upper)
	} else if !errors.Is(err, redis.Nil) {
		slog.Warn("redis lookup failed", "key", cacheKey, "error", err)
	}

	symbol, err := s.symbols.FindByTicker(ctx, s.db, upper)
	if err != nil && !errors.Is(err, repository.ErrNotFound) {
		return nil, err
	}

	isCrypto := symbol != nil && symbol.AssetType == models.AssetCrypto

	var articles []dto.NewsArticleResponse
	if isCrypto {
		articles = s.fetchCryptoNews(ctx)
	} else {
		articles = s.fetchStockNews(ctx, upper)
	}

	if len(articles) > 0 {
		if payload, err := json.Marshal(articles); err == nil {
			if err := s.redis.Set(ctx, cacheKey, payload, newsCacheTTL).Err(); err != nil {
				slog.Warn("failed to cache news", "ticker", upper, "error", err)
			}
		}
	}

	return articles, nil
}

func (s *NewsService) fetchStockNews(ctx context.Context, ticker string) []dto.NewsArticleResponse {
	today := time.Now()
	url := fmt.Sprintf("%s/company-news?symbol=%s&from=%s&to=%s&token=%s",
		s.cfg.FinnhubBaseURL, ticker,
		today.AddDate(0, 0, -7).Format("2006-01-02"),
		today.Format("2006-01-02"),
		s.cfg.FinnhubAPIKey)

	var items []external.FinnhubNewsItem
	if err := s.http.GetJSON(ctx, url, &items); err != nil {
		slog.Error("finnhub company news request failed", "ticker", ticker, "error", err)
		return nil
	}

	return toNewsResponses(items)
}

func (s *NewsService) fetchCryptoNews(ctx context.Context) []dto.NewsArticleResponse {
	url := fmt.Sprintf("%s/news?category=crypto&token=%s", s.cfg.FinnhubBaseURL, s.cfg.FinnhubAPIKey)

	var items []external.FinnhubNewsItem
	if err := s.http.GetJSON(ctx, url, &items); err != nil {
		slog.Error("finnhub crypto news request failed", "error", err)
		return nil
	}

	return toNewsResponses(items)
}

// toNewsResponses drops entries without a headline and caps the list.
func toNewsResponses(items []external.FinnhubNewsItem) []dto.NewsArticleResponse {
	articles := make([]dto.NewsArticleResponse, 0, maxNewsArticles)

	for _, item := range items {
		if strings.TrimSpace(item.Headline) == "" {
			continue
		}

		articles = append(articles, dto.NewsArticleResponse{
			Headline: item.Headline,
			Source:   item.Source,
			URL:      item.URL,
			Summary:  item.Summary,
			Image:    item.Image,
			Datetime: item.Datetime,
		})

		if len(articles) == maxNewsArticles {
			break
		}
	}

	return articles
}
