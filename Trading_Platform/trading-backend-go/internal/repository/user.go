package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/shopspring/decimal"

	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/storage"
)

// UserRepository replaces UserRepository.java.
type UserRepository struct{}

func NewUserRepository() *UserRepository { return &UserRepository{} }

const userColumns = `id, username, email, password, role, created_at, balance::text`

func scanUser(row interface{ Scan(...any) error }) (*models.User, error) {
	var (
		u          models.User
		balanceStr string
	)
	if err := row.Scan(&u.ID, &u.Username, &u.Email, &u.Password, &u.Role, &u.CreatedAt, &balanceStr); err != nil {
		return nil, err
	}

	balance, err := parseDecimal(balanceStr)
	if err != nil {
		return nil, fmt.Errorf("parse user balance: %w", err)
	}
	u.Balance = balance

	return &u, nil
}

// FindByEmail mirrors findByEmail.
func (r *UserRepository) FindByEmail(ctx context.Context, q storage.Querier, email string) (*models.User, error) {
	row := q.QueryRowContext(ctx, `SELECT `+userColumns+` FROM users WHERE email = $1`, email)

	user, err := scanUser(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("find user by email: %w", err)
	}
	return user, nil
}

// FindByIDForUpdate reads a user while holding a row lock. It must be called
// inside a transaction; the trading flow uses it so concurrent orders cannot
// both spend the same balance.
func (r *UserRepository) FindByIDForUpdate(ctx context.Context, q storage.Querier, id int64) (*models.User, error) {
	row := q.QueryRowContext(ctx, `SELECT `+userColumns+` FROM users WHERE id = $1 FOR UPDATE`, id)

	user, err := scanUser(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("lock user row: %w", err)
	}
	return user, nil
}

// FindByUsername mirrors findByUsername.
func (r *UserRepository) FindByUsername(ctx context.Context, q storage.Querier, username string) (*models.User, error) {
	row := q.QueryRowContext(ctx, `SELECT `+userColumns+` FROM users WHERE username = $1`, username)

	user, err := scanUser(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("find user by username: %w", err)
	}
	return user, nil
}

// ExistsByEmail mirrors existsByEmail.
func (r *UserRepository) ExistsByEmail(ctx context.Context, q storage.Querier, email string) (bool, error) {
	var exists bool
	err := q.QueryRowContext(ctx, `SELECT EXISTS (SELECT 1 FROM users WHERE email = $1)`, email).Scan(&exists)
	if err != nil {
		return false, fmt.Errorf("check user email: %w", err)
	}
	return exists, nil
}

// ExistsByUsername mirrors existsByUsername.
func (r *UserRepository) ExistsByUsername(ctx context.Context, q storage.Querier, username string) (bool, error) {
	var exists bool
	err := q.QueryRowContext(ctx, `SELECT EXISTS (SELECT 1 FROM users WHERE username = $1)`, username).Scan(&exists)
	if err != nil {
		return false, fmt.Errorf("check username: %w", err)
	}
	return exists, nil
}

// Insert persists a new user, applying the created_at and default balance the
// JPA @PrePersist hook used to set.
func (r *UserRepository) Insert(ctx context.Context, q storage.Querier, u *models.User) error {
	if u.CreatedAt.IsZero() {
		u.CreatedAt = time.Now()
	}
	if u.Balance.IsZero() {
		u.Balance = mustDefaultBalance()
	}
	if u.Role == "" {
		u.Role = models.RoleUser
	}

	err := q.QueryRowContext(ctx,
		`INSERT INTO users (username, email, password, role, created_at, balance)
		 VALUES ($1, $2, $3, $4, $5, $6::text::numeric)
		 RETURNING id`,
		u.Username, u.Email, u.Password, string(u.Role), u.CreatedAt, u.Balance.String(),
	).Scan(&u.ID)
	if err != nil {
		return fmt.Errorf("insert user: %w", err)
	}
	return nil
}

// UpdateBalance persists a balance change made by the trading flow.
func (r *UserRepository) UpdateBalance(ctx context.Context, q storage.Querier, userID int64, balance string) error {
	if _, err := q.ExecContext(ctx,
		`UPDATE users SET balance = $1::text::numeric WHERE id = $2`, balance, userID); err != nil {
		return fmt.Errorf("update user balance: %w", err)
	}
	return nil
}

func mustDefaultBalance() decimal.Decimal {
	d, err := parseDecimal(models.DefaultBalance)
	if err != nil {
		panic(err)
	}
	return d
}
