import { describe, expect, it, vi, afterEach } from 'vitest';
import {
    buildPredictionOverlay, dateToTime, describePrediction, formatPct, timeToDate,
} from './predictionOverlay';

const day = (iso) => dateToTime(iso);

// Weekday candles Mon 2026-10-05 .. Wed 2026-10-14 (stock-like, no weekends)
const STOCK_DAYS = ['2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09',
    '2026-10-12', '2026-10-13', '2026-10-14'];
const candlesFor = (days) => days.map((d, i) => ({ time: day(d), open: 100 + i, high: 101 + i, low: 99 + i, close: 100 + i }));

const point = (date, predictedClose, extra = {}) => ({
    date, baseClose: predictedClose, actualClose: predictedClose + 1, predictedReturn: 0.001,
    sigma: 0.01, predictedClose, lower: predictedClose - 2, upper: predictedClose + 2, ...extra,
});

const model = (overrides = {}) => ({
    ticker: 'AAPL',
    status: 'ready',
    prediction: {
        asOfDate: '2026-10-14', targetDate: '2026-10-15', lastClose: 107, predictedReturn: -0.0003,
        sigma: 0.002, predictedClose: 106.97, lower: 106.5, upper: 107.4, bandSigmas: 2, direction: 'DOWN',
    },
    backtest: {
        start: '2026-09-28', end: '2026-10-14', bandSigmas: 2,
        points: [point('2026-09-28', 90), point('2026-10-06', 101), point('2026-10-07', 102), point('2026-10-14', 107)],
        metrics: { n: 4, directionalAccuracy: 50, upPrecision: 40, downPrecision: 60, majorityBaseline: 55 },
    },
    ...overrides,
});

afterEach(() => vi.useRealTimers());

describe('date conversion', () => {
    it('round-trips UTC days', () => {
        expect(dateToTime('2026-10-14')).toBe(Date.UTC(2026, 9, 14) / 1000);
        expect(timeToDate(dateToTime('2026-10-14'))).toBe('2026-10-14');
    });
    it('rejects bad dates', () => {
        expect(() => dateToTime('nope')).toThrow();
    });
});

describe('buildPredictionOverlay', () => {
    it('returns null when there is nothing to draw', () => {
        expect(buildPredictionOverlay(null, candlesFor(STOCK_DAYS))).toBeNull();
        expect(buildPredictionOverlay({ status: 'none', prediction: null, backtest: null }, candlesFor(STOCK_DAYS))).toBeNull();
        expect(buildPredictionOverlay(model(), [])).toBeNull();
    });

    it('clips backtest points to the loaded candle range', () => {
        const o = buildPredictionOverlay(model(), candlesFor(STOCK_DAYS));
        expect(o.backtestLine.map(p => timeToDate(p.time))).toEqual(['2026-10-06', '2026-10-07', '2026-10-14']);
        expect(o.backtestLine.map(p => p.value)).toEqual([101, 102, 107]);
        expect(o.backtestRange).toEqual({ start: '2026-10-06', end: '2026-10-14' });
    });

    it('keeps band ordering lower <= mid <= upper', () => {
        const o = buildPredictionOverlay(model(), candlesFor(STOCK_DAYS));
        for (const b of [...o.backtestBand, ...o.forecastBand]) {
            expect(b.lower).toBeLessThanOrEqual(b.mid);
            expect(b.mid).toBeLessThanOrEqual(b.upper);
        }
        expect(o.backtestBand.map(b => b.time)).toEqual(o.backtestLine.map(p => p.time));
    });

    it('draws the forecast from the as-of close to the next trading day', () => {
        const o = buildPredictionOverlay(model(), candlesFor(STOCK_DAYS));
        expect(o.forecastLine).toEqual([
            { time: day('2026-10-14'), value: 107 },
            { time: day('2026-10-15'), value: 106.97 },
        ]);
        expect(o.forecastBand[1]).toEqual({ time: day('2026-10-15'), upper: 107.4, mid: 106.97, lower: 106.5 });
        expect(o.forecastBand[0].upper).toBe(o.forecastBand[0].lower);   // cone starts at a point
        expect(o.stale).toBe(false);
    });

    it('uses the candle time when a candle exists for the day', () => {
        // a feed stamping daily bars at 05:00 UTC must still line up
        const shifted = candlesFor(STOCK_DAYS).map(c => ({ ...c, time: c.time + 5 * 3600 }));
        const o = buildPredictionOverlay(model(), shifted);
        expect(o.backtestLine[0].time).toBe(day('2026-10-06') + 5 * 3600);
        expect(o.forecastLine[0].time).toBe(day('2026-10-14') + 5 * 3600);
        expect(o.forecastLine[1].time).toBe(day('2026-10-15'));    // future day: no candle
    });

    it('drops invalid and duplicate points and sorts', () => {
        const m = model();
        m.backtest.points = [point('2026-10-07', 102), point('2026-10-06', 101), point('2026-10-06', 999),
            { date: '2026-10-08', predictedClose: null, lower: 1, upper: 2 }, null];
        const o = buildPredictionOverlay(m, candlesFor(STOCK_DAYS));
        expect(o.backtestLine.map(p => p.value)).toEqual([101, 102]);
    });

    it('marks a forecast stale once its target day has a finished bar', () => {
        const old = model({ prediction: { ...model().prediction, asOfDate: '2026-10-12', targetDate: '2026-10-13' } });
        expect(buildPredictionOverlay(old, candlesFor(STOCK_DAYS)).stale).toBe(true);
    });

    it('crypto: target equals today\'s forming bar and is not stale', () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date('2026-10-14T15:00:00Z'));
        const days = ['2026-10-10', '2026-10-11', '2026-10-12', '2026-10-13', '2026-10-14'];
        const m = model({
            prediction: { ...model().prediction, asOfDate: '2026-10-13', targetDate: '2026-10-14', direction: 'UP' },
            backtest: { ...model().backtest, points: [point('2026-10-11', 50), point('2026-10-12', 51)] },
        });
        const o = buildPredictionOverlay(m, candlesFor(days));
        expect(o.forecastLine.map(p => timeToDate(p.time))).toEqual(['2026-10-13', '2026-10-14']);
        expect(o.stale).toBe(false);
        vi.setSystemTime(new Date('2026-10-15T01:00:00Z'));          // that bar has now closed
        expect(buildPredictionOverlay(m, candlesFor(days)).stale).toBe(true);
    });

    it('works with a model that has a backtest but no prediction', () => {
        const o = buildPredictionOverlay(model({ prediction: null }), candlesFor(STOCK_DAYS));
        expect(o.forecastLine).toEqual([]);
        expect(o.backtestLine.length).toBe(3);
        expect(o.stale).toBe(false);
    });
});

describe('labels', () => {
    it('describes the prediction', () => {
        expect(describePrediction(model().prediction)).toBe('▼ DOWN −0.03% for Oct 15');
        expect(describePrediction({ ...model().prediction, direction: 'UP', predictedReturn: 0.0123 })).toBe('▲ UP +1.23% for Oct 15');
        expect(describePrediction(null)).toBe('');
    });
    it('formats percentages', () => {
        expect(formatPct(36.234)).toBe('36.2%');
        expect(formatPct(null)).toBe('–');
    });
});
