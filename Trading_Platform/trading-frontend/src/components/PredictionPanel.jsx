import { useState } from 'react';
import { RefreshCw, Sparkles, X } from 'lucide-react';
import { usePredictionJob } from '../hooks/usePredictionJob';
import { formatDuration, stageLabel } from '../utils/predictionJobState';
import { formatPct } from '../utils/predictionOverlay';
import ChangePill from './ChangePill';

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
    const dirClass = p?.direction === 'UP' ? 'up' : p?.direction === 'DOWN' ? 'down' : '';

    const startRetrain = () => { setConfirmRetrain(false); start('train'); };

    return (
        <section className="card prediction-panel" aria-labelledby="prediction-panel-title">
            <div className="card-header">
                <div>
                    <h2 id="prediction-panel-title" className="card-title">Model prediction · {upper}</h2>
                    {ready && model.trainedAt && (
                        <p className="card-subtitle">Trained {fmtDate(model.trainedAt)} on {model.dataStart} – {model.dataEnd}</p>
                    )}
                </div>
                <span className="pill pill-model"><Sparkles size={12} aria-hidden="true" /> Dual-stream LSTM</span>
            </div>

            <div className="card-body">
                {loadError ? (
                    <p className="banner-error">{loadError}</p>
                ) : !model ? (
                    <p className="muted">Loading…</p>
                ) : (
                    <>
                        {ready && p ? (
                            <div className="prediction-grid">
                                <div className="prediction-tile">
                                    <div className="stat-label">Next close ({p.targetDate})</div>
                                    <div className={`stat-value mono ${dirClass}`}>
                                        {p.direction === 'UP' ? '▲' : p.direction === 'DOWN' ? '▼' : '■'} {fmtPrice(p.predictedClose)}
                                        <ChangePill value={p.predictedReturn * 100} label="predicted change" />
                                    </div>
                                    <div className="sub mono">
                                        from {fmtPrice(p.lastClose)} on {p.asOfDate} · ±{p.bandSigmas}σ {fmtPrice(p.lower)} – {fmtPrice(p.upper)} (σ {(p.sigma * 100).toFixed(2)}%)
                                    </div>
                                    <div className="sub">Updated {fmtDate(p.generatedAt)}</div>
                                </div>
                                {m && (
                                    <div className="prediction-tile">
                                        <div className="stat-label">Backtest {model.backtest.start} – {model.backtest.end} (n={m.n})</div>
                                        <dl className="metrics-list">
                                            <dt>Direction accuracy</dt><dd className="mono">{formatPct(m.directionalAccuracy)}</dd>
                                            <dt>Always-majority baseline</dt><dd className="mono">{formatPct(m.majorityBaseline)}</dd>
                                            <dt>Up days called up</dt><dd className="mono">{formatPct(m.upPrecision)}</dd>
                                            <dt>Down days called down</dt><dd className="mono">{formatPct(m.downPrecision)}</dd>
                                        </dl>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <p className="text-2">
                                No model has been trained for {upper} yet.
                                {!busy && ` Training ${trainOnly ? `takes ${trainOnly}` : 'runs the full pipeline'}`}
                                {!busy && queue > 0 && ` after ${formatDuration(queue)} of queued runs`}
                                {!busy && '.'}
                            </p>
                        )}

                        {model && !model.hasNews && (
                            <p className="note">No news source for this asset; the model uses price and technical features only.</p>
                        )}
                        {ready && model.alphaSource === 'fallback' && (
                            <p className="note">Alpha formulas: built-in fallback (the LLM was unavailable at training time).</p>
                        )}

                        {/* Run progress */}
                        <div aria-live="polite" className="button-row" style={{ flexDirection: 'column' }}>
                            {active && job && (
                                <div className="progress-box">
                                    <div className="progress-head">
                                        <span>
                                            {runVerb} · {stageLabel(job)}
                                            {job.status === 'queued' && job.queuePosition ? ` (#${job.queuePosition} in line)` : ''}
                                        </span>
                                        <span className="muted mono">
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
                                        className="progress-track"
                                    >
                                        <div className="progress-fill" style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }} />
                                    </div>
                                    {pollWarning && <div className="note" style={{ color: 'var(--warn)' }}>{pollWarning}</div>}
                                    <div className="note">You can leave this page; the run continues and resumes here when you come back.</div>
                                </div>
                            )}
                            {notice && (
                                <div className={`notice${notice.kind === 'error' ? ' notice-error' : notice.kind === 'success' ? ' notice-success' : ''}`}>
                                    <span>{notice.text}</span>
                                    <button type="button" onClick={dismiss} aria-label="Dismiss message"><X size={14} aria-hidden="true" /></button>
                                </div>
                            )}
                        </div>

                        {/* Controls */}
                        {confirmRetrain && !busy ? (
                            <div className="confirm-box" role="group" aria-label="Confirm retrain">
                                <p>
                                    {ready ? 'Retraining' : 'Training'} runs the full pipeline (news, related companies, LLM
                                    alphas, 3 models) and takes {trainOnly || 'a while'}
                                    {queue > 0 ? `, after ${formatDuration(queue)} of runs already queued` : ''}.
                                    Only one run happens at a time across all users.
                                </p>
                                <div className="button-row">
                                    <button type="button" onClick={startRetrain} className="btn btn-primary">
                                        {ready ? 'Start retrain' : 'Start training'}
                                    </button>
                                    <button type="button" onClick={() => setConfirmRetrain(false)} className="btn">Cancel</button>
                                </div>
                            </div>
                        ) : (
                            <div className="button-row">
                                <button
                                    type="button"
                                    onClick={() => start('refresh')}
                                    disabled={!ready || busy}
                                    className="btn btn-primary"
                                    title={!ready ? 'Train a model first' : undefined}
                                >
                                    <RefreshCw size={14} aria-hidden="true" />
                                    Refresh prediction{refreshEta ? ` (${refreshEta})` : ''}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setConfirmRetrain(true)}
                                    disabled={busy}
                                    className="btn"
                                >
                                    {ready ? 'Retrain' : 'Train model'}{trainEta ? ` (${trainEta})` : ''}
                                </button>
                            </div>
                        )}
                    </>
                )}

                <p className="disclaimer">
                    Research model, not trading advice. Next-day direction is close to a coin flip: compare the accuracy
                    above with the always-majority baseline, and read the up and down rates together.
                </p>
            </div>
        </section>
    );
}
