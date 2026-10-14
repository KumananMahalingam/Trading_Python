import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Client } from '@stomp/stompjs';
import SockJS from 'sockjs-client';
import { useAuth } from '../context/AuthContext';
import { getPortfolio } from '../api/portfolioApi';
import { getTradeHistory } from '../api/tradeApi';

const STARTING_BALANCE = 100000;

export default function PortfolioPage() {
    const { accessToken, logout } = useAuth();
    const navigate = useNavigate();

    const [portfolio, setPortfolio] = useState(null);
    const [history, setHistory] = useState([]);
    const [livePrices, setLivePrices] = useState({});
    const [loading, setLoading] = useState(true);
    const [tab, setTab] = useState('holdings');

    const fetchData = useCallback(() => {
        if (!accessToken) return;
        Promise.all([getPortfolio(), getTradeHistory()])
            .then(([p, h]) => { setPortfolio(p); setHistory(h); })
            .catch(() => {})
            .finally(() => setLoading(false));
    }, [accessToken]);

    useEffect(() => { fetchData(); }, [fetchData]);

    // Subscribe to live prices for all holdings
    useEffect(() => {
        if (!portfolio || portfolio.holdings.length === 0) return;
        const client = new Client({
            webSocketFactory: () => new SockJS('http://localhost:8080/ws'),
            onConnect: () => {
                portfolio.holdings.forEach(h => {
                    client.subscribe(`/topic/price/${h.ticker}`, (msg) => {
                        const data = JSON.parse(msg.body);
                        setLivePrices(prev => ({ ...prev, [h.ticker]: parseFloat(data.price) }));
                    });
                });
            },
            reconnectDelay: 5000,
        });
        client.activate();
        return () => client.deactivate();
    }, [portfolio]);

    if (loading) return <div style={styles.loading}>Loading portfolio...</div>;

    const balance = portfolio ? parseFloat(portfolio.balance) : 0;
    const holdings = portfolio ? portfolio.holdings : [];

    // Calculate totals
    let totalInvested = 0;
    let totalCurrentValue = 0;
    holdings.forEach(h => {
        const invested = parseFloat(h.investedAmount);
        const currentPrice = livePrices[h.ticker] ?? parseFloat(h.avgBuyPrice);
        const currentValue = parseFloat(h.quantity) * currentPrice;
        totalInvested += invested;
        totalCurrentValue += currentValue;
    });

    const totalPL = totalCurrentValue - totalInvested;
    const totalPLPercent = totalInvested > 0 ? (totalPL / totalInvested) * 100 : 0;
    const portfolioValue = balance + totalCurrentValue;
    const overallPL = portfolioValue - STARTING_BALANCE;
    const overallPLPercent = (overallPL / STARTING_BALANCE) * 100;

    const fmt = (n) => n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const fmtPrice = (n) => n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 });

    return (
        <div style={styles.page}>
            {/* Header */}
            <div style={styles.header}>
                <button onClick={() => navigate('/chart')} style={styles.backBtn}>← Chart</button>
                <span style={styles.title}>Portfolio</span>
                <button onClick={async () => { await logout(); navigate('/login'); }} style={styles.logoutBtn}>Logout</button>
            </div>

            {/* Summary cards */}
            <div style={styles.summaryRow}>
                <div style={styles.card}>
                    <div style={styles.cardLabel}>Total Portfolio Value</div>
                    <div style={styles.cardValue}>${fmt(portfolioValue)}</div>
                    <div style={{ ...styles.cardChange, color: overallPL >= 0 ? '#26a69a' : '#ef5350' }}>
                        {overallPL >= 0 ? '▲' : '▼'} ${fmt(Math.abs(overallPL))} ({Math.abs(overallPLPercent).toFixed(2)}%)
                    </div>
                </div>
                <div style={styles.card}>
                    <div style={styles.cardLabel}>Cash Balance</div>
                    <div style={styles.cardValue}>${fmt(balance)}</div>
                </div>
                <div style={styles.card}>
                    <div style={styles.cardLabel}>Invested Value</div>
                    <div style={styles.cardValue}>${fmt(totalCurrentValue)}</div>
                    {holdings.length > 0 && (
                        <div style={{ ...styles.cardChange, color: totalPL >= 0 ? '#26a69a' : '#ef5350' }}>
                            {totalPL >= 0 ? '▲' : '▼'} ${fmt(Math.abs(totalPL))} ({Math.abs(totalPLPercent).toFixed(2)}%)
                        </div>
                    )}
                </div>
            </div>

            {/* Tabs */}
            <div style={styles.tabRow}>
                <button
                    onClick={() => setTab('holdings')}
                    style={{ ...styles.tab, ...(tab === 'holdings' ? styles.tabActive : {}) }}
                >
                    Holdings ({holdings.length})
                </button>
                <button
                    onClick={() => setTab('history')}
                    style={{ ...styles.tab, ...(tab === 'history' ? styles.tabActive : {}) }}
                >
                    Trade History ({history.length})
                </button>
            </div>

            {/* Holdings table */}
            {tab === 'holdings' && (
                <div style={styles.tableWrapper}>
                    {holdings.length === 0 ? (
                        <div style={styles.empty}>
                            No holdings yet. Go to the chart page and make your first trade!
                        </div>
                    ) : (
                        <table style={styles.table}>
                            <thead>
                                <tr>
                                    {['Symbol', 'Qty', 'Avg Buy', 'Current Price', 'Invested', 'Current Value', 'P&L', 'P&L %'].map(h => (
                                        <th key={h} style={styles.th}>{h}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {holdings.map(h => {
                                    const currentPrice = livePrices[h.ticker] ?? parseFloat(h.avgBuyPrice);
                                    const qty = parseFloat(h.quantity);
                                    const invested = parseFloat(h.investedAmount);
                                    const currentValue = qty * currentPrice;
                                    const pl = currentValue - invested;
                                    const plPct = invested > 0 ? (pl / invested) * 100 : 0;
                                    const isUp = pl >= 0;
                                    return (
                                        <tr key={h.ticker} style={styles.tr}
                                            onClick={() => navigate('/chart')}
                                            title="Click to view chart"
                                        >
                                            <td style={{ ...styles.td, ...styles.tickerCell }}>{h.ticker}</td>
                                            <td style={styles.td}>{qty % 1 === 0 ? qty : qty.toFixed(6)}</td>
                                            <td style={styles.td}>${fmtPrice(parseFloat(h.avgBuyPrice))}</td>
                                            <td style={styles.td}>
                                                {livePrices[h.ticker]
                                                    ? `$${fmtPrice(currentPrice)}`
                                                    : <span style={{ color: '#787b86' }}>Loading...</span>
                                                }
                                            </td>
                                            <td style={styles.td}>${fmt(invested)}</td>
                                            <td style={styles.td}>${fmt(currentValue)}</td>
                                            <td style={{ ...styles.td, color: isUp ? '#26a69a' : '#ef5350' }}>
                                                {isUp ? '+' : ''}${fmt(pl)}
                                            </td>
                                            <td style={{ ...styles.td, color: isUp ? '#26a69a' : '#ef5350' }}>
                                                {isUp ? '+' : ''}{plPct.toFixed(2)}%
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    )}
                </div>
            )}

            {/* Trade history table */}
            {tab === 'history' && (
                <div style={styles.tableWrapper}>
                    {history.length === 0 ? (
                        <div style={styles.empty}>No trades yet.</div>
                    ) : (
                        <table style={styles.table}>
                            <thead>
                                <tr>
                                    {['Type', 'Symbol', 'Qty', 'Price', 'Total', 'Date'].map(h => (
                                        <th key={h} style={styles.th}>{h}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {history.map(t => (
                                    <tr key={t.id} style={styles.tr}>
                                        <td style={{ ...styles.td, color: t.type === 'BUY' ? '#26a69a' : '#ef5350', fontWeight: 'bold' }}>
                                            {t.type}
                                        </td>
                                        <td style={{ ...styles.td, ...styles.tickerCell }}>{t.ticker}</td>
                                        <td style={styles.td}>{parseFloat(t.quantity).toFixed(6)}</td>
                                        <td style={styles.td}>${fmtPrice(parseFloat(t.price))}</td>
                                        <td style={styles.td}>${fmt(parseFloat(t.totalAmount))}</td>
                                        <td style={styles.td}>
                                            {new Date(t.executedAt).toLocaleString('en-US', {
                                                month: 'short', day: 'numeric',
                                                hour: '2-digit', minute: '2-digit',
                                            })}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
            )}
        </div>
    );
}

const styles = {
    page: { backgroundColor: '#131722', minHeight: '100vh', color: '#d1d4dc', fontFamily: 'system-ui, sans-serif' },
    loading: { color: '#787b86', padding: '2rem', textAlign: 'center' },

    header: {
        display: 'flex', alignItems: 'center', gap: '1rem',
        padding: '0.75rem 1.5rem', backgroundColor: '#1e222d', borderBottom: '1px solid #2a2e39',
    },
    backBtn: { backgroundColor: 'transparent', color: '#787b86', border: '1px solid #363a45', borderRadius: '4px', padding: '0.45rem 0.75rem', cursor: 'pointer', fontSize: '0.85rem' },
    title: { fontWeight: 'bold', fontSize: '1.1rem', flex: 1 },
    logoutBtn: { backgroundColor: 'transparent', color: '#787b86', border: '1px solid #363a45', borderRadius: '4px', padding: '0.45rem 0.75rem', cursor: 'pointer', fontSize: '0.85rem' },

    summaryRow: { display: 'flex', gap: '1rem', padding: '1.5rem', flexWrap: 'wrap' },
    card: { backgroundColor: '#1e222d', border: '1px solid #2a2e39', borderRadius: '8px', padding: '1.25rem 1.5rem', minWidth: '200px', flex: 1 },
    cardLabel: { color: '#787b86', fontSize: '0.78rem', marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' },
    cardValue: { color: '#d1d4dc', fontSize: '1.5rem', fontWeight: 'bold' },
    cardChange: { fontSize: '0.85rem', marginTop: '0.35rem' },

    tabRow: { display: 'flex', gap: '0', padding: '0 1.5rem', borderBottom: '1px solid #2a2e39' },
    tab: { backgroundColor: 'transparent', color: '#787b86', border: 'none', borderBottom: '2px solid transparent', padding: '0.75rem 1.25rem', cursor: 'pointer', fontSize: '0.9rem' },
    tabActive: { color: '#d1d4dc', borderBottom: '2px solid #2962ff' },

    tableWrapper: { padding: '1.5rem', overflowX: 'auto' },
    empty: { color: '#787b86', textAlign: 'center', padding: '3rem', fontSize: '0.95rem' },
    table: { width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' },
    th: { color: '#787b86', textAlign: 'left', padding: '0.6rem 1rem', borderBottom: '1px solid #2a2e39', fontSize: '0.78rem', textTransform: 'uppercase', letterSpacing: '0.04em', whiteSpace: 'nowrap' },
    tr: { borderBottom: '1px solid #2a2e39', cursor: 'pointer' },
    td: { padding: '0.75rem 1rem', color: '#d1d4dc', whiteSpace: 'nowrap' },
    tickerCell: { fontWeight: 'bold', color: '#d1d4dc' },
};
