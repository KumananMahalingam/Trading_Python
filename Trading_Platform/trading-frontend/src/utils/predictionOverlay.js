// Pure helpers that turn a GET /api/predictions/{ticker} response into chart
// data for lightweight-charts. No chart objects here, so it is unit-testable.
//
// Times are Unix seconds. Daily candles from the backend are stamped at UTC
// midnight, and prediction dates are plain YYYY-MM-DD days, so a date maps to
// the candle whose UTC day matches; dates with no candle (the forecast target,
// which is in the future) fall back to UTC midnight of that day.

const DAY = 86400;

export function dateToTime(isoDate) {
    const ms = Date.parse(`${isoDate}T00:00:00Z`);
    if (Number.isNaN(ms)) throw new Error(`Invalid date: ${isoDate}`);
    return Math.floor(ms / 1000);
}

export function timeToDate(time) {
    return new Date(time * 1000).toISOString().slice(0, 10);
}

function candleTimesByDay(candles) {
    const byDay = new Map();
    for (const c of candles) byDay.set(timeToDate(c.time), c.time);
    return byDay;
}

// Strictly ascending, one entry per time (lightweight-charts rejects duplicates).
function ascendingUnique(points) {
    const sorted = [...points].sort((a, b) => a.time - b.time);
    return sorted.filter((p, i) => i === 0 || p.time !== sorted[i - 1].time);
}

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

/**
 * Build overlay data.
 *
 * @param {object} model   API response ({ status, prediction, backtest, ... })
 * @param {Array}  candles chart candles [{ time, open, high, low, close }], ascending
 * @returns {null | {
 *   backtestLine: {time, value}[],                 predicted close per test day
 *   backtestBand: {time, upper, mid, lower}[],     +/- band around it
 *   forecastLine: {time, value}[],                 [as-of close, predicted next close]
 *   forecastBand: {time, upper, mid, lower}[],     cone from the as-of close to the target band
 *   prediction, metrics, backtestRange, stale
 * }}
 */
export function buildPredictionOverlay(model, candles) {
    if (!model || model.status !== 'ready' || !Array.isArray(candles) || candles.length === 0) return null;

    const byDay = candleTimesByDay(candles);
    const first = candles[0].time;
    const last = candles[candles.length - 1].time;
    const toTime = (day) => byDay.get(day) ?? dateToTime(day);

    // Backtest: only days that are on the visible candle range, so the overlay
    // never stretches the time scale beyond the loaded history.
    const points = (model.backtest?.points ?? [])
        .filter(p => p && p.date && isNum(p.predictedClose) && isNum(p.lower) && isNum(p.upper))
        .map(p => ({ ...p, time: toTime(p.date) }))
        .filter(p => p.time >= first && p.time <= last);
    const backtestPoints = ascendingUnique(points);

    const backtestLine = backtestPoints.map(p => ({ time: p.time, value: p.predictedClose }));
    const backtestBand = backtestPoints.map(p => ({
        time: p.time, upper: p.upper, mid: p.predictedClose, lower: p.lower,
    }));

    // Forecast: from the close the model saw to its predicted next close.
    let forecastLine = [];
    let forecastBand = [];
    let stale = false;
    const pred = model.prediction;
    if (pred && pred.asOfDate && pred.targetDate && isNum(pred.lastClose) && isNum(pred.predictedClose)) {
        const from = toTime(pred.asOfDate);
        const to = toTime(pred.targetDate);
        if (to > from) {
            forecastLine = [
                { time: from, value: pred.lastClose },
                { time: to, value: pred.predictedClose },
            ];
            forecastBand = [
                { time: from, upper: pred.lastClose, mid: pred.lastClose, lower: pred.lastClose },
                { time: to, upper: pred.upper, mid: pred.predictedClose, lower: pred.lower },
            ];
        }
        // The target day already has a finished bar on the chart: the
        // forecast is old and a refresh would produce a newer one. (Today's
        // still-forming crypto bar has the target date and is not "finished".)
        stale = to < last || (to === last && to + DAY <= Math.floor(Date.now() / 1000));
    }

    return {
        backtestLine,
        backtestBand,
        forecastLine,
        forecastBand,
        prediction: pred ?? null,
        metrics: model.backtest?.metrics ?? null,
        backtestRange: backtestPoints.length
            ? { start: timeToDate(backtestPoints[0].time), end: timeToDate(backtestPoints[backtestPoints.length - 1].time) }
            : null,
        stale,
    };
}

/** Short legend text, e.g. "▼ DOWN −0.03% for Oct 15". */
export function describePrediction(prediction) {
    if (!prediction) return '';
    const pct = prediction.predictedReturn * 100;
    const arrow = prediction.direction === 'UP' ? '▲' : prediction.direction === 'DOWN' ? '▼' : '■';
    const sign = pct > 0 ? '+' : pct < 0 ? '−' : '';
    const day = new Date(`${prediction.targetDate}T00:00:00Z`)
        .toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
    return `${arrow} ${prediction.direction} ${sign}${Math.abs(pct).toFixed(2)}% for ${day}`;
}

/** "36.2%" or "–" for missing metrics. */
export function formatPct(value) {
    return isNum(value) ? `${value.toFixed(1)}%` : '–';
}
