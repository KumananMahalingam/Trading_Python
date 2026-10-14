import { useState } from 'react';
import { usePredictionJob } from '../hooks/usePredictionJob';
import { formatDuration, stageLabel } from '../utils/predictionJobState';
import { formatPct } from '../utils/predictionOverlay';

const fmtPrice = (v) => (typeof v === 'number' ? v.toFixed(2) : '–');
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString() : '–');

/**
 * Model prediction details and run controls for one ticker.
 *
 * Props:
 *   ticker      platform ticker
 *   model       GET /api/predictions/{ticker} response, or null while loading
 *   loadError   message when the model could not be loaded
 *   onFinished  called when a run this panel follows finishes (refetch the model)
 */
export default function PredictionPanel({ ticker, model, loadError, onFinished }) {
    const { job, active, starting, notice, pollWarning, start, dismiss } = usePredictionJob({
        ticker, activeJob: model?.activeJob, onFinished,
    });
    const [confirmRetrain, setConfirmRetrain] = useState(false);

    const upper = ticker.toUpperCase();
    const ready = model?.status === 'ready';
    const estimates = model?.estimates;
    const busy = active || starting;
    // Button estimates include the wait for jobs already queued. While this
    // ticker's own run is in progress that wait *is* this run, so leave them off.
    const queue = estimates?.queueSeconds ?? 0;
    const refreshEta = estimates && !busy ? formatDuration(estimates.refreshSeconds + queue) : '';
    const trainEta = estimates && !busy ? formatDuration(estimates.trainSeconds + queue) : '';
    const trainOnly = estimates ? formatDuration(estimates.trainSeconds) : '';
    const runVerb = job?.kind === 'train' ? (ready ? 'Retraining' : 'Training') : 'Refreshing';
    const p = model?.prediction;
    const m = model?.backtest?.metrics;

    const startRetrain = () => { setConfirmRetrain(false); start('train'); };

    return (
        <section style={styles.panel} aria-labelledby="prediction-panel-title">
            <div style={styles.header}>
                <h2 id="prediction-panel-title" style={styles.title}>Model prediction · {upper}</h2>
                {ready && model.trainedAt && (
                    <span style={styles.muted}>Trained {fmtDate(model.trainedAt)} on {model.dataStart} – {model.dataEnd}</span>
                )}
            </div>

            {loadError ? (
                <p style={styles.error}>{loadError}</p>
            ) : !model ? (
                <p style={styles.muted}>Loading…</p>
            ) : (
                <>
                    {ready && p ? (
                        <div style={styles.grid}>
                            <div>
                                <div style={styles.label}>Next close ({p.targetDate})</div>
                                <div style={{ ...styles.big, color: p.direction === 'UP' ? '#26a69a' : p.direction === 'DOWN' ? '#ef5350' : '#d1d4dc' }}>
                                    {p.direction === 'UP' ? '▲' : p.direction === 'DOWN' ? '▼' : '■'} {fmtPrice(p.predictedClose)}
                                    <span style={styles.change}> ({p.predictedReturn >= 0 ? '+' : '−'}{Math.abs(p.predictedReturn * 100).toFixed(2)}%)</span>
                                </div>
                                <div style={styles.muted}>
                                    from {fmtPrice(p.lastClose)} on {p.asOfDate} · ±{p.bandSigmas}σ {fmtPrice(p.lower)} – {fmtPrice(p.upper)} (σ {(p.sigma * 100).toFixed(2)}%)
                                </div>
                                <div style={styles.muted}>Updated {fmtDate(p.generatedAt)}</div>
                            </div>
                            {m && (
                                <div>
                                    <div style={styles.label}>Backtest {model.backtest.start} – {model.backtest.end} (n={m.n})</div>
                                    <dl style={styles.metrics}>
                                        <dt>Direction accuracy</dt><dd>{formatPct(m.directionalAccuracy)}</dd>
                                        <dt>Always-majority baseline</dt><dd>{formatPct(m.majorityBaseline)}</dd>
                                        <dt>Up days called up</dt><dd>{formatPct(m.upPrecision)}</dd>
                                        <dt>Down days called down</dt><dd>{formatPct(m.downPrecision)}</dd>
                                    </dl>
                                </div>
                            )}
                        </div>
                    ) : (
                        <p style={styles.muted}>
                            No model has been trained for {upper} yet.
                            {!busy && ` Training ${trainOnly ? `takes ${trainOnly}` : 'runs the full pipeline'}`}
                            {!busy && queue > 0 && ` after ${formatDuration(queue)} of queued runs`}
                            {!busy && '.'}
                        </p>
                    )}

                    {model && !model.hasNews && (
                        <p style={styles.muted}>No news source for this asset; the model uses price and technical features only.</p>
                    )}
                    {ready && model.alphaSource === 'fallback' && (
                        <p style={styles.muted}>Alpha formulas: built-in fallback (the LLM was unavailable at training time).</p>
                    )}

                    {/* Run progress */}
                    <div aria-live="polite" style={styles.live}>
                        {active && job && (
                            <div style={styles.progressBox}>
                                <div style={styles.progressHeader}>
                                    <span>
                                        {runVerb} · {stageLabel(job)}
                                        {job.status === 'queued' && job.queuePosition ? ` (#${job.queuePosition} in line)` : ''}
                                    </span>
                                    <span style={styles.muted}>
                                        {Math.round(job.progress)}%{job.etaSeconds != null ? ` · ${formatDuration(job.etaSeconds)} left` : ''}
                                    </span>
                                </div>
                                <div
                                    role="progressbar"
                                    aria-label={`${runVerb} progress for ${upper}`}
                                    aria-valuemin={0}
                                    aria-valuemax={100}
                                    aria-valuenow={Math.round(job.progress)}
                                    aria-valuetext={`${Math.round(job.progress)}%, ${stageLabel(job)}`}
                                    style={styles.progressTrack}
                                >
                                    <div style={{ ...styles.progressFill, width: `${Math.min(100, Math.max(0, job.progress))}%` }} />
                                </div>
                                {pollWarning && <div style={styles.warn}>{pollWarning}</div>}
                                <div style={styles.muted}>You can leave this page; the run continues and resumes here when you come back.</div>
                            </div>
                        )}
                        {notice && (
                            <div style={{ ...styles.notice, ...(notice.kind === 'error' ? styles.noticeError : notice.kind === 'success' ? styles.noticeSuccess : {}) }}>
                                <span>{notice.text}</span>
                                <button onClick={dismiss} style={styles.dismiss} aria-label="Dismiss message">✕</button>
                            </div>
                        )}
                    </div>

                    {/* Controls */}
                    {confirmRetrain && !busy ? (
                        <div style={styles.confirm} role="group" aria-label="Confirm retrain">
                            <p style={{ margin: 0 }}>
                                {ready ? 'Retraining' : 'Training'} runs the full pipeline (news, related companies, LLM
                                alphas, 3 models) and takes {trainOnly || 'a while'}
                                {queue > 0 ? `, after ${formatDuration(queue)} of runs already queued` : ''}.
                                Only one run happens at a time across all users.
                            </p>
                            <div style={styles.buttons}>
                                <button onClick={startRetrain} style={styles.primary}>
                                    {ready ? 'Start retrain' : 'Start training'}
                                </button>
                                <button onClick={() => setConfirmRetrain(false)} style={styles.secondary}>Cancel</button>
                            </div>
                        </div>
                    ) : (
                        <div style={styles.buttons}>
                            <button
                                onClick={() => start('refresh')}
                                disabled={!ready || busy}
                                style={{ ...styles.primary, ...(!ready || busy ? styles.disabled : {}) }}
                                title={!ready ? 'Train a model first' : undefined}
                            >
                                Refresh prediction{refreshEta ? ` (${refreshEta})` : ''}
                            </button>
                            <button
                                onClick={() => setConfirmRetrain(true)}
                                disabled={busy}
                                style={{ ...styles.secondary, ...(busy ? styles.disabled : {}) }}
                            >
                                {ready ? 'Retrain' : 'Train model'}{trainEta ? ` (${trainEta})` : ''}
                            </button>
                        </div>
                    )}
                </>
            )}

            <p style={styles.disclaimer}>
                Research model, not trading advice. Next-day direction is close to a coin flip: compare the accuracy
                above with the always-majority baseline, and read the up and down rates together.
            </p>
        </section>
    );
}

