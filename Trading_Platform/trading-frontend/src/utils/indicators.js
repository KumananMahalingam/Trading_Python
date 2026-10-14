// Calculation functions adapted from:
// lightweight-charts/indicator-examples/src/indicators/moving-average/moving-average-calculation.ts

export function calcSMA(closes, period) {
    const result = [];
    let sum = 0;
    const window = [];
    for (let i = 0; i < closes.length; i++) {
        const v = closes[i];
        window.push(v);
        sum += v;
        if (window.length > period) sum -= window.shift();
        result.push(window.length === period ? sum / period : undefined);
    }
    return result;
}

export function calcEMA(closes, period) {
    const result = new Array(closes.length).fill(undefined);
    const k = 2 / (period + 1);
    let ema;
    for (let i = 0; i < closes.length; i++) {
        const v = closes[i];
        ema = ema === undefined ? v : v * k + ema * (1 - k);
        if (i >= period - 1) result[i] = ema;
    }
    return result;
}

// Bollinger Bands: returns [{upper, mid, lower}] aligned with input
export function calcBollingerBands(closes, period = 20, stdDevMult = 2) {
    const mids = calcSMA(closes, period);
    return mids.map((mid, i) => {
        if (mid === undefined || i < period - 1) return undefined;
        const slice = closes.slice(i - period + 1, i + 1);
        const mean = mid;
        const variance = slice.reduce((sum, v) => sum + (v - mean) ** 2, 0) / period;
        const std = Math.sqrt(variance);
        return { upper: mid + stdDevMult * std, mid, lower: mid - stdDevMult * std };
    });
}

// Formats indicator line data for lightweight-charts LineSeries
export function toLineData(times, values) {
    return times
        .map((time, i) => values[i] !== undefined ? { time, value: values[i] } : null)
        .filter(Boolean);
}
