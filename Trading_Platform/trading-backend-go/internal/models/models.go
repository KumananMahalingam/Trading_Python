// Package models holds the domain entities backing the database tables.
//
// JSON tags are chosen to reproduce byte-for-byte the payloads the previous
// Spring/Jackson implementation produced, because the React frontend is
// unchanged.
package models

import (
	"time"

	"github.com/shopspring/decimal"
)

// Role mirrors User.Role in the Java model.
type Role string

const (
	RoleUser  Role = "USER"
	RoleAdmin Role = "ADMIN"
)

// AssetType mirrors Symbol.AssetType.
type AssetType string

const (
	AssetStock  AssetType = "STOCK"
	AssetCrypto AssetType = "CRYPTO"
	AssetForex  AssetType = "FOREX"
)

// AlertCondition mirrors Alert.Condition.
type AlertCondition string

const (
	ConditionAbove AlertCondition = "ABOVE"
	ConditionBelow AlertCondition = "BELOW"
)

// AlertStatus mirrors Alert.Status.
type AlertStatus string

const (
	AlertActive    AlertStatus = "ACTIVE"
	AlertTriggered AlertStatus = "TRIGGERED"
	AlertCancelled AlertStatus = "CANCELLED"
)

// TradeType mirrors Trade.Type.
type TradeType string

const (
	TradeBuy  TradeType = "BUY"
	TradeSell TradeType = "SELL"
)

// DefaultBalance is the virtual starting balance granted by V2 of the schema.
const DefaultBalance = "100000.00"

// User maps to the users table. The password hash is never serialised.
type User struct {
	ID        int64           `json:"id"`
	Username  string          `json:"username"`
	Email     string          `json:"email"`
	Password  string          `json:"-"`
	Role      Role            `json:"role"`
	CreatedAt time.Time       `json:"createdAt"`
	Balance   decimal.Decimal `json:"balance"`
}

// Symbol maps to the symbols table.
type Symbol struct {
	ID        int64     `json:"id"`
	Ticker    string    `json:"ticker"`
	Name      string    `json:"name"`
	Exchange  string    `json:"exchange"`
	AssetType AssetType `json:"assetType"`
	Active    bool      `json:"active"`
}

// Candle maps to the candles table. Note the struct field is Interval while
// the column is interval_type, matching the original JPA mapping.
type Candle struct {
	ID        int64           `json:"id"`
	Ticker    string          `json:"ticker"`
	Interval  string          `json:"interval"`
	Timestamp time.Time       `json:"timestamp"`
	Open      decimal.Decimal `json:"open"`
	High      decimal.Decimal `json:"high"`
	Low       decimal.Decimal `json:"low"`
	Close     decimal.Decimal `json:"close"`
	Volume    decimal.Decimal `json:"volume"`
}

// WatchlistEntry maps to the watchlist table, joined with its symbol.
type WatchlistEntry struct {
	ID      int64
	UserID  int64
	Symbol  Symbol
	AddedAt time.Time
}

// Alert maps to the alerts table.
type Alert struct {
	ID          int64
	UserID      int64
	Ticker      string
	TargetPrice decimal.Decimal
	Condition   AlertCondition
	Status      AlertStatus
	CreatedAt   time.Time
	TriggeredAt *time.Time
}

// Trade maps to the trades table.
type Trade struct {
	ID          int64
	UserID      int64
	Ticker      string
	Type        TradeType
	Quantity    decimal.Decimal
	Price       decimal.Decimal
	TotalAmount decimal.Decimal
	ExecutedAt  time.Time
}

// Holding maps to the holdings table.
type Holding struct {
	ID          int64
	UserID      int64
	Ticker      string
	Quantity    decimal.Decimal
	AvgBuyPrice decimal.Decimal
}
