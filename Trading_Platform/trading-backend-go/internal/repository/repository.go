// Package repository contains the SQL data access layer that replaces the
// Spring Data JPA repositories.
//
// Numeric columns are read with an explicit ::text cast and parsed into
// decimal.Decimal. That keeps monetary values exact and avoids depending on
// how the driver happens to surface NUMERIC.
package repository

import (
	"errors"

	"github.com/shopspring/decimal"
)

// ErrNotFound is returned when a lookup matches no row. Services translate it
// into the same messages the Java services produced.
var ErrNotFound = errors.New("not found")

// parseDecimal converts a NUMERIC read as text into a decimal.
func parseDecimal(s string) (decimal.Decimal, error) {
	if s == "" {
		return decimal.Zero, nil
	}
	return decimal.NewFromString(s)
}