const styles = {
    panel: {
        marginTop: '0.75rem', padding: '0.9rem 1rem', backgroundColor: '#1e222d', border: '1px solid #2a2e39',
        borderRadius: '4px', color: '#d1d4dc', fontSize: '0.85rem', display: 'flex', flexDirection: 'column', gap: '0.6rem',
    },
    header: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '1rem', flexWrap: 'wrap' },
    title: { margin: 0, fontSize: '0.95rem', fontWeight: 600 },
    grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1rem' },
    label: { color: '#9598a1', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 4 },
    big: { fontSize: '1.3rem', fontWeight: 600 },
    change: { fontSize: '0.9rem', fontWeight: 500 },
    metrics: { display: 'grid', gridTemplateColumns: 'auto auto', columnGap: '1rem', rowGap: '2px', margin: 0 },
    muted: { color: '#9598a1', margin: 0 },
    error: { color: '#ef5350', margin: 0 },
    warn: { color: '#f5c518' },
    live: { display: 'flex', flexDirection: 'column', gap: '0.5rem' },
    progressBox: { display: 'flex', flexDirection: 'column', gap: '6px', padding: '0.6rem', backgroundColor: '#131722', borderRadius: '4px' },
    progressHeader: { display: 'flex', justifyContent: 'space-between', gap: '1rem' },
    progressTrack: { height: 8, backgroundColor: '#2a2e39', borderRadius: 4, overflow: 'hidden' },
    progressFill: { height: '100%', backgroundColor: '#ff9800', transition: 'width 0.4s ease' },
    notice: {
        display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'flex-start',
        padding: '0.5rem 0.6rem', borderRadius: '4px', backgroundColor: '#2a2e39',
    },
    noticeError: { backgroundColor: 'rgba(239,83,80,0.15)', color: '#f7a5a3' },
    noticeSuccess: { backgroundColor: 'rgba(38,166,154,0.15)', color: '#7fd6cd' },
    dismiss: { background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0 },
    confirm: { display: 'flex', flexDirection: 'column', gap: '0.5rem', padding: '0.6rem', border: '1px solid #ff9800', borderRadius: '4px' },
    buttons: { display: 'flex', gap: '0.5rem', flexWrap: 'wrap' },
    primary: {
        backgroundColor: '#2962ff', color: '#fff', border: 'none', borderRadius: '4px',
        padding: '0.45rem 0.9rem', cursor: 'pointer', fontSize: '0.82rem',
    },
    secondary: {
        backgroundColor: 'transparent', color: '#d1d4dc', border: '1px solid #363a45', borderRadius: '4px',
        padding: '0.45rem 0.9rem', cursor: 'pointer', fontSize: '0.82rem',
    },
    disabled: { opacity: 0.45, cursor: 'not-allowed' },
    disclaimer: { color: '#6a6d78', fontSize: '0.72rem', margin: 0 },
};
