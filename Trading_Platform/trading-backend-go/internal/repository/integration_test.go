package repository_test

// Integration tests for the SQL layer. They run only when TEST_DATABASE_URL
// points at a Postgres database with the Flyway migrations applied:
//
//	$env:TEST_DATABASE_URL = "postgres://postgres:pw@127.0.0.1:5432/trading?sslmode=disable"
//	go test ./internal/repository/...
//
// Without that variable the whole file is skipped, so the default `go test ./...`
// stays hermetic.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"sync/atomic"
	"testing"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/shopspring/decimal"

	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/repository"
)

var testDB *sql.DB

func TestMain(m *testing.M) {
	url := os.Getenv("TEST_DATABASE_URL")
	if url == "" {
		fmt.Println("TEST_DATABASE_URL not set, skipping repository integration tests")
		return
	}

	db, err := sql.Open("pgx", url)
	if err != nil {
		fmt.Printf("failed to open test database: %v\n", err)
		os.Exit(1)
	}
	if err := db.Ping(); err != nil {
		fmt.Printf("failed to reach test database: %v\n", err)
		os.Exit(1)
	}

	testDB = db
	code := m.Run()

	db.Close()
	os.Exit(code)
}

var userCounter atomic.Int64

// newUser inserts a throwaway user and returns it.
func newUser(t *testing.T) *models.User {
	t.Helper()

	n := userCounter.Add(1)
	unique := fmt.Sprintf("%d_%d", time.Now().UnixNano(), n)

	user := &models.User{
		Username: "user_" + unique,
		Email:    "user_" + unique + "@example.com",
		Password: "$2a$10$notarealhash",
		Role:     models.RoleUser,
	}

	if err := repository.NewUserRepository().Insert(context.Background(), testDB, user); err != nil {
		t.Fatalf("failed to insert user: %v", err)
	}
	if user.ID == 0 {
		t.Fatal("insert did not populate the user id")
	}

	return user
}

func requireDecimal(t *testing.T, got decimal.Decimal, want string) {
	t.Helper()

	expected, err := decimal.NewFromString(want)
	if err != nil {
		t.Fatalf("bad expected value %q: %v", want, err)
	}
	if !got.Equal(expected) {
		t.Errorf("value = %s, want %s", got.String(), want)
	}
}

func TestUserRepositoryRoundTrip(t *testing.T) {
	ctx := context.Background()
	users := repository.NewUserRepository()
	created := newUser(t)

	found, err := users.FindByEmail(ctx, testDB, created.Email)
	if err != nil {
		t.Fatalf("FindByEmail failed: %v", err)
	}

	if found.ID != created.ID || found.Username != created.Username {
		t.Errorf("found %+v, want id %d / username %s", found, created.ID, created.Username)
	}
	if found.Role != models.RoleUser {
		t.Errorf("role = %q, want USER", found.Role)
	}
	// The V2 migration default must survive the insert.
	requireDecimal(t, found.Balance, "100000.00")

	byUsername, err := users.FindByUsername(ctx, testDB, created.Username)
	if err != nil || byUsername.ID != created.ID {
		t.Errorf("FindByUsername returned %v / %v", byUsername, err)
	}

	for _, check := range []struct {
		name string
		fn   func() (bool, error)
		want bool
	}{
		{"ExistsByEmail hit", func() (bool, error) { return users.ExistsByEmail(ctx, testDB, created.Email) }, true},
		{"ExistsByEmail miss", func() (bool, error) { return users.ExistsByEmail(ctx, testDB, "nobody@example.com") }, false},
		{"ExistsByUsername hit", func() (bool, error) { return users.ExistsByUsername(ctx, testDB, created.Username) }, true},
		{"ExistsByUsername miss", func() (bool, error) { return users.ExistsByUsername(ctx, testDB, "nobody") }, false},
	} {
		got, err := check.fn()
		if err != nil {
			t.Errorf("%s errored: %v", check.name, err)
			continue
		}
		if got != check.want {
			t.Errorf("%s = %v, want %v", check.name, got, check.want)
		}
	}

	if _, err := users.FindByEmail(ctx, testDB, "missing@example.com"); !errors.Is(err, repository.ErrNotFound) {
		t.Errorf("expected ErrNotFound for a missing user, got %v", err)
	}

	// A balance with full scale must round-trip exactly.
	if err := users.UpdateBalance(ctx, testDB, created.ID, "12345.67"); err != nil {
		t.Fatalf("UpdateBalance failed: %v", err)
	}
	updated, err := users.FindByEmail(ctx, testDB, created.Email)
	if err != nil {
		t.Fatalf("reload failed: %v", err)
	}
	requireDecimal(t, updated.Balance, "12345.67")
}

