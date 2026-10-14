import { useEffect, useState } from 'react';
import { getNews } from '../api/newsApi';

export default function NewsFeed({ ticker }) {
    const [articles, setArticles] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        setLoading(true);
        setArticles([]);
        getNews(ticker)
            .then(setArticles)
            .catch(() => {})
            .finally(() => setLoading(false));
    }, [ticker]);

    const timeAgo = (unixSecs) => {
        const diff = Math.floor(Date.now() / 1000) - unixSecs;
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return `${Math.floor(diff / 86400)}d ago`;
    };

    return (
        <div style={styles.container}>
            <div style={styles.header}>📰 News — {ticker}</div>

            {loading && <div style={styles.status}>Loading...</div>}
            {!loading && articles.length === 0 && (
                <div style={styles.status}>No recent news found.</div>
            )}

            <div style={styles.list}>
                {articles.map((a, i) => (
                    <a
                        key={i}
                        href={a.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={styles.card}
                    >
                        {a.image && (
                            <img
                                src={a.image}
                                alt=""
                                style={styles.thumb}
                                onError={e => { e.target.style.display = 'none'; }}
                            />
                        )}
                        <div style={styles.headline}>{a.headline}</div>
                        <div style={styles.meta}>
                            <span style={styles.source}>{a.source}</span>
                            <span style={styles.dot}>·</span>
                            <span style={styles.time}>{timeAgo(a.datetime)}</span>
                        </div>
                    </a>
                ))}
            </div>
        </div>
    );
}

const styles = {
    container: {
        width: '280px',
        minWidth: '280px',
        backgroundColor: '#1e222d',
        borderLeft: '1px solid #2a2e39',
        display: 'flex',
        flexDirection: 'column',
        overflowY: 'auto',
    },
    header: {
        color: '#787b86',
        fontSize: '0.7rem',
        fontWeight: 'bold',
        letterSpacing: '0.1em',
        textTransform: 'uppercase',
        padding: '0.75rem 1rem 0.5rem',
        borderBottom: '1px solid #2a2e39',
        position: 'sticky',
        top: 0,
        backgroundColor: '#1e222d',
    },
    status: { color: '#787b86', fontSize: '0.82rem', padding: '1rem' },
    list: { flex: 1 },
    card: {
        display: 'flex',
        flexDirection: 'column',
        gap: '0.4rem',
        padding: '0.75rem 1rem',
        borderBottom: '1px solid #2a2e39',
        textDecoration: 'none',
        cursor: 'pointer',
    },
    cardLeft: { flex: 1, minWidth: 0 },
    headline: {
        color: '#d1d4dc',
        fontSize: '0.82rem',
        lineHeight: '1.4',
        display: '-webkit-box',
        WebkitLineClamp: 2,
        WebkitBoxOrient: 'vertical',
        overflow: 'hidden',
    },
    meta: { display: 'flex', gap: '0.4rem', alignItems: 'center' },
    source: { color: '#2962ff', fontSize: '0.72rem', fontWeight: 'bold' },
    dot: { color: '#787b86', fontSize: '0.72rem' },
    time: { color: '#787b86', fontSize: '0.72rem' },
    thumb: { width: '100%', height: '120px', objectFit: 'cover', borderRadius: '4px', marginTop: '0.25rem' },
};
