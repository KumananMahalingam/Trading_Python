// @vitest-environment happy-dom
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/predictionApi', () => ({
    startPredictionJob: vi.fn(),
    getPredictionJob: vi.fn(),
}));

import { getPredictionJob, startPredictionJob } from '../api/predictionApi';
import { POLL_MS, usePredictionJob } from './usePredictionJob';

const ID = 'b'.repeat(32);
const job = (over = {}) => ({
    id: ID, ticker: 'AAPL', kind: 'refresh', status: 'queued', stage: null, progress: 0,
    stageProgress: 0, queuePosition: 1, etaSeconds: 70, error: null, ...over,
});
const httpError = (status, data = {}) => Object.assign(new Error(`HTTP ${status}`), { response: { status, data } });

const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS); });

beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(startPredictionJob).mockReset();
    vi.mocked(getPredictionJob).mockReset();
});
afterEach(() => vi.useRealTimers());

describe('usePredictionJob', () => {
    it('starts a job, polls until it succeeds, then stops', async () => {
        const onFinished = vi.fn();
        vi.mocked(startPredictionJob).mockResolvedValue({ ...job(), created: true });
        vi.mocked(getPredictionJob)
            .mockResolvedValueOnce(job({ status: 'running', stage: 'news', progress: 40, etaSeconds: 40 }))
            .mockResolvedValueOnce(job({ status: 'succeeded', progress: 100, etaSeconds: null }));

        const { result } = renderHook(() => usePredictionJob({ ticker: 'aapl', activeJob: null, onFinished }));
        await act(async () => { await result.current.start('refresh'); });
        expect(startPredictionJob).toHaveBeenCalledWith('AAPL', 'refresh');
        expect(result.current.active).toBe(true);
        expect(result.current.job.status).toBe('queued');

        await tick();
        expect(result.current.job).toMatchObject({ status: 'running', progress: 40 });
        await tick();
        expect(result.current.active).toBe(false);
        expect(result.current.notice).toMatchObject({ kind: 'success' });
        expect(onFinished).toHaveBeenCalledTimes(1);

        await tick(); await tick();
        expect(getPredictionJob).toHaveBeenCalledTimes(2);       // polling stopped
    });

    it('resumes the active job reported by the model after a reload', async () => {
        const onFinished = vi.fn();
        vi.mocked(getPredictionJob).mockResolvedValueOnce(job({ status: 'succeeded', progress: 100 }));
        const active = job({ status: 'running', stage: 'train', progress: 60, kind: 'train' });

        const { result, rerender } = renderHook(
            ({ activeJob }) => usePredictionJob({ ticker: 'AAPL', activeJob, onFinished }),
            { initialProps: { activeJob: active } },
        );
        expect(result.current.active).toBe(true);
        expect(result.current.job.progress).toBe(60);

        await tick();
        expect(getPredictionJob).toHaveBeenCalledWith(ID);
        expect(onFinished).toHaveBeenCalledTimes(1);
        expect(result.current.active).toBe(false);

        // the parent may still pass the old activeJob until its refetch lands: no new polling
        rerender({ activeJob: active });
        await tick();
        expect(getPredictionJob).toHaveBeenCalledTimes(1);
        expect(result.current.active).toBe(false);
    });

    it('ignores an active job for a different ticker', () => {
        const { result } = renderHook(() => usePredictionJob({ ticker: 'MSFT', activeJob: job(), onFinished: vi.fn() }));
        expect(result.current.job).toBeNull();
        expect(result.current.active).toBe(false);
    });

    it('409 for another ticker shows a message instead of following', async () => {
        vi.mocked(startPredictionJob).mockRejectedValue(httpError(409, {
            message: 'You already have a train job for MSFT in progress',
            job: job({ ticker: 'MSFT', kind: 'train', status: 'running', etaSeconds: 300 }),
        }));
        const { result } = renderHook(() => usePredictionJob({ ticker: 'AAPL', activeJob: null, onFinished: vi.fn() }));
        await act(async () => { await result.current.start('train'); });
        expect(result.current.active).toBe(false);
        expect(result.current.notice.text).toContain('MSFT');
        await tick();
        expect(getPredictionJob).not.toHaveBeenCalled();
    });

    it('409 for the same ticker follows the existing job', async () => {
        vi.mocked(startPredictionJob).mockRejectedValue(httpError(409, {
            message: 'You already have a train job for AAPL in progress',
            job: job({ kind: 'train', status: 'running', progress: 10 }),
        }));
        vi.mocked(getPredictionJob).mockResolvedValue(job({ kind: 'train', status: 'running', progress: 20 }));
        const { result } = renderHook(() => usePredictionJob({ ticker: 'AAPL', activeJob: null, onFinished: vi.fn() }));
        await act(async () => { await result.current.start('refresh'); });
        expect(result.current.job.kind).toBe('train');
        await tick();
        expect(result.current.job.progress).toBe(20);
    });

    it('503 on start reports the outage', async () => {
        vi.mocked(startPredictionJob).mockRejectedValue(httpError(503, { message: 'Prediction service unavailable' }));
        const { result } = renderHook(() => usePredictionJob({ ticker: 'AAPL', activeJob: null, onFinished: vi.fn() }));
        await act(async () => { await result.current.start('refresh'); });
        expect(result.current.notice).toMatchObject({ kind: 'error', text: 'Prediction service unavailable. Try again later.' });
        expect(result.current.starting).toBe(false);
    });

    it('keeps polling through transient errors and stops on 404', async () => {
        const onFinished = vi.fn();
        vi.mocked(getPredictionJob)
            .mockRejectedValueOnce(new Error('network down'))
            .mockResolvedValueOnce(job({ status: 'running', progress: 50 }))
            .mockRejectedValueOnce(httpError(404));
        const { result } = renderHook(() => usePredictionJob({
            ticker: 'AAPL', activeJob: job({ status: 'running' }), onFinished,
        }));
        await tick();
        expect(result.current.pollWarning).toMatch(/Connection problem/);
        await tick();
        expect(result.current.pollWarning).toBeNull();
        expect(result.current.job.progress).toBe(50);
        await tick();
        expect(result.current.active).toBe(false);
        expect(result.current.notice.text).toBe('This run is no longer available.');
        await tick();
        expect(getPredictionJob).toHaveBeenCalledTimes(3);
        expect(onFinished).not.toHaveBeenCalled();
    });

    it('stops polling on unmount', async () => {
        vi.mocked(getPredictionJob).mockResolvedValue(job({ status: 'running' }));
        const { unmount } = renderHook(() => usePredictionJob({
            ticker: 'AAPL', activeJob: job({ status: 'running' }), onFinished: vi.fn(),
        }));
        await tick();
        unmount();
        await tick(); await tick();
        expect(getPredictionJob).toHaveBeenCalledTimes(1);
    });
});
