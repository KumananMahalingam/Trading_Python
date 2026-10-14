// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/predictionApi', () => ({
    startPredictionJob: vi.fn(),
    getPredictionJob: vi.fn(),
}));

import { getPredictionJob, startPredictionJob } from '../api/predictionApi';
import PredictionPanel from './PredictionPanel';

const estimates = { trainSeconds: 1267, refreshSeconds: 67, queueSeconds: 0 };
const readyModel = (over = {}) => ({
    ticker: 'AAPL', assetType: 'STOCK', hasNews: true, status: 'ready',
    trainedAt: '2026-10-14T16:41:52+00:00', dataStart: '2024-11-08', dataEnd: '2026-10-13',
    peers: ['MSFT'], alphaSource: 'groq', activeJob: null, estimates,
    prediction: {
        asOfDate: '2026-10-14', targetDate: '2026-10-15', lastClose: 341.27, predictedReturn: -0.0003,
        sigma: 0.002, predictedClose: 341.17, lower: 339.8, upper: 342.5, bandSigmas: 2,
        direction: 'DOWN', generatedAt: '2026-10-14T20:21:09+00:00',
    },
    backtest: {
        start: '2026-07-08', end: '2026-10-13', points: [],
        metrics: { n: 69, directionalAccuracy: 36.23, majorityBaseline: 56.52, upPrecision: 10.26, downPrecision: 70 },
    },
    ...over,
});
const runningJob = (over = {}) => ({
    id: 'c'.repeat(32), ticker: 'AAPL', kind: 'train', status: 'running', stage: 'train',
    progress: 62.4, stageProgress: 0.4, queuePosition: null, etaSeconds: 600, error: null, ...over,
});

beforeEach(() => {
    vi.mocked(startPredictionJob).mockReset();
    vi.mocked(getPredictionJob).mockReset();
});
afterEach(() => cleanup());

describe('PredictionPanel', () => {
    it('shows the prediction, metrics and both buttons with ETAs', () => {
        render(<PredictionPanel ticker="AAPL" model={readyModel()} onFinished={vi.fn()} />);
        expect(screen.getByRole('heading', { name: /Model prediction · AAPL/ })).toBeTruthy();
        expect(screen.getByText(/341\.17/)).toBeTruthy();
        expect(screen.getByText('36.2%')).toBeTruthy();
        expect(screen.getByText('56.5%')).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Refresh prediction (~1 min)' }).disabled).toBe(false);
        expect(screen.getByRole('button', { name: 'Retrain (~21 min)' }).disabled).toBe(false);
        expect(screen.getByText(/not trading advice/)).toBeTruthy();
    });

    it('untrained ticker: refresh disabled, train offered', () => {
        render(<PredictionPanel ticker="GOOGL"
            model={{ ticker: 'GOOGL', status: 'none', hasNews: true, estimates, activeJob: null }} onFinished={vi.fn()} />);
        expect(screen.getByText(/No model has been trained for GOOGL yet\. Training takes ~21 min/)).toBeTruthy();
        expect(screen.getByRole('button', { name: /Refresh prediction/ }).disabled).toBe(true);
        expect(screen.getByRole('button', { name: 'Train model (~21 min)' })).toBeTruthy();
    });

    it('includes the queue wait in button estimates and explains it', () => {
        render(<PredictionPanel ticker="AAPL" model={readyModel({ estimates: { ...estimates, queueSeconds: 1200 } })} onFinished={vi.fn()} />);
        expect(screen.getByRole('button', { name: 'Refresh prediction (~21 min)' })).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: 'Retrain (~41 min)' }));
        expect(screen.getByRole('group', { name: 'Confirm retrain' }).textContent)
            .toMatch(/takes ~21 min, after ~20 min of runs already queued/);
    });

    it('untrained ticker with a queue', () => {
        render(<PredictionPanel ticker="GOOGL"
            model={{ ticker: 'GOOGL', status: 'none', hasNews: true, estimates: { ...estimates, queueSeconds: 600 }, activeJob: null }}
            onFinished={vi.fn()} />);
        expect(screen.getByText(/Training takes ~21 min after ~10 min of queued runs\./)).toBeTruthy();
    });

    it('while this ticker trains, estimates are not double counted', () => {
        const job = runningJob({ status: 'queued', progress: 0, queuePosition: 1, etaSeconds: 1300 });
        render(<PredictionPanel ticker="GOOGL"
            model={{ ticker: 'GOOGL', status: 'none', hasNews: true, estimates: { ...estimates, queueSeconds: 1300 },
                activeJob: { ...job, ticker: 'GOOGL' } }}
            onFinished={vi.fn()} />);
        expect(screen.getByText('No model has been trained for GOOGL yet.')).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Train model' }).disabled).toBe(true);
        expect(screen.getByRole('button', { name: 'Refresh prediction' }).disabled).toBe(true);
        expect(screen.getByRole('progressbar', { name: 'Training progress for GOOGL' })).toBeTruthy();
        expect(screen.getByText(/~22 min left/)).toBeTruthy();
    });

    it('retrain asks for confirmation before starting', async () => {
        vi.mocked(startPredictionJob).mockResolvedValue({ ...runningJob({ status: 'queued', progress: 0, queuePosition: 2, etaSeconds: 1400 }), created: true });
        render(<PredictionPanel ticker="AAPL" model={readyModel()} onFinished={vi.fn()} />);

        fireEvent.click(screen.getByRole('button', { name: /Retrain/ }));
        expect(startPredictionJob).not.toHaveBeenCalled();
        expect(screen.getByRole('group', { name: 'Confirm retrain' }).textContent).toMatch(/~21 min/);
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(screen.queryByRole('group', { name: 'Confirm retrain' })).toBeNull();

        fireEvent.click(screen.getByRole('button', { name: /Retrain/ }));
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Start retrain' })); });
        expect(startPredictionJob).toHaveBeenCalledWith('AAPL', 'train');
        expect(screen.getByText(/Waiting in queue \(#2 in line\)/)).toBeTruthy();
        expect(screen.getByRole('button', { name: /Refresh prediction/ }).disabled).toBe(true);
    });

    it('renders an accessible progress bar for a resumed job', () => {
        render(<PredictionPanel ticker="AAPL" model={readyModel({ activeJob: runningJob() })} onFinished={vi.fn()} />);
        const bar = screen.getByRole('progressbar', { name: 'Retraining progress for AAPL' });
        expect(bar.getAttribute('aria-valuenow')).toBe('62');
        expect(bar.getAttribute('aria-valuemin')).toBe('0');
        expect(bar.getAttribute('aria-valuemax')).toBe('100');
        expect(bar.getAttribute('aria-valuetext')).toBe('62%, Training models');
        expect(screen.getByText(/~10 min left/)).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Retrain' }).disabled).toBe(true);
    });

    it('shows load errors and the no-news note', () => {
        const { rerender } = render(<PredictionPanel ticker="AAPL" model={null} loadError="Prediction service unavailable." onFinished={vi.fn()} />);
        expect(screen.getByText('Prediction service unavailable.')).toBeTruthy();
        expect(screen.queryByRole('button', { name: /Refresh/ })).toBeNull();
        rerender(<PredictionPanel ticker="SOLUSDT" model={{ ticker: 'SOLUSDT', status: 'none', hasNews: false, estimates, activeJob: null }} onFinished={vi.fn()} />);
        expect(screen.getByText(/price and technical features only/)).toBeTruthy();
    });
});
