// Small UI preferences kept in localStorage, so a reload returns to the same
// chart (and a running model job stays visible).

export const LAST_TICKER_KEY = 'lastChartTicker';
export const SHOW_PREDICTION_KEY = 'showModelPrediction';
export const DEFAULT_TICKER = 'BTCUSDT';

export function readPreference(key, fallback) {
    try {
        const value = localStorage.getItem(key);
        return value ?? fallback;
    } catch {
        return fallback;
    }
}

export function writePreference(key, value) {
    try {
        localStorage.setItem(key, value);
    } catch {
        /* storage unavailable (private mode, quota) */
    }
}

/** Remember `ticker` as the chart to open next. */
export function rememberTicker(ticker) {
    writePreference(LAST_TICKER_KEY, String(ticker).trim().toUpperCase());
}
