// Package apperr provides the typed HTTP error used across the service.
//
// The status codes intentionally reproduce GlobalExceptionHandler.java so the
// frontend keeps seeing the responses it was written against. Notably the Java
// handler mapped every plain RuntimeException to 404, which is why NotFound is
// the default for business rule violations such as an insufficient balance.
package apperr

import (
	"errors"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/tradingapp/trading-backend/internal/dto"
)

// Error carries an HTTP status alongside a client-safe message.
type Error struct {
	Status  int
	Message string
}

func (e *Error) Error() string { return e.Message }

// New builds an error with an explicit status.
func New(status int, message string) *Error {
	return &Error{Status: status, Message: message}
}

// NotFound is the equivalent of the Java RuntimeException path (HTTP 404).
func NotFound(message string) *Error {
	return New(http.StatusNotFound, message)
}

// BadRequest maps to the validation path (HTTP 400).
func BadRequest(message string) *Error {
	return New(http.StatusBadRequest, message)
}

// Unauthorized maps to the bad-credentials path (HTTP 401).
func Unauthorized(message string) *Error {
	return New(http.StatusUnauthorized, message)
}

// Respond writes err as an ErrorResponse. Unrecognised errors become a 500
// with a generic message, matching the old catch-all handler.
func Respond(c *gin.Context, err error) {
	var appErr *Error
	if !errors.As(err, &appErr) {
		appErr = New(http.StatusInternalServerError, "Something went wrong")
	}

	c.AbortWithStatusJSON(appErr.Status, dto.ErrorResponse{
		Status:    appErr.Status,
		Message:   appErr.Message,
		Timestamp: time.Now().UTC(),
	})
}
