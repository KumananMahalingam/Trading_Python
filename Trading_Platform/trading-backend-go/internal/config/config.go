// Package config loads application configuration from the environment.
//
// It mirrors the settings previously held in application.yaml so the Go
// service can be dropped in with the same .env file the Java service used.
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config holds every tunable used by the service.
type Config struct {
	Port string

	DatabaseURL  string
	MaxOpenConns int
	MaxIdleConns int

	RedisAddr     string
	RedisPassword string
	RedisDB       int

	JWTSecret       string
	AccessTokenTTL  time.Duration
	RefreshTokenTTL time.Duration

	FinnhubAPIKey  string
	FinnhubBaseURL string
	FinnhubWSURL   string

	BinanceBaseURL string
	BinanceWSURL   string

	AlphaVantageAPIKey  string
	AlphaVantageBaseURL string

	AllowedOrigins []string

	// ML prediction service (ml_service in the Python repo). Predictions are
	// disabled (503) when MLInternalToken is empty.
	MLServiceURL    string
	MLInternalToken string
	MLTimeout       time.Duration

	// SeedSymbols mirrors the old DataSeeder @PostConstruct behaviour.
	SeedSymbols bool
	// StreamMarketData mirrors the old MarketDataService @PostConstruct behaviour.
	StreamMarketData bool
}

// Load reads configuration from the environment, applying the same defaults
// the Spring configuration used. It returns an error when a required secret is
// missing so the process fails fast instead of serving broken auth.
func Load() (*Config, error) {
	cfg := &Config{
		Port: env("PORT", "8080"),

		DatabaseURL:  env("DATABASE_URL", "postgres://dev:dev@localhost:5432/trading?sslmode=disable"),
		MaxOpenConns: envInt("DB_MAX_OPEN_CONNS", 10),
		MaxIdleConns: envInt("DB_MAX_IDLE_CONNS", 5),

		RedisAddr:     env("REDIS_ADDR", "localhost:6379"),
		RedisPassword: env("REDIS_PASSWORD", ""),
		RedisDB:       envInt("REDIS_DB", 0),

		JWTSecret: env("JWT_SECRET", ""),
		// application.yaml used expiration-ms: 3600000 and
		// refresh-expiration-ms: 604800000.
		AccessTokenTTL:  envDuration("JWT_EXPIRATION_MS", time.Hour),
		RefreshTokenTTL: envDuration("JWT_REFRESH_EXPIRATION_MS", 7*24*time.Hour),

		FinnhubAPIKey:  env("FINNHUB_API_KEY", ""),
		FinnhubBaseURL: env("FINNHUB_BASE_URL", "https://finnhub.io/api/v1"),
		FinnhubWSURL:   env("FINNHUB_WS_URL", "wss://ws.finnhub.io"),

		BinanceBaseURL: env("BINANCE_BASE_URL", "https://api.binance.com/api/v3"),
		BinanceWSURL:   env("BINANCE_WS_URL", "wss://data-stream.binance.vision"),

		AlphaVantageAPIKey:  env("ALPHA_VANTAGE_API_KEY", ""),
		AlphaVantageBaseURL: env("ALPHA_VANTAGE_BASE_URL", "https://www.alphavantage.co"),

		AllowedOrigins: envList("CORS_ALLOWED_ORIGINS",
			"http://localhost:5173",
			"http://localhost:5174",
			"http://localhost:5175",
			"http://localhost:5176",
		),

		MLServiceURL:    env("ML_SERVICE_URL", "http://127.0.0.1:8000"),
		MLInternalToken: env("ML_INTERNAL_TOKEN", ""),
		MLTimeout:       envDuration("ML_TIMEOUT_MS", 10*time.Second),

		SeedSymbols:      envBool("SEED_SYMBOLS", true),
		StreamMarketData: envBool("STREAM_MARKET_DATA", true),
	}

	if cfg.JWTSecret == "" {
		return nil, fmt.Errorf("JWT_SECRET is required")
	}
	// Keys.hmacShaKeyFor in the Java service rejected keys under 256 bits.
	// Keep the same floor so an existing .env behaves identically.
	if len(cfg.JWTSecret) < 32 {
		return nil, fmt.Errorf("JWT_SECRET must be at least 32 bytes for HS256, got %d", len(cfg.JWTSecret))
	}

	return cfg, nil
}

func env(key, fallback string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

func envBool(key string, fallback bool) bool {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			return b
		}
	}
	return fallback
}

// envDuration reads a millisecond count, matching the *-ms keys used before.
func envDuration(key string, fallback time.Duration) time.Duration {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		if ms, err := strconv.ParseInt(v, 10, 64); err == nil && ms > 0 {
			return time.Duration(ms) * time.Millisecond
		}
	}
	return fallback
}

func envList(key string, fallback ...string) []string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		parts := strings.Split(v, ",")
		out := make([]string, 0, len(parts))
		for _, p := range parts {
			if p = strings.TrimSpace(p); p != "" {
				out = append(out, p)
			}
		}
		if len(out) > 0 {
			return out
		}
	}
	return fallback
}
