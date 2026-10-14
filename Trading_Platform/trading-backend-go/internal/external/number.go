package external

import (
	"bytes"
	"fmt"

	"github.com/shopspring/decimal"
)

// Number is a decimal that accepts either a JSON string or a JSON number.
//
// Market data providers are inconsistent about this: Binance's REST klines
// return quoted strings while its WebSocket streams send bare numbers, and the
// same field can switch between the two. The Java implementation read every
// value through Jackson's asText(), which papered over the difference; this
// type does the same while keeping full decimal precision, since it parses the
// raw JSON token rather than going through float64.
type Number struct {
	decimal.Decimal
}

// UnmarshalJSON accepts 1.5, "1.5" and null.
func (n *Number) UnmarshalJSON(data []byte) error {
	trimmed := bytes.TrimSpace(data)

	if len(trimmed) == 0 || bytes.Equal(trimmed, []byte("null")) {
		n.Decimal = decimal.Zero
		return nil
	}

	// Strip surrounding quotes when the value arrives as a string.
	if len(trimmed) >= 2 && trimmed[0] == '"' && trimmed[len(trimmed)-1] == '"' {
		trimmed = trimmed[1 : len(trimmed)-1]
	}

	if len(trimmed) == 0 {
		n.Decimal = decimal.Zero
		return nil
	}

	value, err := decimal.NewFromString(string(trimmed))
	if err != nil {
		return fmt.Errorf("parse number %q: %w", trimmed, err)
	}

	n.Decimal = value
	return nil
}
