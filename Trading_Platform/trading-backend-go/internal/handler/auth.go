package handler

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/tradingapp/trading-backend/internal/apperr"
	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/service"
)

// refreshCookieName, refreshCookiePath and refreshCookieMaxAge mirror the
// cookie AuthController.java issued, so existing browser sessions still work.
const (
	refreshCookieName   = "refreshToken"
	refreshCookiePath   = "/api/auth"
	refreshCookieMaxAge = 7 * 24 * 60 * 60
)

// AuthHandler serves /api/auth.
type AuthHandler struct {
	auth *service.AuthService
}

func NewAuthHandler(auth *service.AuthService) *AuthHandler {
	return &AuthHandler{auth: auth}
}

// Register handles POST /api/auth/register.
func (h *AuthHandler) Register(c *gin.Context) {
	var req dto.RegisterRequest
	if err := bindJSON(c, &req); err != nil {
		apperr.Respond(c, err)
		return
	}

	response, err := h.auth.Register(c.Request.Context(), req)
	if err != nil {
		apperr.Respond(c, err)
		return
	}

	h.setRefreshCookie(c, response.RefreshToken)
	c.JSON(http.StatusOK, response)
}

// Login handles POST /api/auth/login.
func (h *AuthHandler) Login(c *gin.Context) {
	var req dto.LoginRequest
	if err := bindJSON(c, &req); err != nil {
		apperr.Respond(c, err)
		return
	}

	response, err := h.auth.Login(c.Request.Context(), req)
	if err != nil {
		apperr.Respond(c, err)
		return
	}

	h.setRefreshCookie(c, response.RefreshToken)
	c.JSON(http.StatusOK, response)
}

// Refresh handles POST /api/auth/refresh using the HTTP-only cookie.
func (h *AuthHandler) Refresh(c *gin.Context) {
	response, err := h.auth.Refresh(c.Request.Context(), h.readRefreshCookie(c))
	if err != nil {
		apperr.Respond(c, err)
		return
	}

	h.setRefreshCookie(c, response.RefreshToken)
	c.JSON(http.StatusOK, response)
}

// Logout handles POST /api/auth/logout.
func (h *AuthHandler) Logout(c *gin.Context) {
	if err := h.auth.Logout(c.Request.Context(), h.readRefreshCookie(c)); err != nil {
		apperr.Respond(c, err)
		return
	}

	h.clearRefreshCookie(c)
	c.Status(http.StatusNoContent)
}

func (h *AuthHandler) readRefreshCookie(c *gin.Context) string {
	token, err := c.Cookie(refreshCookieName)
	if err != nil {
		return ""
	}
	return token
}

// setRefreshCookie writes the refresh token as an HTTP-only cookie. Secure is
// false because local development is served over plain HTTP; it should be
// enabled behind TLS in production.
func (h *AuthHandler) setRefreshCookie(c *gin.Context, token string) {
	c.SetSameSite(http.SameSiteLaxMode)
	c.SetCookie(refreshCookieName, token, refreshCookieMaxAge, refreshCookiePath, "", false, true)
}

func (h *AuthHandler) clearRefreshCookie(c *gin.Context) {
	c.SetSameSite(http.SameSiteLaxMode)
	c.SetCookie(refreshCookieName, "", -1, refreshCookiePath, "", false, true)
}
