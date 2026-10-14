// Package handler contains the Gin HTTP handlers replacing the Spring
// @RestController classes.
package handler

import (
	"errors"
	"fmt"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/go-playground/validator/v10"

	"github.com/tradingapp/trading-backend/internal/apperr"
)

// validationMessages reproduces the messages from the Bean Validation
// annotations so the UI keeps showing the same copy.
var validationMessages = map[string]map[string]string{
	"Username": {
		"required": "Username is required",
		"min":      "Username must be between 3 and 20 characters",
		"max":      "Username must be between 3 and 20 characters",
	},
	"Email": {
		"required": "Email is required",
		"email":    "Invalid email format",
	},
	"Password": {
		"required": "Password is required",
		"min":      "Password must be at least 6 characters",
	},
	"Ticker": {
		"required": "Ticker is required",
	},
	"Quantity": {
		"required": "Quantity is required",
	},
	"Price": {
		"required": "Price is required",
	},
	"TargetPrice": {
		"required": "Target price is required",
	},
	"Condition": {
		"required": "Condition is required",
		"oneof":    "Condition must be ABOVE or BELOW",
	},
	"Kind": {
		"required": "Kind is required",
		"oneof":    "Kind must be train or refresh",
	},
}

// bindJSON decodes and validates the body, translating failures into the same
// 400 envelope the Java validation handler produced.
func bindJSON(c *gin.Context, target any) error {
	if err := c.ShouldBindJSON(target); err != nil {
		return apperr.BadRequest(validationMessage(err))
	}
	return nil
}

// validationMessage renders the first field error, matching findFirst() in the
// old handler.
func validationMessage(err error) string {
	var validationErrs validator.ValidationErrors
	if !errors.As(err, &validationErrs) || len(validationErrs) == 0 {
		// Malformed JSON and type mismatches land here.
		return "Validation failed"
	}

	fieldErr := validationErrs[0]
	field := fieldErr.Field()

	if byTag, ok := validationMessages[field]; ok {
		if message, ok := byTag[fieldErr.Tag()]; ok {
			return fmt.Sprintf("%s: %s", lowerFirst(field), message)
		}
	}

	return fmt.Sprintf("%s: %s validation failed", lowerFirst(field), fieldErr.Tag())
}

func lowerFirst(value string) string {
	if value == "" {
		return value
	}
	return strings.ToLower(value[:1]) + value[1:]
}
