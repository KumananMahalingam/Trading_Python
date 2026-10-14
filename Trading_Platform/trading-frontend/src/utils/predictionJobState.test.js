import { describe, expect, it } from 'vitest';
import {
    formatDuration, initialJobState, isActiveJob, predictionJobReducer as reduce, stageLabel,
} from './predictionJobState';

const job = (over = {}) => ({
    id: 'a'.repeat(32), ticker: 'AAPL', kind: 'refresh', status: 'queued', stage: null,
    progress: 0, stageProgress: 0, queuePosition: 1, etaSeconds: 70, error: null, ...over,
});

describe('formatDuration', () => {
    it.each([
        [null, ''], [undefined, ''], [-5, ''], [NaN, ''],
        [0, 'under a minute'], [59, 'under a minute'], [67, '~1 min'], [1267, '~21 min'],
        [3600, '~1 h'], [5700, '~1 h 35 min'],
    ])('%s -> %s', (s, out) => expect(formatDuration(s)).toBe(out));
});

describe('stageLabel / isActiveJob', () => {
    it('labels stages and statuses', () => {
        expect(stageLabel(job())).toBe('Waiting in queue');
        expect(stageLabel(job({ status: 'running', stage: 'train' }))).toBe('Training models');
        expect(stageLabel(job({ status: 'running', stage: null }))).toBe('Starting');
        expect(stageLabel(job({ status: 'succeeded' }))).toBe('Done');
        expect(stageLabel(null)).toBe('');
    });
    it('detects active jobs', () => {
        expect(isActiveJob(job())).toBe(true);
        expect(isActiveJob(job({ status: 'running' }))).toBe(true);
        expect(isActiveJob(job({ status: 'failed' }))).toBe(false);
        expect(isActiveJob(null)).toBe(false);
    });
});

describe('predictionJobReducer', () => {
    it('queued -> running -> succeeded', () => {
        let s = reduce(initialJobState, { type: 'start' });
        expect(s.starting).toBe(true);
        s = reduce(s, { type: 'started', job: job(), created: true });
        expect(s).toMatchObject({ starting: false, ticker: 'AAPL', notice: null });
        s = reduce(s, { type: 'polled', job: job({ status: 'running', stage: 'news', progress: 30 }) });
        expect(s.job.progress).toBe(30);
        s = reduce(s, { type: 'polled', job: job({ status: 'succeeded', progress: 100 }) });
        expect(s.notice).toEqual({ ticker: 'AAPL', kind: 'success', text: 'Prediction refreshed.' });
    });

    it('running -> failed shows the job error', () => {
        let s = reduce(initialJobState, { type: 'started', job: job({ kind: 'train' }), created: true });
        s = reduce(s, { type: 'polled', job: job({ kind: 'train', status: 'failed', error: 'No price data available for AAPL' }) });
        expect(s.notice).toMatchObject({ kind: 'error', text: 'No price data available for AAPL' });
    });

    it('joining an existing job tells the user', () => {
        const s = reduce(initialJobState, { type: 'started', job: job(), created: false });
        expect(s.notice.kind).toBe('info');
    });

    it('conflict on the same ticker follows the existing job', () => {
        const existing = job({ kind: 'train', status: 'running' });
        const s = reduce(initialJobState, { type: 'conflict', ticker: 'AAPL', job: existing, message: 'x' });
        expect(s.job).toBe(existing);
        expect(s.notice).toBeNull();
    });

    it('conflict on another ticker explains and keeps the current job', () => {
        const other = job({ ticker: 'MSFT', kind: 'train', status: 'running', etaSeconds: 600 });
        const s = reduce(initialJobState, {
            type: 'conflict', ticker: 'AAPL', job: other, message: 'You already have a train job for MSFT in progress',
        });
        expect(s.job).toBeNull();
        expect(s.notice.ticker).toBe('AAPL');
        expect(s.notice.text).toContain('MSFT');
        expect(s.notice.text).toContain('~10 min left');
    });

    it('start failure and dismiss', () => {
        let s = reduce(initialJobState, { type: 'startFailed', ticker: 'AAPL', message: 'Prediction service unavailable.' });
        expect(s.notice.text).toBe('Prediction service unavailable.');
        s = reduce(s, { type: 'dismiss' });
        expect(s.notice).toBeNull();
    });

    it('transient poll errors warn, fatal ones fail the job', () => {
        let s = reduce(initialJobState, { type: 'started', job: job({ status: 'running' }), created: true });
        s = reduce(s, { type: 'pollFailed', fatal: false, message: 'Connection problem' });
        expect(s.pollWarning).toBe('Connection problem');
        expect(isActiveJob(s.job)).toBe(true);
        s = reduce(s, { type: 'polled', job: job({ status: 'running', progress: 50 }) });
        expect(s.pollWarning).toBeNull();
        s = reduce(s, { type: 'pollFailed', fatal: true, jobId: s.job.id, message: 'gone' });
        expect(s.job.status).toBe('failed');
        expect(s.notice.text).toBe('gone');
    });
});