func TestUserRepositoryRowLockInsideTransaction(t *testing.T) {
	ctx := context.Background()
	users := repository.NewUserRepository()
	created := newUser(t)

	tx, err := testDB.BeginTx(ctx, nil)
	if err != nil {
		t.Fatalf("BeginTx failed: %v", err)
	}
	defer tx.Rollback()

	locked, err := users.FindByIDForUpdate(ctx, tx, created.ID)
	if err != nil {
		t.Fatalf("FindByIDForUpdate failed: %v", err)
	}
	if locked.ID != created.ID {
		t.Errorf("locked user id = %d, want %d", locked.ID, created.ID)
	}
}

func TestSymbolRepository(t *testing.T) {
	ctx := context.Background()
	symbols := repository.NewSymbolRepository()

	ticker := fmt.Sprintf("TST%d", time.Now().UnixNano()%10000)
	symbol := &models.Symbol{
		Ticker:    ticker,
		Name:      "Integration Test Corp",
		Exchange:  "NASDAQ",
		AssetType: models.AssetStock,
		Active:    true,
	}

	if err := symbols.Insert(ctx, testDB, symbol); err != nil {
		t.Fatalf("Insert failed: %v", err)
	}
	// Inserting the same ticker again must be a no-op, not an error.
	if err := symbols.Insert(ctx, testDB, symbol); err != nil {
		t.Fatalf("second Insert should be ignored, got: %v", err)
	}

	found, err := symbols.FindByTicker(ctx, testDB, ticker)
	if err != nil {
		t.Fatalf("FindByTicker failed: %v", err)
	}
	if found.AssetType != models.AssetStock || !found.Active {
		t.Errorf("found %+v, want an active STOCK", found)
	}

	active, err := symbols.FindActive(ctx, testDB)
	if err != nil {
		t.Fatalf("FindActive failed: %v", err)
	}
	if !containsTicker(active, ticker) {
		t.Errorf("FindActive did not return %s", ticker)
	}

	// Search must be case-insensitive on both ticker and name.
	for _, query := range []string{ticker, "integration test"} {
		results, err := symbols.Search(ctx, testDB, query)
		if err != nil {
			t.Fatalf("Search(%q) failed: %v", query, err)
		}
		if !containsTicker(results, ticker) {
			t.Errorf("Search(%q) did not return %s", query, ticker)
		}
	}

	exists, err := symbols.ExistsByTicker(ctx, testDB, ticker)
	if err != nil || !exists {
		t.Errorf("ExistsByTicker = %v / %v, want true", exists, err)
	}

	if _, err := symbols.FindByTicker(ctx, testDB, "NOSUCH"); !errors.Is(err, repository.ErrNotFound) {
		t.Errorf("expected ErrNotFound, got %v", err)
	}
}

func containsTicker(symbols []models.Symbol, ticker string) bool {
	for _, s := range symbols {
		if s.Ticker == ticker {
			return true
		}
	}
	return false
}

