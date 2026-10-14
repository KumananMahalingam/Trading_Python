package external_test

import (
	"encoding/json"
	"testing"

	"github.com/tradingapp/trading-backend/internal/external"
)

// TestNumberAcceptsStringsAndNumbers guards the regression where Binance's
// WebSocket streams send prices as bare JSON numbers while its REST endpoints
// quote them. Declaring these fields as plain strings made every live tick fail
// to decode.
func TestNumberAcceptsStringsAndNumbers(t *testing.T) {
	tests := []struct {
		name string
		json string
		want string
	}{
		{"quoted decimal", `"65746.45"`, "65746.45"},
		{"bare decimal", `65746.45`, "65746.45"},
		{"bare integer", `65746`, "65746"},
		{"quoted integer", `"65746"`, "65746"},
		{"high precision string", `"0.000000012345678"`, "0.000000012345678"},
		{"high precision number", `0.000000012345678`, "0.000000012345678"},
		{"negative", `-1.25`, "-1.25"},
		{"exponent", `1.5e3`, "1500"},
		{"null becomes zero", `null`, "0"},
		{"empty string becomes zero", `""`, "0"},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			var n external.Number
			if err := json.Unmarshal([]byte(tc.json), &n); err != nil {
				t.Fatalf("unmarshal %s failed: %v", tc.json, err)
			}
			if n.String() != tc.want {
				t.Errorf("got %s, want %s", n.String(), tc.want)
			}
		})
	}
}

// TestNumberDecodesBinanceTickerPayloads covers both shapes of the real payload.
func TestNumberDecodesBinanceTickerPayloads(t *testing.T) {
	type ticker struct {
		Symbol    string          `json:"s"`
		LastPrice external.Number `json:"c"`
	}

	for _, payload := range []string{
		`{"s":"BTCUSDT","c":"65746.45"}`,
		`{"s":"BTCUSDT","c":65746.45}`,
	} {
		var decoded ticker
		if err := json.Unmarshal([]byte(payload), &decoded); err != nil {
			t.Fatalf("failed to decode %s: %v", payload, err)
		}
		if decoded.Symbol != "BTCUSDT" || decoded.LastPrice.String() != "65746.45" {
			t.Errorf("decoded %s as %+v", payload, decoded)
		}
	}
}

func TestNumberRejectsGarbage(t *testing.T) {
	var n external.Number
	if err := json.Unmarshal([]byte(`"not-a-number"`), &n); err == nil {
		t.Error("expected an error for a non-numeric string")
	}
}
