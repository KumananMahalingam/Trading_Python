"""
Main pipeline script for LSTM Trading Platform
Replaces the original integrated_pipeline.py with new modular structure
"""

import time
import torch
import pandas as pd
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter
import re
import spacy
from polygon import RESTClient
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from groq import Groq
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import from new structure
import config.secret_key as secret_key
from config import settings
from src.data.processors.company_validator import (
    load_company_tickers_json,
    validate_company_exists
)
from src.data.collectors.news_collector import (
    fetch_news,
    fetch_sentiment_for_ticker
)
from src.data.collectors.stock_collector import (
    fetch_stock_data,
    validate_ticker_quality
)
from src.data.collectors.fred_collector import fetch_fred_data
from src.data.collectors.sec_collector import fetch_all_alternative_data
from src.data.storage.excel_handler import (
    save_all_data_to_excel,
    load_all_data_from_excel
)
from src.data.storage.cache_manager import check_essential_data_only
from src.data.processors.sentiment_analyzer import analyze_sentiment
from src.features.alpha_generator import (
    generate_simple_alphas,
    generate_alphas_with_groq
)
from src.features.feature_engineering import prepare_dataframe_for_alpha
from src.models.training.trainer import train_stock_predictor, train_ensemble
from src.utils.helpers import is_same_company

# Initialize clients
client = RESTClient(secret_key.API_KEY)
groq_client = Groq(api_key=secret_key.GROQ_API_KEY)
nlp = spacy.load("en_core_web_sm")
sia = SentimentIntensityAnalyzer()

# Load configuration
companies = settings.COMPANIES
COMPANY_ALIASES = settings.COMPANY_ALIASES
DATA_FILE = settings.DATA_FILE
FORCE_REFETCH = settings.FORCE_REFETCH
FORCE_REGENERATE_ALPHAS = settings.FORCE_REGENERATE_ALPHAS