// TestCandleRepositoryPreservesPrecision is the key check that NUMERIC values
// survive the round trip without being flattened through a float.
func TestCandleRepositoryPreservesPrecision(t *testing.T) {
	ctx := context.Background()
	candles := repository.NewCandleRepository()

	ticker := fmt.Sprintf("CDL%d", time.Now().UnixNano()%100000)
	base := time.Date(2026, 9, 22, 0, 0, 0, 0, time.UTC)

	input := []models.Candle{
		{
			Ticker: ticker, Interval: "1D", Timestamp: base.Add(24 * time.Hour),
			Open:  decimal.RequireFromString("64000.123456"),
			High:  decimal.RequireFromString("65999.999999"),
			Low:   decimal.RequireFromString("0.000001"),
			Close: decimal.RequireFromString("64500.500000"),
			// numeric(38,2) volume
			Volume: decimal.RequireFromString("123456789.12"),
		},
		{
			Ticker: ticker, Interval: "1D", Timestamp: base,
			Open:   decimal.RequireFromString("1.5"),
			High:   decimal.RequireFromString("2"),
			Low:    decimal.RequireFromString("1"),
			Close:  decimal.RequireFromString("1.75"),
			Volume: decimal.RequireFromString("10.00"),
		},
	}

	if err := candles.InsertMany(ctx, testDB, input); err != nil {
		t.Fatalf("InsertMany failed: %v", err)
	}
	// The unique key must make a re-insert a no-op.
	if err := candles.InsertMany(ctx, testDB, input); err != nil {
		t.Fatalf("duplicate InsertMany should be ignored, got: %v", err)
	}

	stored, err := candles.FindByTickerAndInterval(ctx, testDB, ticker, "1D")
	if err != nil {
		t.Fatalf("FindByTickerAndInterval failed: %v", err)
	}
	if len(stored) != 2 {
		t.Fatalf("got %d candles, want 2 (duplicates must be skipped)", len(stored))
	}

	// Ordering is ascending by timestamp, so the second input row comes first.
	if !stored[0].Timestamp.Equal(base) {
		t.Errorf("first candle timestamp = %s, want %s", stored[0].Timestamp, base)
	}
	if stored[1].Timestamp.Before(stored[0].Timestamp) {
		t.Error("candles are not ordered ascending by timestamp")
	}

	// Timestamps must come back in UTC so the JSON matches the Java output.
	for i, candle := range stored {
		if name, _ := candle.Timestamp.Zone(); name != "UTC" {
			t.Errorf("candle %d timestamp zone = %q, want UTC", i, name)
		}
	}

	requireDecimal(t, stored[1].Open, "64000.123456")
	requireDecimal(t, stored[1].High, "65999.999999")
	requireDecimal(t, stored[1].Low, "0.000001")
	requireDecimal(t, stored[1].Close, "64500.5")
	requireDecimal(t, stored[1].Volume, "123456789.12")

	if stored[1].Interval != "1D" {
		t.Errorf("interval = %q, want 1D (mapped from interval_type)", stored[1].Interval)
	}

	empty, err := candles.FindByTickerAndInterval(ctx, testDB, ticker, "4H")
	if err != nil {
		t.Fatalf("FindByTickerAndInterval for a missing interval failed: %v", err)
	}
	if len(empty) != 0 {
		t.Errorf("got %d candles for an unused interval, want 0", len(empty))
	}
}

func TestWatchlistRepository(t *testing.T) {
	ctx := context.Background()
	watchlists := repository.NewWatchlistRepository()
	symbols := repository.NewSymbolRepository()
	user := newUser(t)

	ticker := fmt.Sprintf("WL%d", time.Now().UnixNano()%100000)
	symbol := &models.Symbol{
		Ticker: ticker, Name: "Watchlist Co", Exchange: "BINANCE",
		AssetType: models.AssetCrypto, Active: true,
	}
	if err := symbols.Insert(ctx, testDB, symbol); err != nil {
		t.Fatalf("symbol insert failed: %v", err)
	}
	stored, err := symbols.FindByTicker(ctx, testDB, ticker)
	if err != nil {
		t.Fatalf("symbol reload failed: %v", err)
	}

	exists, err := watchlists.ExistsByUserAndTicker(ctx, testDB, user.ID, ticker)
	if err != nil || exists {
		t.Fatalf("expected no entry yet, got %v / %v", exists, err)
	}

	entry := &models.WatchlistEntry{UserID: user.ID, Symbol: *stored, AddedAt: time.Now().UTC()}
	if err := watchlists.Insert(ctx, testDB, entry); err != nil {
		t.Fatalf("Insert failed: %v", err)
	}

	entries, err := watchlists.FindByUser(ctx, testDB, user.ID)
	if err != nil {
		t.Fatalf("FindByUser failed: %v", err)
	}
	if len(entries) != 1 {
		t.Fatalf("got %d entries, want 1", len(entries))
	}
	// The join must hydrate the symbol, as the EAGER mapping did.
	if entries[0].Symbol.Ticker != ticker || entries[0].Symbol.AssetType != models.AssetCrypto {
		t.Errorf("symbol was not joined correctly: %+v", entries[0].Symbol)
	}

	exists, err = watchlists.ExistsByUserAndTicker(ctx, testDB, user.ID, ticker)
	if err != nil || !exists {
		t.Errorf("ExistsByUserAndTicker = %v / %v, want true", exists, err)
	}

	if err := watchlists.DeleteByUserAndTicker(ctx, testDB, user.ID, ticker); err != nil {
		t.Fatalf("DeleteByUserAndTicker failed: %v", err)
	}
	entries, err = watchlists.FindByUser(ctx, testDB, user.ID)
	if err != nil {
		t.Fatalf("FindByUser after delete failed: %v", err)
	}
	if len(entries) != 0 {
		t.Errorf("got %d entries after delete, want 0", len(entries))
	}
}

