// Package security holds token signing and password hashing.
package security

import (
	"errors"
	"fmt"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/crypto/bcrypt"
)

// TokenService replaces JwtUtil.java. Tokens are HS256 with the user's email
// as the subject, matching the previous implementation so existing tokens and
// the frontend's expectations still line up.
type TokenService struct {
	secret     []byte
	accessTTL  time.Duration
	refreshTTL time.Duration
}

// NewTokenService builds the signer. The secret must be at least 32 bytes,
// which config validates before we get here.
func NewTokenService(secret string, accessTTL, refreshTTL time.Duration) *TokenService {
	return &TokenService{
		secret:     []byte(secret),
		accessTTL:  accessTTL,
		refreshTTL: refreshTTL,
	}
}

// GenerateAccessToken issues a short-lived access token for the given email.
func (s *TokenService) GenerateAccessToken(email string) (string, error) {
	return s.build(email, s.accessTTL)
}

// GenerateRefreshToken issues a long-lived refresh token for the given email.
func (s *TokenService) GenerateRefreshToken(email string) (string, error) {
	return s.build(email, s.refreshTTL)
}

func (s *TokenService) build(email string, ttl time.Duration) (string, error) {
	now := time.Now()

	token := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.RegisteredClaims{
		Subject:   email,
		IssuedAt:  jwt.NewNumericDate(now),
		ExpiresAt: jwt.NewNumericDate(now.Add(ttl)),
	})

	signed, err := token.SignedString(s.secret)
	if err != nil {
		return "", fmt.Errorf("sign token: %w", err)
	}
	return signed, nil
}

// ErrInvalidToken reports a malformed, expired, or wrongly signed token.
var ErrInvalidToken = errors.New("invalid or expired token")

// ExtractSubject validates the signature and expiry, then returns the subject
// (the user's email). This is the equivalent of extractUsername plus the
// isTokenValid expiry check.
func (s *TokenService) ExtractSubject(tokenString string) (string, error) {
	claims := &jwt.RegisteredClaims{}

	_, err := jwt.ParseWithClaims(tokenString, claims, func(t *jwt.Token) (any, error) {
		if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, fmt.Errorf("unexpected signing method %q", t.Method.Alg())
		}
		return s.secret, nil
	}, jwt.WithValidMethods([]string{jwt.SigningMethodHS256.Alg()}))
	if err != nil {
		return "", ErrInvalidToken
	}

	if claims.Subject == "" {
		return "", ErrInvalidToken
	}
	return claims.Subject, nil
}

// HashPassword produces a bcrypt hash compatible with Spring Security's
// BCryptPasswordEncoder, so password hashes already in the database keep
// verifying.
func HashPassword(plain string) (string, error) {
	hash, err := bcrypt.GenerateFromPassword([]byte(plain), bcrypt.DefaultCost)
	if err != nil {
		return "", fmt.Errorf("hash password: %w", err)
	}
	return string(hash), nil
}

// CheckPassword reports whether the plaintext matches the stored hash.
func CheckPassword(hash, plain string) bool {
	return bcrypt.CompareHashAndPassword([]byte(hash), []byte(plain)) == nil
}
