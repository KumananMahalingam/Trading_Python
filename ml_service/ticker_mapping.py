"""Map trading-platform tickers to the symbols each data provider expects.

The platform uses plain tickers for stocks (``AAPL``) and Binance pairs for
crypto (``BTCUSDT``). yfinance wants ``BTC-USD`` and Polygon news wants
``X:BTCUSD`` for the same asset.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

STOCK = "STOCK"
CRYPTO = "CRYPTO"

# Mirrors CryptoTickers in Trading_Platform/trading-backend-go/internal/service/seeder.go
SUPPORTED_CRYPTO_PAIRS = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "MATICUSDT", "DOTUSDT", "LINKUSDT",
    "AVAXUSDT", "UNIUSDT", "LTCUSDT", "ATOMUSDT", "XLMUSDT",
)

# yfinance renamed some assets; MATIC trades as POL on Yahoo after the 2024 migration.
_YFINANCE_OVERRIDES = {"UNI": "UNI7083-USD", "MATIC": "POL28321-USD"}

_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "USD")
_STOCK_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{0,5}([.\-][A-Z]{1,2})?$")


class UnsupportedTickerError(ValueError):
    """Raised for tickers the model pipeline cannot fetch data for."""


@dataclass(frozen=True)
class TickerInfo:
    platform: str      # ticker as the trading platform knows it
    asset_type: str    # STOCK or CRYPTO
    yfinance: str      # symbol passed to yfinance
    news: str          # symbol passed to Polygon's news endpoint
    base: str          # base asset/company ticker, used for feature column names

    @property
    def is_crypto(self) -> bool:
        return self.asset_type == CRYPTO


def _crypto_base(pair: str) -> str:
    for suffix in _QUOTE_SUFFIXES:
        if pair.endswith(suffix) and len(pair) > len(suffix):
            return pair[: -len(suffix)]
    raise UnsupportedTickerError(f"Unsupported crypto pair: {pair}")


def resolve_ticker(ticker: str) -> TickerInfo:
    """Resolve a platform ticker to provider symbols.

    Crypto is limited to the pairs the platform seeds; anything else that looks
    like a quote-suffixed pair is rejected rather than guessed at.
    """
    if not isinstance(ticker, str):
        raise UnsupportedTickerError("Ticker must be a string")
    symbol = ticker.strip().upper()
    if not symbol:
        raise UnsupportedTickerError("Ticker is empty")

    if symbol in SUPPORTED_CRYPTO_PAIRS:
        base = _crypto_base(symbol)
        return TickerInfo(
            platform=symbol,
            asset_type=CRYPTO,
            yfinance=_YFINANCE_OVERRIDES.get(base, f"{base}-USD"),
            news=f"X:{base}USD",
            base=base,
        )

    if any(symbol.endswith(s) and len(symbol) > len(s) + 1 for s in ("USDT", "USDC", "BUSD")):
        raise UnsupportedTickerError(f"Unsupported crypto pair: {symbol}")

    if not _STOCK_PATTERN.match(symbol):
        raise UnsupportedTickerError(f"Invalid stock ticker: {symbol}")

    # yfinance uses '-' for share classes (BRK-B); Polygon uses '.' (BRK.B).
    return TickerInfo(
        platform=symbol,
        asset_type=STOCK,
        yfinance=symbol.replace(".", "-"),
        news=symbol.replace("-", "."),
        base=symbol,
    )