func TestAlertRepository(t *testing.T) {
	ctx := context.Background()
	alerts := repository.NewAlertRepository()
	user := newUser(t)

	ticker := fmt.Sprintf("ALT%d", time.Now().UnixNano()%100000)
	alert := &models.Alert{
		UserID:      user.ID,
		Ticker:      ticker,
		TargetPrice: decimal.RequireFromString("70000.123456"),
		Condition:   models.ConditionAbove,
		Status:      models.AlertActive,
		CreatedAt:   time.Now().UTC(),
	}

	if err := alerts.Insert(ctx, testDB, alert); err != nil {
		t.Fatalf("Insert failed: %v", err)
	}

	found, err := alerts.FindByID(ctx, testDB, alert.ID)
	if err != nil {
		t.Fatalf("FindByID failed: %v", err)
	}
	requireDecimal(t, found.TargetPrice, "70000.123456")
	if found.TriggeredAt != nil {
		t.Errorf("triggeredAt = %v, want nil for a new alert", found.TriggeredAt)
	}
	if found.Status != models.AlertActive || found.Condition != models.ConditionAbove {
		t.Errorf("found %+v, want an ACTIVE ABOVE alert", found)
	}

	byUser, err := alerts.FindByUser(ctx, testDB, user.ID)
	if err != nil || len(byUser) != 1 {
		t.Fatalf("FindByUser = %d rows / %v, want 1", len(byUser), err)
	}

	active, err := alerts.FindByTickerAndStatus(ctx, testDB, ticker, models.AlertActive)
	if err != nil || len(active) != 1 {
		t.Fatalf("FindByTickerAndStatus = %d rows / %v, want 1", len(active), err)
	}

	triggeredAt := time.Now().UTC().Truncate(time.Millisecond)
	if err := alerts.UpdateStatus(ctx, testDB, alert.ID, models.AlertTriggered, &triggeredAt); err != nil {
		t.Fatalf("UpdateStatus failed: %v", err)
	}

	reloaded, err := alerts.FindByID(ctx, testDB, alert.ID)
	if err != nil {
		t.Fatalf("reload failed: %v", err)
	}
	if reloaded.Status != models.AlertTriggered {
		t.Errorf("status = %q, want TRIGGERED", reloaded.Status)
	}
	if reloaded.TriggeredAt == nil {
		t.Fatal("triggeredAt should be set after triggering")
	}

	// A triggered alert must drop out of the active query.
	active, err = alerts.FindByTickerAndStatus(ctx, testDB, ticker, models.AlertActive)
	if err != nil {
		t.Fatalf("FindByTickerAndStatus failed: %v", err)
	}
	if len(active) != 0 {
		t.Errorf("got %d active alerts after triggering, want 0", len(active))
	}

	if err := alerts.DeleteByID(ctx, testDB, alert.ID); err != nil {
		t.Fatalf("DeleteByID failed: %v", err)
	}
	if _, err := alerts.FindByID(ctx, testDB, alert.ID); !errors.Is(err, repository.ErrNotFound) {
		t.Errorf("expected ErrNotFound after delete, got %v", err)
	}
}

