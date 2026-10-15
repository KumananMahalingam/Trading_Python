import { Fragment } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { Activity, Briefcase, ChartCandlestick, ChevronRight, LogOut, Sparkles } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { BRAND_NAME } from '../brand';
import { rememberTicker } from '../utils/preferences';
import SymbolSearch from './SymbolSearch';

const NAV = [
    { to: '/chart', label: 'Markets', Icon: ChartCandlestick },
    { to: '/portfolio', label: 'Portfolio', Icon: Briefcase },
];

/**
 * Page layout: sidebar (brand, search, navigation, page sections, account),
 * breadcrumb top bar, scrollable content and an optional right-hand panel.
 *
 * Props:
 *   breadcrumb      ['Markets', 'AAPL']; the last item is the current page
 *   actions         nodes on the right of the top bar
 *   sidebarContent  page-specific sidebar sections (e.g. the watchlist)
 *   onSymbolSelect  called with a ticker from the search; defaults to opening its chart
 *   aside           right-hand panel (e.g. news)
 */
export default function AppShell({ breadcrumb = [], actions, sidebarContent, onSymbolSelect, aside, children }) {
    const { logout } = useAuth();
    const navigate = useNavigate();

    const openSymbol = onSymbolSelect ?? ((ticker) => { rememberTicker(ticker); navigate('/chart'); });
    const handleLogout = async () => { await logout(); navigate('/login'); };

    return (
        <div className="app-shell">
            <aside className="sidebar" aria-label="Main">
                <NavLink to="/chart" className="brand">
                    <span className="brand-mark" aria-hidden="true"><Activity size={16} strokeWidth={2.5} /></span>
                    {BRAND_NAME}
                </NavLink>

                <SymbolSearch onSelect={openSymbol} />

                <nav className="nav-section" aria-label="Pages">
                    <div className="nav-label">General</div>
                    {NAV.map(({ to, label, Icon }) => (
                        <NavLink key={to} to={to} className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
                            <Icon size={16} aria-hidden="true" />
                            {label}
                        </NavLink>
                    ))}
                </nav>

                {sidebarContent}

                <div className="sidebar-footer">
                    <div className="promo-card">
                        <h3><Sparkles size={14} aria-hidden="true" /> Model predictions</h3>
                        <p>Open any symbol on the 1D chart and turn on <strong>Model prediction</strong> to see a next-day forecast and its backtest.</p>
                    </div>
                    <button type="button" className="nav-item" onClick={handleLogout}>
                        <LogOut size={16} aria-hidden="true" />
                        Log out
                    </button>
                </div>
            </aside>

            <div className="main">
                <header className="topbar">
                    <nav aria-label="Breadcrumb">
                        <ol className="breadcrumb">
                            {breadcrumb.map((item, i) => (
                                <Fragment key={`${item}-${i}`}>
                                    {i > 0 && <li className="sep" aria-hidden="true"><ChevronRight size={14} /></li>}
                                    <li aria-current={i === breadcrumb.length - 1 ? 'page' : undefined}>{item}</li>
                                </Fragment>
                            ))}
                        </ol>
                    </nav>
                    {actions && <div className="topbar-actions">{actions}</div>}
                </header>

                <div className="content-row">
                    <main className="content">{children}</main>
                    {aside}
                </div>
            </div>
        </div>
    );
}
