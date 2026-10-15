import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Briefcase, TrendingUp, Wallet } from 'lucide-react';
import { Client } from '@stomp/stompjs';
import SockJS from 'sockjs-client';
import { useAuth } from '../context/AuthContext';
import { getPortfolio } from '../api/portfolioApi';
import { getTradeHistory } from '../api/tradeApi';
import { rememberTicker } from '../utils/preferences';
import AppShell from '../components/AppShell';
import ChangePill from '../components/ChangePill';
import TickerAvatar from '../components/TickerAvatar';

const STARTING_BALANCE = 100000;

const fmt = (n) => n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtPrice = (n) => n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 });
const signed = (n) => `${n >= 0 ? '+' : '−'}$${fmt(Math.abs(n))}`;

function StatCard({ icon: Icon, label, name, value, change }) {
    return (
        <div className="card stat-card">
            <div className="stat-card-top">
                <span className="stat-icon" aria-hidden="true"><Icon size={15} /></span>
                <div>
                    <div className="stat-label">{label}</div>
                    <div className="stat-name">{name}</div>
                </div>
            </div>
            <div>
                <div className="stat-label">Value (USD)</div>
                <div className="stat-value mono">
                    {value}
                    {change}
                </div>
            </div>
        </div>
    );
}

export default function PortfolioPage() {
    const { accessToken } = useAuth();
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

    const openChart = (ticker) => { rememberTicker(ticker); navigate('/chart'); };

    const balance = portfolio ? parseFloat(portfolio.balance) : 0;
    const holdings = portfolio ? portfolio.holdings : [];

    let totalInvested = 0;
    let totalCurrentValue = 0;
    holdings.forEach(h => {
        const currentPrice = livePrices[h.ticker] ?? parseFloat(h.avgBuyPrice);
        totalInvested += parseFloat(h.investedAmount);
        totalCurrentValue += parseFloat(h.quantity) * currentPrice;
    });

    const totalPL = totalCurrentValue - totalInvested;
    const totalPLPercent = totalInvested > 0 ? (totalPL / totalInvested) * 100 : 0;
    const portfolioValue = balance + totalCurrentValue;
    const overallPL = portfolioValue - STARTING_BALANCE;
    const overallPLPercent = (overallPL / STARTING_BALANCE) * 100;

    return (
        <AppShell breadcrumb={['Portfolio', 'Overview']}>
            <div className="page-header">
                <div>
                    <h1 className="page-title">Portfolio</h1>
                    <p className="page-subtitle">Your paper-trading account, valued at live prices.</p>
                </div>
            </div>

            {loading ? (
                <p className="muted">Loading portfolio…</p>
            ) : (
                <>
                    <div className="stat-grid">
                        <StatCard
                            icon={Briefcase} label="Account" name="Total portfolio value"
                            value={`$${fmt(portfolioValue)}`}
                            change={<ChangePill value={overallPLPercent} label="since start" />}
                        />
                        <StatCard icon={Wallet} label="Account" name="Cash balance" value={`$${fmt(balance)}`} />
                        <StatCard
                            icon={TrendingUp} label="Positions" name="Invested value"
                            value={`$${fmt(totalCurrentValue)}`}
                            change={holdings.length > 0 ? <ChangePill value={totalPLPercent} label="unrealised" /> : null}
                        />
                    </div>

                    <section className="card" aria-labelledby="positions-title">
                        <div className="card-header">
                            <div>
                                <h2 id="positions-title" className="card-title">{tab === 'holdings' ? 'Holdings' : 'Trade history'}</h2>
                                <p className="card-subtitle">
                                    {tab === 'holdings'
                                        ? <>Open positions with live P&amp;L · overall <span className={`mono ${overallPL >= 0 ? 'up' : 'down'}`}>{signed(overallPL)}</span></>
                                        : 'Every buy and sell on this account'}
                                </p>
                            </div>
                            <div className="segmented" role="tablist" aria-label="Portfolio view">
                                <button type="button" role="tab" aria-selected={tab === 'holdings'} onClick={() => setTab('holdings')}>
                                    Holdings <span className="mono">{holdings.length}</span>
                                </button>
                                <button type="button" role="tab" aria-selected={tab === 'history'} onClick={() => setTab('history')}>
                                    History <span className="mono">{history.length}</span>
                                </button>
                            </div>
                        </div>

                        {tab === 'holdings' && (
                            holdings.length === 0 ? (
                                <div className="empty-state">No holdings yet. Open a chart and make your first trade.</div>
                            ) : (
                                <div className="table-wrap">
                                    <table className="table">
                                        <thead>
                                            <tr>
                                                <th>#</th>
                                                <th>Symbol</th>
                                                <th className="num">Qty</th>
                                                <th className="num">Avg buy</th>
                                                <th className="num">Price</th>
                                                <th className="num">Invested</th>
                                                <th className="num">Value</th>
                                                <th className="num">P&amp;L</th>
                                                <th className="num">P&amp;L %</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {holdings.map((h, i) => {
                                                const currentPrice = livePrices[h.ticker] ?? parseFloat(h.avgBuyPrice);
                                                const qty = parseFloat(h.quantity);
                                                const invested = parseFloat(h.investedAmount);
                                                const currentValue = qty * currentPrice;
                                                const pl = currentValue - invested;
                                                const plPct = invested > 0 ? (pl / invested) * 100 : 0;
                                                return (
                                                    <tr key={h.ticker}>
                                                        <td className="mono muted">{String(i + 1).padStart(2, '0')}</td>
                                                        <td>
                                                            <button type="button" className="nav-item" style={{ height: 26, padding: 0, width: 'auto' }}
                                                                onClick={() => openChart(h.ticker)} title={`Open ${h.ticker} chart`}>
                                                                <TickerAvatar ticker={h.ticker} />
                                                                <span className="mono" style={{ color: 'var(--text)' }}>{h.ticker}</span>
                                                            </button>
                                                        </td>
                                                        <td className="num mono">{qty % 1 === 0 ? qty : qty.toFixed(6)}</td>
                                                        <td className="num mono">${fmtPrice(parseFloat(h.avgBuyPrice))}</td>
                                                        <td className="num mono">
                                                            {livePrices[h.ticker] ? `$${fmtPrice(currentPrice)}` : <span className="muted">—</span>}
                                                        </td>
                                                        <td className="num mono">${fmt(invested)}</td>
                                                        <td className="num mono">${fmt(currentValue)}</td>
                                                        <td className={`num mono ${pl >= 0 ? 'up' : 'down'}`}>{signed(pl)}</td>
                                                        <td className="num"><ChangePill value={plPct} /></td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            )
                        )}

                        {tab === 'history' && (
                            history.length === 0 ? (
                                <div className="empty-state">No trades yet.</div>
                            ) : (
                                <div className="table-wrap">
                                    <table className="table">
                                        <thead>
                                            <tr>
                                                <th>Type</th>
                                                <th>Symbol</th>
                                                <th className="num">Qty</th>
                                                <th className="num">Price</th>
                                                <th className="num">Total</th>
                                                <th className="num">Date</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {history.map(t => (
                                                <tr key={t.id}>
                                                    <td>
                                                        <span className={`pill ${t.type === 'BUY' ? 'pill-up' : 'pill-down'}`}>{t.type === 'BUY' ? 'Buy' : 'Sell'}</span>
                                                    </td>
                                                    <td>
                                                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 9 }}>
                                                            <TickerAvatar ticker={t.ticker} />
                                                            <span className="mono">{t.ticker}</span>
                                                        </span>
                                                    </td>
                                                    <td className="num mono">{parseFloat(t.quantity).toFixed(6)}</td>
                                                    <td className="num mono">${fmtPrice(parseFloat(t.price))}</td>
                                                    <td className="num mono">${fmt(parseFloat(t.totalAmount))}</td>
                                                    <td className="num muted">
                                                        {new Date(t.executedAt).toLocaleString('en-US', {
                                                            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                                                        })}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )
                        )}
                    </section>
                </>
            )}
        </AppShell>
    );
}
