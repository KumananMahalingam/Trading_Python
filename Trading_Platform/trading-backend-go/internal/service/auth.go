// Package service holds the business logic ported from the Spring @Service
// classes.
package service

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/tradingapp/trading-backend/internal/apperr"
	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/repository"
	"github.com/tradingapp/trading-backend/internal/security"
)

// AuthService replaces AuthService.java.
type AuthService struct {
	db         *sql.DB
	users      *repository.UserRepository
	tokens     *security.TokenService
	redis      *redis.Client
	refreshTTL time.Duration
}

// NewAuthService wires the auth dependencies.
func NewAuthService(
	db *sql.DB,
	users *repository.UserRepository,
	tokens *security.TokenService,
	rdb *redis.Client,
	refreshTTL time.Duration,
) *AuthService {
	return &AuthService{db: db, users: users, tokens: tokens, redis: rdb, refreshTTL: refreshTTL}
}

// refreshTokenKey matches the Redis key the Java service used, so sessions
// created by either implementation are interchangeable.
func refreshTokenKey(userID int64) string {
	return fmt.Sprintf("session:refresh:%d", userID)
}

// Register creates the account and issues a token pair.
func (s *AuthService) Register(ctx context.Context, req dto.RegisterRequest) (*dto.AuthResponse, error) {
	emailTaken, err := s.users.ExistsByEmail(ctx, s.db, req.Email)
	if err != nil {
		return nil, err
	}
	if emailTaken {
		return nil, apperr.NotFound("Email already in use")
	}

	usernameTaken, err := s.users.ExistsByUsername(ctx, s.db, req.Username)
	if err != nil {
		return nil, err
	}
	if usernameTaken {
		return nil, apperr.NotFound("Username already taken")
	}

	hash, err := security.HashPassword(req.Password)
	if err != nil {
		return nil, err
	}

	user := &models.User{
		Username: req.Username,
		Email:    req.Email,
		Password: hash,
		Role:     models.RoleUser,
	}
	if err := s.users.Insert(ctx, s.db, user); err != nil {
		return nil, err
	}

	return s.issueTokens(ctx, user)
}

// Login verifies the credentials and issues a token pair.
func (s *AuthService) Login(ctx context.Context, req dto.LoginRequest) (*dto.AuthResponse, error) {
	user, err := s.users.FindByEmail(ctx, s.db, req.Email)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			// Spring hid the distinction between unknown user and wrong
			// password behind BadCredentialsException; keep doing that.
			return nil, apperr.Unauthorized("Invalid email or password")
		}
		return nil, err
	}

	if !security.CheckPassword(user.Password, req.Password) {
		return nil, apperr.Unauthorized("Invalid email or password")
	}

	return s.issueTokens(ctx, user)
}

// Refresh exchanges a valid refresh token for a new access token. The refresh
// token itself is reused, matching the previous behaviour.
func (s *AuthService) Refresh(ctx context.Context, refreshToken string) (*dto.AuthResponse, error) {
	if refreshToken == "" {
		return nil, apperr.Unauthorized("Refresh token cookie not found")
	}

	email, err := s.tokens.ExtractSubject(refreshToken)
	if err != nil {
		return nil, apperr.Unauthorized("Invalid or expired refresh token")
	}

	user, err := s.users.FindByEmail(ctx, s.db, email)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, apperr.NotFound("User not found")
		}
		return nil, err
	}

	stored, err := s.redis.Get(ctx, refreshTokenKey(user.ID)).Result()
	if errors.Is(err, redis.Nil) || stored != refreshToken {
		return nil, apperr.Unauthorized("Invalid or expired refresh token")
	}
	if err != nil {
		return nil, fmt.Errorf("read refresh token: %w", err)
	}

	accessToken, err := s.tokens.GenerateAccessToken(email)
	if err != nil {
		return nil, err
	}

	return &dto.AuthResponse{
		AccessToken:  accessToken,
		RefreshToken: refreshToken,
		Username:     user.Username,
		Email:        user.Email,
	}, nil
}

// Logout invalidates the stored refresh token.
func (s *AuthService) Logout(ctx context.Context, refreshToken string) error {
	if refreshToken == "" {
		return apperr.Unauthorized("Refresh token cookie not found")
	}

	email, err := s.tokens.ExtractSubject(refreshToken)
	if err != nil {
		return apperr.Unauthorized("Invalid or expired refresh token")
	}

	user, err := s.users.FindByEmail(ctx, s.db, email)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return apperr.NotFound("User not found")
		}
		return err
	}

	if err := s.redis.Del(ctx, refreshTokenKey(user.ID)).Err(); err != nil {
		return fmt.Errorf("delete refresh token: %w", err)
	}
	return nil
}

// FindUserByEmail lets the auth middleware resolve a token subject.
func (s *AuthService) FindUserByEmail(ctx context.Context, email string) (*models.User, error) {
	return s.users.FindByEmail(ctx, s.db, email)
}

func (s *AuthService) issueTokens(ctx context.Context, user *models.User) (*dto.AuthResponse, error) {
	accessToken, err := s.tokens.GenerateAccessToken(user.Email)
	if err != nil {
		return nil, err
	}

	refreshToken, err := s.tokens.GenerateRefreshToken(user.Email)
	if err != nil {
		return nil, err
	}

	if err := s.redis.Set(ctx, refreshTokenKey(user.ID), refreshToken, s.refreshTTL).Err(); err != nil {
		return nil, fmt.Errorf("store refresh token: %w", err)
	}

	return &dto.AuthResponse{
		AccessToken:  accessToken,
		RefreshToken: refreshToken,
		Username:     user.Username,
		Email:        user.Email,
	}, nil
}
