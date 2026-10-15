import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import { useSymbols } from '../hooks/useSymbols';
import TickerAvatar from './TickerAvatar';

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

/**
 * Symbol search with suggestions. Ctrl+K / ⌘K focuses it from anywhere.
 * `onSelect(ticker)` receives an uppercase ticker.
 */
export default function SymbolSearch({ onSelect }) {
    const symbols = useSymbols();
    const [query, setQuery] = useState('');
    const [open, setOpen] = useState(false);
    const [highlight, setHighlight] = useState(-1);
    const inputRef = useRef(null);
    const listId = useId();

    useEffect(() => {
        const onKey = (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
                e.preventDefault();
                inputRef.current?.focus();
                inputRef.current?.select();
            }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, []);

    const results = useMemo(() => {
        const q = query.trim().toUpperCase();
        if (!q) return symbols.slice(0, 12);
        return symbols
            .filter(s => s.ticker.toUpperCase().includes(q) || (s.name || '').toUpperCase().includes(q))
            .slice(0, 30);
    }, [symbols, query]);

    const choose = (ticker) => {
        const symbol = ticker.trim().toUpperCase();
        if (!symbol) return;
        onSelect(symbol);
        setQuery('');
        setOpen(false);
        setHighlight(-1);
        inputRef.current?.blur();
    };

    const onKeyDown = (e) => {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setOpen(true);
            setHighlight(h => Math.min(results.length - 1, h + 1));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setHighlight(h => Math.max(-1, h - 1));
        } else if (e.key === 'Enter') {
            e.preventDefault();
            choose(highlight >= 0 && results[highlight] ? results[highlight].ticker : query);
        } else if (e.key === 'Escape') {
            setOpen(false);
            inputRef.current?.blur();
        }
    };

    const showList = open && results.length > 0;

    return (
        <div className="sidebar-search">
            <Search size={14} className="search-icon" aria-hidden="true" />
            <input
                ref={inputRef}
                type="search"
                role="combobox"
                aria-label="Search symbols"
                aria-expanded={showList}
                aria-controls={listId}
                aria-autocomplete="list"
                aria-activedescendant={showList && highlight >= 0 ? `${listId}-${highlight}` : undefined}
                placeholder="Search symbol..."
                value={query}
                onChange={e => { setQuery(e.target.value); setOpen(true); setHighlight(-1); }}
                onFocus={() => setOpen(true)}
                onBlur={() => setTimeout(() => setOpen(false), 120)}
                onKeyDown={onKeyDown}
            />
            <span className="search-kbd" aria-hidden="true">
                <span className="kbd">{isMac ? '⌘' : 'Ctrl'}</span>
                <span className="kbd">K</span>
            </span>
            {showList && (
                <div className="search-results" role="listbox" id={listId} aria-label="Symbols">
                    {results.map((s, i) => (
                        <button
                            key={s.ticker}
                            id={`${listId}-${i}`}
                            type="button"
                            role="option"
                            aria-selected={i === highlight}
                            className="search-result"
                            onMouseDown={e => { e.preventDefault(); choose(s.ticker); }}
                            onMouseEnter={() => setHighlight(i)}
                        >
                            <TickerAvatar ticker={s.ticker} />
                            <span className="mono">{s.ticker}</span>
                            <span className="name">{s.name}</span>
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}
