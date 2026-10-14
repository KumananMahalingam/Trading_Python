package service

import (
	"testing"

	"github.com/shopspring/decimal"
)

func dec(t *testing.T, value string) decimal.Decimal {
	t.Helper()

	d, err := decimal.NewFromString(value)
	if err != nil {
		t.Fatalf("invalid decimal %q: %v", value, err)
	}
	return d
}

func TestFoldPurchase(t *testing.T) {
	tests := []struct {
		name        string
		existingQty string
		existingAvg string
		addedQty    string
		addedTotal  string
		wantQty     string
		wantAvg     string
	}{
		{
			name:        "first purchase sets the average to the fill price",
			existingQty: "0", existingAvg: "0",
			addedQty: "2", addedTotal: "200.000000",
			wantQty: "2", wantAvg: "100",
		},
		{
			name:        "second purchase blends the average",
			existingQty: "2", existingAvg: "100",
			addedQty: "1", addedTotal: "130.000000",
			wantQty: "3", wantAvg: "110",
		},
		{
			name:        "repeating division is rounded to six places",
			existingQty: "1", existingAvg: "10",
			addedQty: "2", addedTotal: "20.000001",
			wantQty: "3", wantAvg: "10",
		},
		{
			name:        "fractional crypto quantities are preserved",
			existingQty: "0.5", existingAvg: "30000",
			addedQty: "0.25", addedTotal: "10000.000000",
			wantQty: "0.75", wantAvg: "33333.333333",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			gotQty, gotAvg, err := foldPurchase(
				dec(t, tc.existingQty), dec(t, tc.existingAvg),
				dec(t, tc.addedQty), dec(t, tc.addedTotal),
			)
			if err != nil {
				t.Fatalf("foldPurchase returned error: %v", err)
			}

			if !gotQty.Equal(dec(t, tc.wantQty)) {
				t.Errorf("quantity = %s, want %s", gotQty, tc.wantQty)
			}
			if !gotAvg.Equal(dec(t, tc.wantAvg)) {
				t.Errorf("average price = %s, want %s", gotAvg, tc.wantAvg)
			}
		})
	}
}

func TestFoldPurchaseRejectsNonPositiveQuantity(t *testing.T) {
	if _, _, err := foldPurchase(decimal.Zero, decimal.Zero, decimal.Zero, decimal.Zero); err == nil {
		t.Error("expected an error for a zero resulting quantity")
	}
}

// TestTotalAmountRounding pins the setScale(6, HALF_UP) behaviour used when
// pricing an order.
func TestTotalAmountRounding(t *testing.T) {
	tests := []struct {
		quantity string
		price    string
		want     string
	}{
		{"3", "0.3333335", "1.000001"}, // half rounds away from zero
		{"2", "10.0000004", "20.000001"},
		{"1", "0.1", "0.1"},
		{"0.00000001", "50000", "0.0005"},
	}

	for _, tc := range tests {
		got := dec(t, tc.quantity).Mul(dec(t, tc.price)).Round(moneyScale)
		if !got.Equal(dec(t, tc.want)) {
			t.Errorf("%s * %s = %s, want %s", tc.quantity, tc.price, got, tc.want)
		}
	}
}
