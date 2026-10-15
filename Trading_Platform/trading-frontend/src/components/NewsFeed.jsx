import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { getNews } from '../api/newsApi';

const timeAgo = (unixSecs) => {
    const diff = Math.floor(Date.now() / 1000) - unixSecs;
    if (diff < 3600) return `${Math.max(1, Math.floor(diff / 60))}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
};

export default function NewsFeed({ ticker, onClose }) {
    // Keyed by ticker so a stale response is never shown for a new symbol.
    const [state, setState] = useState({ ticker: null, articles: [] });

    useEffect(() => {
        let cancelled = false;
        getNews(ticker)
            .then(articles => { if (!cancelled) setState({ ticker, articles: articles || [] }); })
            .catch(() => { if (!cancelled) setState({ ticker, articles: [] }); });
        return () => { cancelled = true; };
    }, [ticker]);

    const loading = state.ticker !== ticker;
    const articles = loading ? [] : state.articles;

    return (
        <aside className="news-panel" aria-label={`News for ${ticker}`}>
            <div className="news-panel-header">
                <h2>News · <span className="mono">{ticker}</span></h2>
                {onClose && (
                    <button type="button" className="icon-btn" onClick={onClose} aria-label="Close news">
                        <X size={14} />
                    </button>
                )}
            </div>

            {loading && <div className="news-status">Loading…</div>}
            {!loading && articles.length === 0 && <div className="news-status">No recent news found.</div>}

            {articles.map((a, i) => (
                <a key={a.url || i} href={a.url} target="_blank" rel="noopener noreferrer" className="news-item">
                    {a.image && (
                        <img src={a.image} alt="" loading="lazy" onError={e => { e.currentTarget.style.display = 'none'; }} />
                    )}
                    <span className="headline">{a.headline}</span>
                    <span className="meta">
                        <span className="source">{a.source}</span>
                        <span aria-hidden="true">·</span>
                        <span>{timeAgo(a.datetime)}</span>
                    </span>
                </a>
            ))}
        </aside>
    );
}
