// State and formatting for prediction jobs (train / refresh runs). Pure, so
// the transitions can be unit-tested without React.

export const ACTIVE_STATUSES = ['queued', 'running'];
export const isActiveJob = (job) => !!job && ACTIVE_STATUSES.includes(job.status);

const STAGE_LABELS = {
    news: 'Fetching news',
    peers: 'Finding related companies',
    prices: 'Downloading prices',
    alphas: 'Generating alpha formulas',
    train: 'Training models',
    backtest: 'Backtesting',
    predict: 'Predicting next day',
};

export function stageLabel(job) {
    if (!job) return '';
    if (job.status === 'queued') return 'Waiting in queue';
    if (job.status === 'succeeded') return 'Done';
    if (job.status === 'failed') return 'Failed';
    return STAGE_LABELS[job.stage] || 'Starting';
}

/** "under a minute", "~4 min", "~1 h 35 min". Empty for unknown. */
export function formatDuration(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '';
    if (seconds < 60) return 'under a minute';
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `~${minutes} min`;
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return m ? `~${h} h ${m} min` : `~${h} h`;
}

export const initialJobState = {
    job: null,          // job being shown, for `ticker`
    ticker: null,
    starting: false,    // POST in flight
    notice: null,       // { ticker, kind: 'error' | 'info' | 'success', text }
    pollWarning: null,  // transient connection problem while polling
};

export function predictionJobReducer(state, action) {
    switch (action.type) {
        case 'start':
            return { ...state, starting: true, notice: null, pollWarning: null };

        case 'started':
            return {
                ...state, starting: false, job: action.job, ticker: action.job.ticker,
                notice: action.created ? null
                    : { ticker: action.job.ticker, kind: 'info', text: 'Someone already started this run; following its progress.' },
            };

        case 'conflict': {
            // The user's own job for this ticker: just follow it. For another ticker, explain.
            if (action.job && action.job.ticker === action.ticker) {
                return { ...state, starting: false, job: action.job, ticker: action.ticker, notice: null };
            }
            const eta = formatDuration(action.job?.etaSeconds);
            return {
                ...state, starting: false,
                notice: {
                    ticker: action.ticker, kind: 'error',
                    text: `${action.message || 'You already have a job in progress.'}${eta ? ` (${eta} left)` : ''} `
                        + 'You can start another once it finishes.',
                },
            };
        }

        case 'startFailed':
            return { ...state, starting: false, notice: { ticker: action.ticker, kind: 'error', text: action.message } };

        case 'polled': {
            const job = action.job;
            if (state.job && state.job.id !== job.id && isActiveJob(state.job) && state.job.ticker === job.ticker) {
                return state;   // a late response for a job we are no longer following
            }
            let notice = state.notice;
            if (job.status === 'succeeded') {
                notice = {
                    ticker: job.ticker, kind: 'success',
                    text: job.kind === 'train' ? 'Training finished; prediction updated.' : 'Prediction refreshed.',
                };
            } else if (job.status === 'failed') {
                notice = { ticker: job.ticker, kind: 'error', text: job.error || 'The run failed.' };
            }
            return { ...state, job, ticker: job.ticker, notice, pollWarning: null };
        }

        case 'pollFailed':
            if (action.fatal && state.job?.id === action.jobId) {
                return {
                    ...state,
                    job: { ...state.job, status: 'failed', error: action.message },
                    notice: { ticker: state.job.ticker, kind: 'error', text: action.message },
                    pollWarning: null,
                };
            }
            return { ...state, pollWarning: action.message };

        case 'dismiss':
            return { ...state, notice: null };

        default:
            return state;
    }
}
