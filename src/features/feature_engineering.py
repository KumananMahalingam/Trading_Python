"""
Advanced feature engineering for stock prediction
"""
import pandas as pd
import numpy as np


def add_alternative_features_to_df(df, ticker, economic_data, sec_sentiment, earnings_sentiment):
    """
    Add all alternative data features to the main dataframe

    Args:
        df: Main stock dataframe with date column
        ticker: Stock ticker
        economic_data: DataFrame with economic indicators
        sec_sentiment: dict of {date: sentiment_score}
        earnings_sentiment: dict of {date: sentiment_score}

    Returns:
        Enhanced dataframe
    """
    df = df.copy()

    # Merge economic data
    if not economic_data.empty:
        df = df.merge(economic_data, on='date', how='left')

        # Forward fill economic data (released weekly/monthly)
        econ_cols = [col for col in economic_data.columns if col != 'date']
        df[econ_cols] = df[econ_cols].ffill()

    # Add SEC filing sentiment (forward fill between filings)
    df[f'{ticker}_SEC_Sentiment'] = df['date'].map(sec_sentiment)
    df[f'{ticker}_SEC_Sentiment'] = df[f'{ticker}_SEC_Sentiment'].ffill().fillna(0)

    # Add earnings sentiment (binary flag + sentiment)
    df[f'{ticker}_Earnings_Event'] = df['date'].isin(earnings_sentiment.keys()).astype(int)
    df[f'{ticker}_Earnings_Sentiment'] = df['date'].map(earnings_sentiment).fillna(0)

    # Days since last earnings (useful feature)
    earnings_dates = sorted([d for d in earnings_sentiment.keys()])
    def days_since_earnings(date):
        date_obj = pd.to_datetime(date)
        previous_earnings = [pd.to_datetime(d) for d in earnings_dates if pd.to_datetime(d) <= date_obj]
        if previous_earnings:
            return (date_obj - max(previous_earnings)).days
        return 999  # No previous earnings

    df[f'{ticker}_Days_Since_Earnings'] = df['date'].apply(days_since_earnings)

    new_features = len([c for c in df.columns if c not in ['date', 'open', 'high', 'low', 'close', 'volume']])
    print(f"  Added alternative features (total features: {new_features})")

    return df


