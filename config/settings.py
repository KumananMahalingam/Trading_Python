"""
FAST TRAINING MODE - For quick testing
Copy these values to settings.py if you want faster training
"""

# Company configurations (same)
COMPANIES = {
    "AAPL": "Apple",
}

COMPANY_ALIASES = {
    "APPLE": ["AAPL"],
}

# File paths
DATA_FILE = "stock_analysis_complete.xlsx"
COMPANY_TICKERS_JSON = "company_tickers.json"

# Data collection settings
FORCE_REFETCH = False
FORCE_REGENERATE_ALPHAS = True

# Date range: rolling window ending at the run date (scripts/run_pipeline.py).
LOOKBACK_DAYS = 730
# Unused legacy values, kept for reference
START_DATE = "2024-06-01"
END_DATE = "2025-09-01"

# API settings
NEWS_BATCH_SIZE = 1000
NEWS_SLEEP_TIME = 12
RELATED_COMPANIES_SLEEP_TIME = 15

# Validation thresholds
MIN_MARKET_CAP = 5e9
REQUIRED_EXCHANGES = ['NYSE', 'NASDAQ', 'NYQ', 'NMS']
MIN_TRADING_VOLUME = 100000

# Model settings - REDUCED FOR SPEED
WINDOW_SIZE = 30
NUM_EPOCHS = 100      # Reduced from 200
LEARNING_RATE = 2e-4
BATCH_SIZE_MIN = 16
BATCH_SIZE_MAX = 64

# Model architecture - SMALLER BUT STILL IMPROVED
HIDDEN_SIZE = 192     # Reduced from 256
NUM_LAYERS = 3        # Reduced from 4
DROPOUT = 0.35
NUM_HEADS = 6         # Reduced from 8

# Feature selection
USE_FEATURE_SELECTION = True
TOP_K_FEATURES = 30   # Reduced from 40
MIN_CORRELATION = 0.01

# Ensemble settings - SMALLER
ENSEMBLE_SIZE = 3     # Reduced from 5
ENSEMBLE_MC_SAMPLES = 10  # Reduced from 15

# Training settings - FASTER STOPPING
PATIENCE = 25         # Reduced from 50
GRADIENT_CLIP_NORM = 1.0

# Loss function weights (same)
LOSS_ALPHA = 0.7
LOSS_BETA = 0.3
LOSS_GAMMA = 0.1
