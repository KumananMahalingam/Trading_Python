import pytest

from ml_service.ticker_mapping import (
    CRYPTO,
    STOCK,
    SUPPORTED_CRYPTO_PAIRS,
    UnsupportedTickerError,
    resolve_ticker,
)


@pytest.mark.parametrize("ticker", ["AAPL", "msft", " JPM ", "TM", "PEP", "GOOGL"])
def test_stocks_pass_through(ticker):
    info = resolve_ticker(ticker)
    expected = ticker.strip().upper()
    assert info.asset_type == STOCK
    assert not info.is_crypto
    assert info.platform == expected
    assert info.yfinance == expected
    assert info.news == expected
    assert info.base == expected


def test_share_class_uses_provider_separators():
    info = resolve_ticker("BRK.B")
    assert info.yfinance == "BRK-B"
    assert info.news == "BRK.B"


def test_all_seeded_crypto_pairs_map():
    assert len(SUPPORTED_CRYPTO_PAIRS) == 15
    for pair in SUPPORTED_CRYPTO_PAIRS:
        info = resolve_ticker(pair)
        base = pair[: -len("USDT")]
        assert info.asset_type == CRYPTO
        assert info.is_crypto
        assert info.base == base
        assert info.news == f"X:{base}USD"
        assert info.yfinance.endswith("-USD")


def test_btc_mapping():
    info = resolve_ticker("btcusdt")
    assert (info.platform, info.yfinance, info.news) == ("BTCUSDT", "BTC-USD", "X:BTCUSD")


def test_renamed_yahoo_symbols():
    assert resolve_ticker("UNIUSDT").yfinance == "UNI7083-USD"
    assert resolve_ticker("MATICUSDT").yfinance == "POL28321-USD"


@pytest.mark.parametrize("ticker", ["PEPEUSDT", "SHIBUSDC", "FOOBUSD"])
def test_unknown_crypto_pair_rejected(ticker):
    with pytest.raises(UnsupportedTickerError):
        resolve_ticker(ticker)


@pytest.mark.parametrize("ticker", ["", "   ", "AAPL;DROP", "../etc", "TOOLONGTICKER", "1ABC", None])
def test_invalid_tickers_rejected(ticker):
    with pytest.raises(UnsupportedTickerError):
        resolve_ticker(ticker)
