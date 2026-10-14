// Package dto contains the request and response payloads exposed over HTTP
// and pushed over WebSocket.
//
// Field names and validation rules reproduce the previous Java DTOs.
package dto

import (
	"time"

	"github.com/shopspring/decimal"

	"github.com/tradingapp/trading-backend/internal/models"
)

// ---------- Requests ----------

// RegisterRequest mirrors RegisterRequest.java.
type RegisterRequest struct {
	Username string `json:"username" binding:"required,min=3,max=20"`
	Email    string `json:"email" binding:"required,email"`
	Password string `json:"password" binding:"required,min=6"`
}

// LoginRequest mirrors LoginRequest.java.
type LoginRequest struct {
	Email    string `json:"email" binding:"required"`
	Password string `json:"password" binding:"required"`
}

// TradeRequest mirrors TradeRequest.java.
type TradeRequest struct {
	Ticker   string           `json:"ticker" binding:"required"`
	Quantity *decimal.Decimal `json:"quantity" binding:"required"`
	Price    *decimal.Decimal `json:"price" binding:"required"`
}

// CreateAlertRequest mirrors CreateAlertRequest.java.
type CreateAlertRequest struct {
	Ticker      string                `json:"ticker" binding:"required"`
	TargetPrice *decimal.Decimal      `json:"targetPrice" binding:"required"`
	Condition   models.AlertCondition `json:"condition" binding:"required,oneof=ABOVE BELOW"`
}

// ---------- Responses ----------

// AuthResponse mirrors AuthResponse.java. The refresh token is carried
// internally only; it travels to the browser as an HTTP-only cookie and is
// excluded from the JSON body exactly as @JsonIgnore did.
type AuthResponse struct {
	AccessToken  string `json:"accessToken"`
	RefreshToken string `json:"-"`
	Username     string `json:"username"`
	Email        string `json:"email"`
}

// UserProfileResponse mirrors UserProfileResponse.java.
type UserProfileResponse struct {
	ID       int64  `json:"id"`
	Username string `json:"username"`
	Email    string `json:"email"`
}

// WatchlistItemResponse mirrors WatchlistItemResponse.java.
type WatchlistItemResponse struct {
	Ticker    string           `json:"ticker"`
	Name      string           `json:"name"`
	Exchange  string           `json:"exchange"`
	AssetType models.AssetType `json:"assetType"`
	AddedAt   time.Time        `json:"addedAt"`
}

// AlertResponse mirrors AlertResponse.java.
type AlertResponse struct {
	ID          int64                 `json:"id"`
	Ticker      string                `json:"ticker"`
	TargetPrice decimal.Decimal       `json:"targetPrice"`
	Condition   models.AlertCondition `json:"condition"`
	Status      models.AlertStatus    `json:"status"`
	CreatedAt   time.Time             `json:"createdAt"`
	TriggeredAt *time.Time            `json:"triggeredAt"`
}

// HoldingResponse mirrors HoldingResponse.java.
type HoldingResponse struct {
	Ticker         string          `json:"ticker"`
	Quantity       decimal.Decimal `json:"quantity"`
	AvgBuyPrice    decimal.Decimal `json:"avgBuyPrice"`
	InvestedAmount decimal.Decimal `json:"investedAmount"`
}

// PortfolioResponse mirrors PortfolioResponse.java.
type PortfolioResponse struct {
	Balance  decimal.Decimal   `json:"balance"`
	Holdings []HoldingResponse `json:"holdings"`
}

// TradeResponse mirrors TradeResponse.java. BalanceAfter is omitted from
// history entries, matching the null the Java version emitted.
type TradeResponse struct {
	ID           int64            `json:"id"`
	Ticker       string           `json:"ticker"`
	Type         models.TradeType `json:"type"`
	Quantity     decimal.Decimal  `json:"quantity"`
	Price        decimal.Decimal  `json:"price"`
	TotalAmount  decimal.Decimal  `json:"totalAmount"`
	ExecutedAt   time.Time        `json:"executedAt"`
	BalanceAfter *decimal.Decimal `json:"balanceAfter"`
}

// NewsArticleResponse mirrors NewsArticleResponse.java.
type NewsArticleResponse struct {
	Headline string `json:"headline"`
	Source   string `json:"source"`
	URL      string `json:"url"`
	Summary  string `json:"summary"`
	Image    string `json:"image"`
	Datetime int64  `json:"datetime"`
}

// PriceResponse is pushed to /topic/price/{ticker}.
type PriceResponse struct {
	Ticker        string          `json:"ticker"`
	Price         decimal.Decimal `json:"price"`
	Change        decimal.Decimal `json:"change"`
	ChangePercent decimal.Decimal `json:"changePercent"`
	Timestamp     time.Time       `json:"timestamp"`
}

// CandleUpdateResponse is pushed to /topic/candle/{ticker}/{interval}.
// Timestamp stays a Unix-seconds number because lightweight-charts consumes
// it directly as a time value.
type CandleUpdateResponse struct {
	Ticker    string          `json:"ticker"`
	Interval  string          `json:"interval"`
	Timestamp int64           `json:"timestamp"`
	Open      decimal.Decimal `json:"open"`
	High      decimal.Decimal `json:"high"`
	Low       decimal.Decimal `json:"low"`
	Close     decimal.Decimal `json:"close"`
	Volume    decimal.Decimal `json:"volume"`
}

// ErrorResponse mirrors ErrorResponse.java.
type ErrorResponse struct {
	Status    int       `json:"status"`
	Message   string    `json:"message"`
	Timestamp time.Time `json:"timestamp"`
}
