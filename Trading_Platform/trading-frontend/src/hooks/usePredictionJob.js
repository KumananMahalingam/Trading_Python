import { useCallback, useEffect, useReducer, useRef } from 'react';
import { getPredictionJob, startPredictionJob } from '../api/predictionApi';
import { initialJobState, isActiveJob, predictionJobReducer } from '../utils/predictionJobState';

export const POLL_MS = 3000;

function errorMessage(err, fallback) {
    const status = err?.response?.status;
    if (status === 503) return 'Prediction service unavailable. Try again later.';
    return err?.response?.data?.message || fallback;
}

/**
 * Start and follow train/refresh jobs for `ticker`.
 *
 * `activeJob` is the job reported by GET /api/predictions/{ticker}; it lets a
 * reloaded page pick up a run that is still going. `onFinished(job)` fires
 * once when a followed job succeeds or fails (e.g. to refetch the model).
 */
export function usePredictionJob({ ticker, activeJob, onFinished, pollMs = POLL_MS }) {
    const [state, dispatch] = useReducer(predictionJobReducer, initialJobState);
    const onFinishedRef = useRef(onFinished);
    useEffect(() => { onFinishedRef.current = onFinished; });

    const upper = (ticker || '').toUpperCase();
    const known = state.job && state.job.ticker === upper ? state.job : null;
    // Resume the server's active job unless it is the one we already know about.
    const resumable = isActiveJob(activeJob) && activeJob.ticker === upper && activeJob.id !== state.job?.id
        ? activeJob : null;
    const job = resumable && !isActiveJob(known) ? resumable : known;
    const pollingId = isActiveJob(job) ? job.id : null;

    useEffect(() => {
        if (!pollingId) return;
        let cancelled = false;
        let timer = null;
        const poll = async () => {
            try {
                const latest = await getPredictionJob(pollingId);
                if (cancelled) return;
                dispatch({ type: 'polled', job: latest });
                if (!isActiveJob(latest)) {
                    onFinishedRef.current?.(latest);
                    return;
                }
            } catch (err) {
                if (cancelled) return;
                if (err?.response?.status === 404) {
                    dispatch({ type: 'pollFailed', fatal: true, jobId: pollingId, message: 'This run is no longer available.' });
                    return;
                }
                dispatch({ type: 'pollFailed', fatal: false, message: 'Connection problem; still checking…' });
            }
            timer = setTimeout(poll, pollMs);
        };
        // A resumed job is already known to be active, so the first check can wait.
        timer = setTimeout(poll, pollMs);
        return () => { cancelled = true; clearTimeout(timer); };
    }, [pollingId, pollMs]);

    const start = useCallback(async (kind) => {
        dispatch({ type: 'start' });
        try {
            const started = await startPredictionJob(upper, kind);
            const { created, ...startedJob } = started;
            dispatch({ type: 'started', job: startedJob, created });
        } catch (err) {
            if (err?.response?.status === 409) {
                dispatch({ type: 'conflict', ticker: upper, job: err.response.data?.job, message: err.response.data?.message });
            } else {
                dispatch({ type: 'startFailed', ticker: upper, message: errorMessage(err, 'Could not start the run.') });
            }
        }
    }, [upper]);

    const dismiss = useCallback(() => dispatch({ type: 'dismiss' }), []);

    return {
        job,
        active: isActiveJob(job),
        starting: state.starting,
        notice: state.notice?.ticker === upper ? state.notice : null,
        pollWarning: pollingId ? state.pollWarning : null,
        start,
        dismiss,
    };
}