func TestTradeAndHoldingRepositories(t *testing.T) {
	ctx := context.Background()
	trades := repository.NewTradeRepository()
	holdings := repository.NewHoldingRepository()
	user := newUser(t)

	ticker := "BTCUSDT"

	first := &models.Trade{
		UserID: user.ID, Ticker: ticker, Type: models.TradeBuy,
		// numeric(18,8) quantity, numeric(18,6) price
		Quantity:    decimal.RequireFromString("0.00000001"),
		Price:       decimal.RequireFromString("64000.123456"),
		TotalAmount: decimal.RequireFromString("0.000640"),
		ExecutedAt:  time.Now().UTC().Add(-time.Hour),
	}
	second := &models.Trade{
		UserID: user.ID, Ticker: ticker, Type: models.TradeSell,
		Quantity:    decimal.RequireFromString("0.5"),
		Price:       decimal.RequireFromString("70000"),
		TotalAmount: decimal.RequireFromString("35000"),
		ExecutedAt:  time.Now().UTC(),
	}

	for _, trade := range []*models.Trade{first, second} {
		if err := trades.Insert(ctx, testDB, trade); err != nil {
			t.Fatalf("trade insert failed: %v", err)
		}
	}

	history, err := trades.FindByUser(ctx, testDB, user.ID)
	if err != nil {
		t.Fatalf("FindByUser failed: %v", err)
	}
	if len(history) != 2 {
		t.Fatalf("got %d trades, want 2", len(history))
	}
	// Newest first.
	if history[0].ID != second.ID {
		t.Errorf("trades are not ordered by executed_at descending: %+v", history)
	}
	if history[0].Type != models.TradeSell {
		t.Errorf("type = %q, want SELL", history[0].Type)
	}
	requireDecimal(t, history[1].Quantity, "0.00000001")
	requireDecimal(t, history[1].Price, "64000.123456")

	if name, _ := history[0].ExecutedAt.Zone(); name != "UTC" {
		t.Errorf("executedAt zone = %q, want UTC", name)
	}

	// Upsert inserts on first call.
	holding := &models.Holding{
		UserID: user.ID, Ticker: ticker,
		Quantity:    decimal.RequireFromString("1.23456789"),
		AvgBuyPrice: decimal.RequireFromString("64000.123456"),
	}
	if err := holdings.Upsert(ctx, testDB, holding); err != nil {
		t.Fatalf("Upsert insert failed: %v", err)
	}
	if holding.ID == 0 {
		t.Fatal("Upsert did not return the holding id")
	}

	found, err := holdings.FindByUserAndTicker(ctx, testDB, user.ID, ticker)
	if err != nil {
		t.Fatalf("FindByUserAndTicker failed: %v", err)
	}
	requireDecimal(t, found.Quantity, "1.23456789")
	requireDecimal(t, found.AvgBuyPrice, "64000.123456")

	// Upsert updates on the unique (user_id, ticker) key rather than erroring.
	holding.Quantity = decimal.RequireFromString("2.5")
	holding.AvgBuyPrice = decimal.RequireFromString("65000")
	if err := holdings.Upsert(ctx, testDB, holding); err != nil {
		t.Fatalf("Upsert update failed: %v", err)
	}

	all, err := holdings.FindByUser(ctx, testDB, user.ID)
	if err != nil {
		t.Fatalf("FindByUser failed: %v", err)
	}
	if len(all) != 1 {
		t.Fatalf("got %d holdings, want 1 after upsert", len(all))
	}
	requireDecimal(t, all[0].Quantity, "2.5")
	requireDecimal(t, all[0].AvgBuyPrice, "65000")

	if err := holdings.Delete(ctx, testDB, holding.ID); err != nil {
		t.Fatalf("Delete failed: %v", err)
	}
	if _, err := holdings.FindByUserAndTicker(ctx, testDB, user.ID, ticker); !errors.Is(err, repository.ErrNotFound) {
		t.Errorf("expected ErrNotFound after delete, got %v", err)
	}
}
