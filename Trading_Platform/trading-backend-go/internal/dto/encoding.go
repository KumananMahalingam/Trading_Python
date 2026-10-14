package dto

import "github.com/shopspring/decimal"

// ConfigureJSONEncoding sets the process-wide decimal encoding used for every
// response.
//
// Jackson serialised BigDecimal as a bare JSON number, and shopspring/decimal
// defaults to a quoted string, so the default is flipped here. The frontend
// runs parseFloat over these values, but chart libraries are given some of
// them directly, so keeping them numeric preserves the original contract.
//
// It must be called once during startup, before any response is written.
func ConfigureJSONEncoding() {
	decimal.MarshalJSONWithoutQuotes = true
}