def main():
    """Main pipeline execution"""
    print("="*80)
    print("IMPROVED STOCK PRICE PREDICTION PIPELINE")
    print("="*80)

    print("\nLoading company tickers JSON...")
    company_lookups = load_company_tickers_json("company_tickers.json")

    load_from_cache = False

    if not FORCE_REFETCH and check_essential_data_only(DATA_FILE, list(companies.keys())):
        print(f"\n{'='*80}")
        print("COMPLETE DATA FOUND IN CACHE")
        print(f"{'='*80}")

        user_input = input("\nLoad data from cache? (yes/no) [yes]: ").strip().lower()
        if user_input in ['', 'yes', 'y']:
            load_from_cache = True

    if load_from_cache:
        (daily_sentiments, stock_dataframes, validated_mentions,
         all_related_companies, alpha_texts, success) = load_all_data_from_excel(DATA_FILE)

        if not success:
            print("Failed to load data from cache. Falling back to data collection...")
            load_from_cache = False
        else:
            print("\n✓ Loaded essential data from cache")
            print(f"  - Daily sentiment for {len(daily_sentiments)} tickers")
            print(f"  - Stock data for {len(stock_dataframes)} tickers")
            print(f"  - Related companies for {len(all_related_companies)} tickers")
            print(f"  - Alpha formulas for {len(alpha_texts)} tickers")

            # Fetch economic and alternative data
            print("\n" + "="*80)
            print("FETCHING ECONOMIC & FUNDAMENTAL DATA (FRED + SEC + EARNINGS)")
            print("="*80)

            all_dates = []
            for ticker, df in stock_dataframes.items():
                if not df.empty and 'date' in df.columns:
                    all_dates.extend(df['date'].tolist())

            if all_dates:
                full_start_date = min(all_dates)
                full_end_date = max(all_dates)
                print(f"Date range from cached data: {full_start_date} to {full_end_date}")

                print("\nFetching FRED economic indicators...")
                economic_data = fetch_fred_data(full_start_date, full_end_date)

                print("\nFetching SEC filings and earnings data...")
                all_alternative_data = {}

                for ticker, name in companies.items():
                    print(f"\n{ticker} ({name}):")
                    try:
                        sec_sentiment, earnings_sentiment = fetch_all_alternative_data(
                            ticker, full_start_date, full_end_date
                        )
                        all_alternative_data[ticker] = {
                            'sec': sec_sentiment,
                            'earnings': earnings_sentiment
                        }
                        time.sleep(3)
                    except Exception as e:
                        print(f"  ✗ Error fetching alternative data for {ticker}: {e}")
                        all_alternative_data[ticker] = {'sec': {}, 'earnings': {}}

                print("\n✓ Alternative data collection complete")
            else:
                print("\n⚠ Could not determine date range from cached data")
                economic_data = pd.DataFrame()
                all_alternative_data = {}

            # Check and generate missing alphas
            print("\n" + "="*80)
            print("CHECKING ALPHA FORMULAS")
            print("="*80)

            missing_alphas = []
            for ticker in companies.keys():
                if FORCE_REGENERATE_ALPHAS or ticker not in alpha_texts or not alpha_texts[ticker]:
                    missing_alphas.append(ticker)
                    if FORCE_REGENERATE_ALPHAS:
                        print(f"  🔄 Forcing regeneration for {ticker}")
                else:
                    print(f"  ✓ Loaded alpha formula for {ticker}")

            if missing_alphas:
                print(f"\n{'='*80}")
                print(f"GENERATING MISSING ALPHAS FOR {len(missing_alphas)} TICKERS")
                print(f"{'='*80}")

                for ticker in missing_alphas:
                    name = companies[ticker]

                    if ticker not in stock_dataframes:
                        print(f"\nSkipping {ticker}: No stock data available")
                        alpha_texts[ticker] = generate_simple_alphas(ticker)
                        continue

                    print(f"\nGenerating alphas for {name} ({ticker})...")
                    stock_df = stock_dataframes[ticker]
                    related_companies = all_related_companies.get(ticker, [])

                    comprehensive_df = prepare_dataframe_for_alpha(
                        ticker, stock_df, daily_sentiments, related_companies,
                        alternative_data=all_alternative_data,
                        economic_data=economic_data
                    )

                    if comprehensive_df is None or comprehensive_df.empty:
                        print(f"  Failed to prepare data, using simple alphas")
                        alpha_texts[ticker] = generate_simple_alphas(ticker)
                        continue

                    alpha_text = generate_alphas_with_groq(
                        groq_client, ticker, name, comprehensive_df, related_companies
                    )
                    alpha_texts[ticker] = alpha_text
                    print(f"\nGenerated alphas for {ticker}:")
                    print(alpha_text)
                    print()
                    time.sleep(2)

                save_all_data_to_excel(
                    DATA_FILE, daily_sentiments, stock_dataframes,
                    validated_mentions, all_related_companies, alpha_texts
                )
            else:
                print(f"\n✓ All alpha formulas loaded from cache - no API calls needed!")

    if not load_from_cache:
        # Data collection phase
        # Rolling window ending now (was hardcoded 2023-09-01 -> 2025-09-01)
        end_dt = datetime.now(timezone.utc).replace(microsecond=0)
        start_dt = end_dt - timedelta(days=getattr(settings, 'LOOKBACK_DAYS', 730))
        start_date = start_dt.isoformat().replace("+00:00", "Z")
        end_date = end_dt.isoformat().replace("+00:00", "Z")

        daily_sentiments = defaultdict(lambda: defaultdict(list))
        validated_mentions = defaultdict(Counter)
        stock_dataframes = {}
        alpha_texts = {}
        all_related_companies = {}

        # Phase 1: Fetch news for primary companies
        print("\n" + "="*80)
        print("PHASE 1: FETCHING NEWS FOR PRIMARY COMPANIES")
        print("="*80)

        for ticker, name in companies.items():
            print(f"\nFetching news for {name} ({ticker})...")
            news_articles = fetch_news(client, ticker, start_date, end_date,
                                      batch_size=settings.NEWS_BATCH_SIZE,
                                      sleep_time=settings.NEWS_SLEEP_TIME)

            for item in news_articles:
                text = f"{item.title} {item.summary if hasattr(item, 'summary') else ''}"
                compound_score = analyze_sentiment(text)
                article_date = item.published_utc.split('T')[0] if 'T' in item.published_utc else item.published_utc[:10]
                daily_sentiments[ticker][article_date].append(compound_score)

                doc = nlp(text)
                for ent in doc.ents:
                    if ent.label_ == "ORG":
                        if company_lookups:
                            is_valid, mention_ticker, official_name = validate_company_exists(ent.text, company_lookups)
                            if is_valid:
                                validated_mentions[ticker][f"{official_name} ({mention_ticker})"] += 1

        # Phase 2: Identify and fetch related company sentiment
        print("\n" + "="*80)
        print("PHASE 2: IDENTIFYING AND FETCHING RELATED COMPANY SENTIMENT")
        print("="*80)

        all_related_tickers = set()

        for ticker, name in companies.items():
            print(f"\nIdentifying related companies for {name} ({ticker})...")
            related_companies = []

            for company_str, count in validated_mentions[ticker].most_common(20):
                if len(related_companies) >= 5:
                    break

                match = re.search(r'\(([A-Z]+)\)', company_str)
                if not match:
                    continue

                related_ticker = match.group(1)
                if related_ticker == ticker:
                    continue

                related_name = company_str.split('(')[0].strip()

                if is_same_company(ticker, related_ticker, name, related_name, COMPANY_ALIASES):
                    continue

                is_valid, reason = validate_ticker_quality(related_ticker,
                                                          min_market_cap=settings.MIN_MARKET_CAP)
                if not is_valid:
                    print(f"     Skipped {related_ticker}: {reason}")
                    continue

                related_companies.append(related_ticker)
                all_related_tickers.add(related_ticker)
                print(f"     Added {related_ticker} ({related_name}): {count} mentions")

            all_related_companies[ticker] = related_companies
            print(f"  Found {len(related_companies)} related companies")

        # Fetch sentiment for related companies
        print(f"\n{'='*80}")
        print(f"FETCHING SENTIMENT FOR {len(all_related_tickers)} UNIQUE RELATED COMPANIES")
        print(f"{'='*80}")

        for related_ticker in all_related_tickers:
            print(f"\nFetching sentiment for {related_ticker}...")
            fetch_sentiment_for_ticker(client, sia, related_ticker, start_date, end_date, daily_sentiments)
            print(f"Waiting {settings.RELATED_COMPANIES_SLEEP_TIME} seconds before next company...")
            time.sleep(settings.RELATED_COMPANIES_SLEEP_TIME)

        # Phase 3: Fetch economic and alternative data
        print("\n" + "="*80)
        print("FETCHING ECONOMIC & FUNDAMENTAL DATA")
        print("="*80)

        full_start_date = start_date[:10]
        full_end_date = end_date[:10]

        economic_data = fetch_fred_data(full_start_date, full_end_date)
        all_alternative_data = {}

        for ticker, name in companies.items():
            print(f"\n{ticker} ({name}):")
            sec_sentiment, earnings_sentiment = fetch_all_alternative_data(
                ticker, full_start_date, full_end_date
            )
            all_alternative_data[ticker] = {
                'sec': sec_sentiment,
                'earnings': earnings_sentiment
            }
            time.sleep(3)

        print("\n✓ Alternative data collection complete")

        # Phase 3.5: Fetch stock data
        print("\n" + "="*80)
        print("PHASE 3: FETCHING STOCK DATA")
        print("="*80)

        for ticker, name in companies.items():
            print(f"\nFetching stock data for {name} ({ticker})...")
            stock_df = fetch_stock_data(ticker, start_date, end_date)
            if not stock_df.empty:
                stock_dataframes[ticker] = stock_df
            time.sleep(2)

        # Phase 3.5: Generate alphas
        print("\n" + "="*80)
        print("PHASE 3.5: GENERATING ALPHAS WITH GROQ LLM")
        print("="*80)

        for ticker, name in companies.items():
            if ticker not in stock_dataframes:
                print(f"\nSkipping {ticker}: No stock data available")
                alpha_texts[ticker] = generate_simple_alphas(ticker)
                continue

            print(f"\nGenerating alphas for {name} ({ticker})...")
            stock_df = stock_dataframes[ticker]
            related_companies = all_related_companies.get(ticker, [])

            comprehensive_df = prepare_dataframe_for_alpha(
                ticker, stock_df, daily_sentiments, related_companies,
                alternative_data=all_alternative_data,
                economic_data=economic_data
            )

            if comprehensive_df is None or comprehensive_df.empty:
                print(f"  Failed to prepare data, using simple alphas")
                alpha_texts[ticker] = generate_simple_alphas(ticker)
                continue

            alpha_text = generate_alphas_with_groq(
                groq_client, ticker, name, comprehensive_df, related_companies
            )
            alpha_texts[ticker] = alpha_text
            print(f"\nGenerated alphas for {ticker}:")
            print(alpha_text)
            print()

        time.sleep(2)

        save_all_data_to_excel(
            DATA_FILE, daily_sentiments, stock_dataframes,
            validated_mentions, all_related_companies, alpha_texts
        )

    # Phase 4: Training
    print("\n" + "="*80)
    print("PHASE 4: TRAINING ENSEMBLE OF ENHANCED LSTM MODELS")
    print("="*80)

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"Using device: {device}")

    trained_models = {}
    successful_trainings = 0

    for ticker, name in companies.items():
        if ticker not in stock_dataframes or ticker not in alpha_texts:
            print(f"\nSkipping {ticker}: Missing data or alphas")
            continue

        print(f"\n{'='*80}")
        print(f"TRAINING ENSEMBLE FOR {name} ({ticker})")
        print(f"{'='*80}")

        stock_df = stock_dataframes[ticker]
        related_companies = all_related_companies.get(ticker, [])

        comprehensive_df = prepare_dataframe_for_alpha(
            ticker, stock_df, daily_sentiments, related_companies,
            alternative_data=all_alternative_data if 'all_alternative_data' in locals() else {},
            economic_data=economic_data if 'economic_data' in locals() else None
        )

        if comprehensive_df is None or comprehensive_df.empty:
            print(f"✗ Failed to prepare data for {ticker}")
            continue

        try:
            alpha_text = alpha_texts.get(ticker, generate_simple_alphas(ticker))

            if len(comprehensive_df) < 100:
                print(f"✗ Insufficient data for {ticker}: only {len(comprehensive_df)} rows")
                continue

            print(f"✓ Data ready: {len(comprehensive_df)} rows with {len(comprehensive_df.columns)} features")

            print(f"\nAttempting ensemble training...")
            ensemble, metrics, scalers = train_ensemble(
                ticker=ticker,
                company_name=name,
                comprehensive_df=comprehensive_df,
                alpha_text=alpha_text,
                n_models=settings.ENSEMBLE_SIZE,
                window_size=settings.WINDOW_SIZE,
                device=device
            )

            if ensemble is not None and metrics is not None:
                trained_models[ticker] = {
                    'model': ensemble,
                    'metrics': metrics,
                    'scalers': scalers,
                    'type': 'ensemble'
                }

                torch.save(ensemble.state_dict(), f'{ticker}_ensemble.pth')
                print(f"\n✓ Ensemble saved as {ticker}_ensemble.pth")

                for i, model in enumerate(ensemble.models):
                    torch.save(model.state_dict(), f'{ticker}_model_{i}.pth')

                successful_trainings += 1
            else:
                print(f"✗ Ensemble training failed, trying single model...")
                model, metrics, scalers = train_stock_predictor(
                    ticker=ticker,
                    company_name=name,
                    comprehensive_df=comprehensive_df,
                    alpha_text=alpha_text,
                    num_epochs=settings.NUM_EPOCHS,
                    device=device
                )

                if model is not None and metrics is not None:
                    trained_models[ticker] = {
                        'model': model,
                        'metrics': metrics,
                        'scalers': scalers,
                        'type': 'single'
                    }
                    torch.save(model.state_dict(), f'{ticker}_model.pth')
                    print(f"✓ Single model saved as {ticker}_model.pth")
                    successful_trainings += 1
                else:
                    print(f"✗ Single model training also failed for {ticker}")

        except Exception as e:
            print(f"\n✗ Error training model for {ticker}: {e}")
            import traceback
            traceback.print_exc()
            continue

        print(f"\nWaiting 3 seconds before next ticker...")
        time.sleep(3)

    # Summary
    print(f"\n{'='*80}")
    print(f"TRAINING SUMMARY")
    print(f"{'='*80}")
    print(f"Successfully trained models: {successful_trainings}/{len(companies)}")

    if trained_models:
        print("\nUpdating Excel file with training results...")
        save_all_data_to_excel(
            DATA_FILE, daily_sentiments, stock_dataframes,
            validated_mentions, all_related_companies, alpha_texts, trained_models
        )

    print("\n" + "="*80)
    print("PIPELINE COMPLETE!")
    print("="*80)
    print(f"\nModels trained: {len(trained_models)}/{len(companies)}")

    for ticker in trained_models:
        model_data = trained_models[ticker]
        model_type = model_data.get('type', 'single')
        metrics = model_data['metrics']

        print(f"\n  {ticker} ({model_type}):")
        print(f"    - RMSE: {metrics.get('RMSE', 0):.6f}")
        print(f"    - Directional Accuracy: {metrics.get('Directional Accuracy', 0):.2f}%")
        print(f"    - Up Precision: {metrics.get('Up Precision', 0):.2f}%")
        print(f"    - Down Precision: {metrics.get('Down Precision', 0):.2f}%")
        print(f"    - Large Move Hit Rate: {metrics.get('Large Move Hit Rate', 0):.2f}%")
        print(f"    - Sharpe Ratio: {metrics.get('Sharpe Ratio', 0):.3f}")
        print(f"    - Win Rate: {metrics.get('Win Rate', 0):.2f}%")

        if 'Avg Uncertainty' in metrics:
            print(f"    - Avg Uncertainty: {metrics['Avg Uncertainty']:.6f}")

        if model_type == 'ensemble':
            print(f"    - Ensemble of {len(model_data['model'].models)} models")

    print(f"\n✓ All data cached in {DATA_FILE}")
    print(f"   Next run will load from cache automatically!")
    print("="*80)


if __name__ == "__main__":
    main()