def add_advanced_features(df, fill_missing=True):
    """Add advanced technical indicators and features.

    Args:
        df: Frame sorted by date.
        fill_missing: Forward-fill gaps and zero-fill the remaining warm-up NaNs.
            Values are never back-filled, since that copies future observations
            into earlier rows. Pass False to keep NaNs (the time-series prep
            trims warm-up rows itself before filling).
    """
    df = df.copy()

    if df.empty:
        return df

    # Ensure numeric columns
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # **FIX 1: Add Returns column FIRST (before alpha computation)**
    if 'close' in df.columns:
        df['Returns'] = df['close'].pct_change()

    # Price-based features
    if 'close' in df.columns:
        # Multi-timeframe returns
        for window in [1, 3, 5, 10, 20, 50]:
            df[f'return_{window}d'] = df['close'].pct_change(window)

        # Rolling volatility
        for window in [5, 10, 20, 50]:
            df[f'volatility_{window}d'] = df['close'].pct_change().rolling(window).std()

        # Price position within recent range
        for window in [20, 50, 200]:
            df[f'price_position_{window}d'] = (
                (df['close'] - df['low'].rolling(window).min()) /
                (df['high'].rolling(window).max() - df['low'].rolling(window).min())
            )

        # Trend strength (slope of linear regression)
        for window in [20, 50]:
            def calculate_slope(series):
                if len(series) < window:
                    return np.nan
                x = np.arange(len(series))
                slope = np.polyfit(x, series, 1)[0]
                return slope / np.std(series) if np.std(series) > 0 else 0

            df[f'trend_strength_{window}d'] = df['close'].rolling(window).apply(
                calculate_slope, raw=False
            )

    # Volume-based features
    if 'volume' in df.columns:
        df['volume_sma_20'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_sma_20']
        df['volume_zscore'] = (df['volume'] - df['volume'].rolling(20).mean()) / df['volume'].rolling(20).std()

        # Volume-price correlation
        df['volume_price_corr_10'] = df['volume'].rolling(10).corr(df['close'])

    # **FIX 2: Build features efficiently using lists, then concat (avoid fragmentation)**
    new_features = {}

    # Sentiment momentum features
    sentiment_cols = [col for col in df.columns if 'Sentiment' in col and not col.startswith('alpha_')]
    for col in sentiment_cols:
        new_features[f'{col}_MA5'] = df[col].rolling(5).mean()
        new_features[f'{col}_MA20'] = df[col].rolling(20).mean()
        new_features[f'{col}_Change'] = df[col].diff()
        new_features[f'{col}_Momentum'] = df[col] - df[col].rolling(5).mean()

    # Economic data features
    econ_cols = [col for col in df.columns if any(ind in col for ind in
                ['GDP', 'CPI', 'Unemployment', 'Fed_Funds', 'Treasury', 'VIX'])]
    for col in econ_cols:
        new_features[f'{col}_Change'] = df[col].pct_change()

    # **FIX 3: Add interaction features between top signals (Amazon sentiment + others)**
    # These are based on the analysis showing AMZN_Sentiment as top predictor
    if 'AMZN_Sentiment' in df.columns:
        # AMZN sentiment interactions with other stocks
        for ticker_col in ['AAPL_Sentiment', 'GOOG_Sentiment', 'MSFT_Sentiment']:
            if ticker_col in df.columns:
                new_features[f'AMZN_{ticker_col}_Interaction'] = df['AMZN_Sentiment'] * df[ticker_col]
                new_features[f'AMZN_{ticker_col}_Ratio'] = df['AMZN_Sentiment'] / (df[ticker_col].abs() + 1e-8)

        # AMZN sentiment with volatility
        if 'volatility_20d' in df.columns:
            new_features['AMZN_Sent_Vol_Interaction'] = df['AMZN_Sentiment'] * df['volatility_20d']

        # AMZN sentiment with returns
        if 'Returns' in df.columns:
            new_features['AMZN_Sent_Return_Interaction'] = df['AMZN_Sentiment'] * df['Returns']

    # Sentiment divergence interactions
    if 'Sentiment_Div_AAPL_GOOG' in df.columns:
        if 'Returns' in df.columns:
            new_features['SentDiv_Return_Interaction'] = df['Sentiment_Div_AAPL_GOOG'] * df['Returns']
        if 'volatility_20d' in df.columns:
            new_features['SentDiv_Vol_Interaction'] = df['Sentiment_Div_AAPL_GOOG'] * df['volatility_20d']

    # Skip features that already exist (prepare_dataframe_for_alpha creates
    # {ticker}_Sentiment_MA5/_Change with the same definitions); concatenating
    # them again would produce duplicate column names.
    new_features = {k: v for k, v in new_features.items() if k not in df.columns}

    # Add all new features at once (efficient)
    if new_features:
        new_features_df = pd.DataFrame(new_features, index=df.index)
        df = pd.concat([df, new_features_df], axis=1)

    # Market regime detection
    if 'close' in df.columns and 'volatility_20d' in df.columns:
        df['market_regime'] = 0
        bull_condition = (
            (df['close'] > df['close'].rolling(50).mean()) &
            (df['volatility_20d'] < df['volatility_20d'].rolling(50).mean())
        )
        bear_condition = (
            (df['close'] < df['close'].rolling(50).mean()) &
            (df['volatility_20d'] > df['volatility_20d'].rolling(50).mean())
        )
        df.loc[bull_condition, 'market_regime'] = 1
        df.loc[bear_condition, 'market_regime'] = -1

    # Forward fill only (no bfill: that would leak future values into warm-up rows)
    if fill_missing:
        for col in df.columns:
            if col not in ['date', 'target']:
                if pd.api.types.is_numeric_dtype(df[col]):
                    df[col] = df[col].replace([np.inf, -np.inf], np.nan).ffill().fillna(0)

    print(f"  Added {len(df.columns)} total features")
    return df


def prepare_dataframe_for_alpha(ticker, stock_df, daily_sentiments, related_companies,
                                alternative_data=None, economic_data=None):
    """ENHANCED: Prepare dataframe with sentiment lag features"""
    if stock_df.empty:
        return None

    from src.data.processors.technical_indicators import calculate_technical_indicators

    df = calculate_technical_indicators(stock_df.copy())

    sentiment_dict = {}
    for date, scores in daily_sentiments[ticker].items():
        sentiment_dict[date] = sum(scores) / len(scores) if scores else 0
    df[f'{ticker}_Sentiment'] = df['date'].map(sentiment_dict).fillna(0)

    df[f'{ticker}_Sentiment_Lag1'] = df[f'{ticker}_Sentiment'].shift(1)
    df[f'{ticker}_Sentiment_Lag5'] = df[f'{ticker}_Sentiment'].shift(5)

    df[f'{ticker}_Sentiment_Change'] = df[f'{ticker}_Sentiment'].diff()
    df[f'{ticker}_Sentiment_MA5'] = df[f'{ticker}_Sentiment'].rolling(5).mean()

    for related_ticker in related_companies:
        if related_ticker in daily_sentiments:
            related_sentiment_dict = {}
            for date, scores in daily_sentiments[related_ticker].items():
                related_sentiment_dict[date] = sum(scores) / len(scores) if scores else 0
            df[f'{related_ticker}_Sentiment'] = df['date'].map(related_sentiment_dict).fillna(0)

            df[f'Sentiment_Div_{ticker}_{related_ticker}'] = \
                df[f'{ticker}_Sentiment'] - df[f'{related_ticker}_Sentiment']

    if alternative_data and ticker in alternative_data:
        df = add_alternative_features_to_df(
            df, ticker,
            economic_data if economic_data is not None else pd.DataFrame(),
            alternative_data[ticker]['sec'],
            alternative_data[ticker]['earnings']
        )

    return df


def create_target(df):
    """
    Add ``target`` = next-period return, close[t+1] / close[t] - 1.

    Row t's features only use data up to t, and its label is strictly in the
    future. The last row keeps a NaN target: it is the window that predicts
    the next, not-yet-observed period, so inference needs it.
    """
    df = df.copy()
    if 'close' in df.columns:
        df['target'] = df['close'].pct_change(1).shift(-1)
    return df


def create_target_safely(df):
    """Legacy variant of :func:`create_target` that drops the unlabeled last row."""
    df = create_target(df)
    if 'target' in df.columns:
        df = df[:-1].copy()
    return df