"""
Alpha formula generation using Groq LLM
"""
import time

# llama-3.3-70b-versatile was retired by Groq. Override with the GROQ_MODEL setting.
GROQ_MODEL = "openai/gpt-oss-120b"


def generate_simple_alphas(ticker):
    """
    Fallback alphas that don't require related company sentiment

    Args:
        ticker: Stock ticker symbol

    Returns:
        str: Simple alpha formulas
    """
    return f"""
α1 = Return_5D
α2 = (RSI - 50) / 50
α3 = MACD - MACD_Signal
α4 = (close - SMA_20) / SMA_20
α5 = Return_5D + 0.1 * {ticker}_Sentiment
"""


def _alpha_lines(text):
    return [line for line in text.split('\n') if line.strip().startswith('α')]


def build_alpha_prompt(ticker, company_name, comprehensive_df, related_companies,
                       has_sentiment=True, sample_end_frac=0.7):
    """Build the Groq prompt from technical and sentiment features.

    Only rows before ``sample_end_frac`` of the frame are shown, which keeps
    the sample inside the training period (the train split is the first 70%
    of rows), so the LLM never sees validation or test data.
    """
    cutoff_idx = max(1, int(len(comprehensive_df) * sample_end_frac))
    sample_df = comprehensive_df.iloc[:cutoff_idx].tail(20).copy()

    important_cols = ['date', 'close', 'volume']
    tech_cols = [col for col in sample_df.columns if any(ind in col for ind in
                ['RSI', 'MACD', 'SMA_20', 'SMA_50', 'EMA_12', 'EMA_26', 'ATR',
                 'BB_Upper', 'BB_Lower', 'BB_Position', 'Return_5D', 'Return_20D',
                 'Volatility_20D', 'Volume_Ratio'])]
    important_cols.extend(tech_cols)
    if has_sentiment:
        important_cols.extend(col for col in sample_df.columns if 'Sentiment' in col)

    important_cols = list(dict.fromkeys(important_cols))
    sample_df = sample_df[[col for col in important_cols if col in sample_df.columns]]
    available_features = list(sample_df.columns)

    stock_features = [col for col in available_features if col in ['close', 'open', 'high', 'low', 'volume']]
    sentiment_features = [col for col in available_features if 'Sentiment' in col]
    target_sentiment = [col for col in sentiment_features if col.startswith(f'{ticker}_')]
    related_sentiment = [col for col in sentiment_features
                         if not col.startswith(f'{ticker}_') and not col.startswith('Sentiment_Div')]
    sentiment_divergence = [col for col in sentiment_features if col.startswith('Sentiment_Div')]

    vol_20d = f"{sample_df['Volatility_20D'].iloc[-1]:.4f}" if 'Volatility_20D' in sample_df.columns else 'N/A'
    ret_20d = f"{sample_df['Return_20D'].iloc[-1]:.4f}" if 'Return_20D' in sample_df.columns else 'N/A'
    stats_context = f"- Recent volatility (20D): {vol_20d}\n- Recent return (20D): {ret_20d}\n"
    target_col = f'{ticker}_Sentiment'
    if has_sentiment and target_col in sample_df.columns:
        s = sample_df[target_col]
        stats_context = (f"- {ticker} Sentiment: Mean={s.mean():.3f}, Std={s.std():.3f}, "
                         f"Range=[{s.min():.3f}, {s.max():.3f}]\n") + stats_context

    if related_companies:
        related_str = ", ".join(
            f"{t} (sentiment available)" if f'{t}_Sentiment' in sample_df.columns else f"{t} (no sentiment)"
            for t in related_companies
        )
    else:
        related_str = "None available"

    describe_cols = [c for c in sample_df.columns if c != 'date']
    df_summary = f"""
Date Range: {sample_df['date'].iloc[0]} to {sample_df['date'].iloc[-1]} ({len(sample_df)} days)

Recent Values (last 5 days):
{sample_df.tail(5).to_string(index=False, max_cols=10)}

Feature Statistics:
{sample_df[describe_cols].describe().loc[['mean', 'std', 'min', 'max']].to_string()}
"""

    if has_sentiment:
        data_sections = f"""3. SENTIMENT FEATURES ({len(sentiment_features)}):
   Target: {', '.join(target_sentiment[:5])}
   Related: {', '.join(related_sentiment[:5])}{'...' if len(related_sentiment) > 5 else ''}
   Divergences: {', '.join(sentiment_divergence[:5]) or 'none'}

RELATED COMPANIES: {related_str}"""
        source_rules = f"""   - α1: CROSS-COMPANY SENTIMENT DIVERGENCE (Sentiment_Div features) or target sentiment momentum
   - α2: TECHNICAL INDICATORS combined with TARGET SENTIMENT
   - α3: MOMENTUM + VOLATILITY (trend-following)
   - α4: MEAN REVERSION (Bollinger Band position, RSI extremes)
   - α5: MULTI-TIMEFRAME technical or VOLUME confirmation"""
        examples = f"""α1 = {f'Sentiment_Div_{ticker}_{related_companies[0]}' if related_companies else f'{ticker}_Sentiment_Momentum'} / (Volatility_20D + 1e-8)
α2 = (RSI - 50) / 50 + 0.3 * {ticker}_Sentiment_MA5
α3 = (MACD_Hist / (ATR + 1e-8)) * (1 + {ticker}_Sentiment_Change)
α4 = (0.5 - BB_Position) * Volume_Ratio
α5 = (SMA_5 - SMA_50) / close + 0.5 * Return_20D"""
        diversity = "   - Include at least ONE sentiment-based alpha\n"
    else:
        data_sections = "3. SENTIMENT FEATURES: none (no news coverage for this asset; do NOT use any *_Sentiment column)"
        source_rules = """   - α1: SHORT-TERM MOMENTUM normalized by volatility
   - α2: TREND (moving-average crossovers, MACD)
   - α3: MOMENTUM + VOLATILITY regime
   - α4: MEAN REVERSION (Bollinger Band position, RSI extremes)
   - α5: VOLUME confirmation or multi-timeframe returns"""
        examples = """α1 = Return_5D / (Volatility_20D + 1e-8)
α2 = (SMA_5 - SMA_50) / close
α3 = (MACD_Hist / (ATR + 1e-8)) * Volume_Ratio
α4 = (0.5 - BB_Position) * (1 + Volatility_20D)
α5 = Return_20D * Volume_Ratio"""
        diversity = ""

    return f"""You are a quantitative finance expert designing predictive alpha factors for algorithmic trading.

TASK: Generate 5 formulaic alpha signals to predict next-day {company_name} ({ticker}) price movements.

DATA AVAILABLE:
════════════════════════════════════════════════════════════════════════════════
1. PRICE FEATURES ({len(stock_features)}):
   {', '.join(stock_features)}

2. TECHNICAL INDICATORS:
   RSI, MACD, MACD_Signal, MACD_Hist, SMA_5/10/20/50/200, EMA_12/26, BB_Upper, BB_Lower,
   BB_Position, BB_Width, ATR, Volume_Ratio, Return_1D/5D/20D/60D, Volatility_5D/20D/60D,
   Momentum_5/10/20, ROC_5/10, OBV, MFI, Stochastic_K/D, Williams_R, ADX

{data_sections}

Statistical Context:
{stats_context}
SAMPLE DATA (20 trading days - summary):
{df_summary}

ALPHA GENERATION REQUIREMENTS:
════════════════════════════════════════════════════════════════════════════════

Generate exactly 5 diverse alpha formulas:

1. SIGNAL TYPES:
{source_rules}

2. FEATURE ENGINEERING BEST PRACTICES:
   ✓ Normalize by volatility (divide by ATR or Volatility_20D)
   ✓ Use ratio features (e.g., Volume_Ratio, BB_Position)
   ✓ Combine short-term and long-term signals (e.g., SMA_5 - SMA_50)

3. MATHEMATICAL VALIDITY:
   ✓ Avoid division by zero (add a small epsilon: 1e-8)
   ✓ Keep formulas executable (2-6 terms per alpha)
   ✓ Use ONLY the column names listed above, exactly as written
   ✓ Use only + - * / and parentheses; no function calls

4. STRATEGIC DIVERSITY:
   - Include at least ONE mean-reversion alpha
   - Include at least ONE momentum/trend alpha
{diversity}   - Avoid redundant alphas

OUTPUT FORMAT (respond with ONLY these 5 lines, no explanations):
α1 = <formula>
α2 = <formula>
α3 = <formula>
α4 = <formula>
α5 = <formula>

EXAMPLE ALPHAS (for reference only - generate NEW ones based on actual data):
{examples}

NOW GENERATE 5 UNIQUE ALPHAS FOR {company_name}:"""


