// Package middleware holds the Gin middleware replacing the Spring Security
// filter chain.
package middleware

import (
	"context"
	"errors"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"

	"github.com/tradingapp/trading-backend/internal/apperr"
	"github.com/tradingapp/trading-backend/internal/models"
	"github.com/tradingapp/trading-backend/internal/repository"
	"github.com/tradingapp/trading-backend/internal/security"
)

// contextUserKey is where the authenticated user is stashed on the request.
const contextUserKey = "authenticatedUser"

// UserLoader resolves the user behind a token subject. It is the equivalent of
// UserDetailsServiceImpl.
type UserLoader interface {
	FindUserByEmail(ctx context.Context, email string) (*models.User, error)
}

// RequireAuth validates the bearer token and loads the user, aborting with 401
// when the token is missing, invalid, or points at a user that no longer
// exists.
//
// The Java stack answered 403 for unauthenticated calls because Spring
// Security had no entry point configured; 401 is used here since it is the
// correct status and the frontend does not branch on the distinction.
func RequireAuth(tokens *security.TokenService, loader UserLoader) gin.HandlerFunc {
	return func(c *gin.Context) {
		header := c.GetHeader("Authorization")
		if header == "" || !strings.HasPrefix(header, "Bearer ") {
			apperr.Respond(c, apperr.New(http.StatusUnauthorized, "Missing or malformed Authorization header"))
			return
		}

		token := strings.TrimSpace(strings.TrimPrefix(header, "Bearer "))
		email, err := tokens.ExtractSubject(token)
		if err != nil {
			apperr.Respond(c, apperr.New(http.StatusUnauthorized, "Invalid or expired token"))
			return
		}

		user, err := loader.FindUserByEmail(c.Request.Context(), email)
		if err != nil {
			if errors.Is(err, repository.ErrNotFound) {
				apperr.Respond(c, apperr.New(http.StatusUnauthorized, "User not found"))
				return
			}
			apperr.Respond(c, err)
			return
		}

		c.Set(contextUserKey, user)
		c.Next()
	}
}

// CurrentUser returns the authenticated user placed on the context by
// RequireAuth. It is only valid on protected routes.
func CurrentUser(c *gin.Context) *models.User {
	value, ok := c.Get(contextUserKey)
	if !ok {
		return nil
	}
	user, ok := value.(*models.User)
	if !ok {
		return nil
	}
	return user
}
