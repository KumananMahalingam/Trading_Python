package middleware

import (
	"log/slog"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/tradingapp/trading-backend/internal/dto"
)

// Recovery converts a panic into the same 500 envelope the Java catch-all
// exception handler produced, instead of dropping the connection.
func Recovery() gin.HandlerFunc {
	return gin.CustomRecoveryWithWriter(nil, func(c *gin.Context, recovered any) {
		slog.Error("panic recovered", "error", recovered, "path", c.Request.URL.Path)

		c.AbortWithStatusJSON(http.StatusInternalServerError, dto.ErrorResponse{
			Status:    http.StatusInternalServerError,
			Message:   "Something went wrong",
			Timestamp: time.Now().UTC(),
		})
	})
}

// RequestLogger emits one structured line per request.
func RequestLogger() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()

		slog.Info("request",
			"method", c.Request.Method,
			"path", c.Request.URL.Path,
			"status", c.Writer.Status(),
			"duration", time.Since(start).String(),
		)
	}
}
