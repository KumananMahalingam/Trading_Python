package external

// AlphaVantageCandleResponse mirrors AlphaVantageCandleResponse.java.
type AlphaVantageCandleResponse struct {
	TimeSeries map[string]AlphaVantageDailyData `json:"Time Series (Daily)"`
}

// AlphaVantageDailyData is one daily OHLCV entry.
type AlphaVantageDailyData struct {
	Open   string `json:"1. open"`
	High   string `json:"2. high"`
	Low    string `json:"3. low"`
	Close  string `json:"4. close"`
	Volume string `json:"5. volume"`
}

// BinanceSymbolResponse mirrors BinanceSymbolResponse.java.
type BinanceSymbolResponse struct {
	Symbols []BinanceSymbol `json:"symbols"`
}

// BinanceSymbol is one entry of the Binance exchangeInfo payload.
type BinanceSymbol struct {
	Symbol     string `json:"symbol"`
	BaseAsset  string `json:"baseAsset"`
	QuoteAsset string `json:"quoteAsset"`
	Status     string `json:"status"`
}

// FinnhubSymbolResponse mirrors FinnhubSymbolResponse.java.
type FinnhubSymbolResponse struct {
	Count  int             `json:"count"`
	Result []FinnhubSymbol `json:"result"`
}

// FinnhubSymbol is one search hit from Finnhub.
type FinnhubSymbol struct {
	Symbol          string `json:"symbol"`
	Description     string `json:"description"`
	Type            string `json:"type"`
	PrimaryExchange string `json:"primaryExchange"`
}

// FinnhubNewsItem mirrors FinnhubNewsItem.java.
type FinnhubNewsItem struct {
	Headline string `json:"headline"`
	Source   string `json:"source"`
	URL      string `json:"url"`
	Summary  string `json:"summary"`
	Image    string `json:"image"`
	Datetime int64  `json:"datetime"`
}