def generate_alphas_with_groq(groq_client, ticker, company_name, comprehensive_df,
                              related_companies, max_retries=3, has_sentiment=True,
                              sleep=time.sleep, model=GROQ_MODEL):
    """
    Generate predictive alphas using Groq LLM.

    Returns the LLM's alpha text, or ``generate_simple_alphas(ticker)`` if every
    attempt fails or returns fewer than 5 alpha lines.
    """
    print(f"\n  Generating alphas for {company_name} ({ticker}) using Groq LLM...")
    prompt = build_alpha_prompt(ticker, company_name, comprehensive_df, related_companies,
                                has_sentiment=has_sentiment)

    for attempt in range(max_retries):
        try:
            print(f"    Attempt {attempt + 1}/{max_retries}...")
            response = groq_client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a quantitative finance expert specializing in alpha generation. Output ONLY the 5 alpha formulas in the exact format specified. No preamble, no explanations, no markdown formatting."
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.8,
                # reasoning models spend part of the budget before the answer
                max_tokens=4000,
                top_p=0.95
            )
            alpha_text = (response.choices[0].message.content or "").strip()
            alpha_lines = _alpha_lines(alpha_text)
            if len(alpha_lines) >= 5:
                print(f"    ✓ Successfully generated {len(alpha_lines)} alphas")
                return '\n'.join(alpha_lines)
            print(f"    ⚠ Only got {len(alpha_lines)} alphas, retrying...")
        except Exception as e:
            print(f"    ✗ Error on attempt {attempt + 1}: {e}")
        if attempt < max_retries - 1:
            sleep(2)

    print(f"    Falling back to simple alphas for {ticker}")
    return generate_simple_alphas(ticker)
