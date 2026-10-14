import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { createChart, CrosshairMode, HistogramSeries, LineSeries } from 'lightweight-charts';
import { createPrimitive } from '../utils/drawingTools';
import { CrosshairHighlightPrimitive } from '../utils/crosshairHighlight';
import { calcSMA, calcEMA, calcBollingerBands } from '../utils/indicators';
import { RoundedCandleSeriesView } from '../utils/roundedCandleSeries';
import { BollingerBandsPrimitive } from '../utils/bollingerBandsPrimitive';
import { Client } from '@stomp/stompjs';
import SockJS from 'sockjs-client';
import { useAuth } from '../context/AuthContext';
import { getCandles } from '../api/candleApi';
import { getAllSymbols } from '../api/symbolApi';
import { getWatchlist, addToWatchlist, removeFromWatchlist } from '../api/watchlistApi';
import { getAlerts, createAlert, cancelAlert, deleteAlert } from '../api/alertApi';
import { getMyProfile } from '../api/userApi';
import { buyStock, sellStock } from '../api/tradeApi';
import { getPortfolio } from '../api/portfolioApi';
import { getPrediction } from '../api/predictionApi';
import { buildPredictionOverlay, describePrediction, formatPct } from '../utils/predictionOverlay';
import { PredictionOverlayRenderer, PREDICTION_COLOR } from '../utils/predictionOverlayRenderer';
import NewsFeed from '../components/NewsFeed';
import PredictionPanel from '../components/PredictionPanel';

// Model predictions are daily, so the overlay is only offered on this interval.
const PREDICTION_INTERVAL = '1D';
const SHOW_PREDICTION_KEY = 'showModelPrediction';
const LAST_TICKER_KEY = 'lastChartTicker';

function predictionErrorMessage(err) {
    const status = err?.response?.status;
    if (status === 503) return 'Prediction service unavailable.';
    if (status === 404) return 'Predictions are not available for this symbol.';
    return err?.response?.data?.message || 'Failed to load the model prediction.';
}

const CRYPTO_SYMBOLS = [
    'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT',
    'ADAUSDT', 'DOGEUSDT', 'MATICUSDT', 'DOTUSDT', 'LINKUSDT',
    'AVAXUSDT', 'UNIUSDT', 'LTCUSDT', 'ATOMUSDT', 'XLMUSDT',
];

const DRAWING_TOOLS = [
    { id: 'select',    icon: '↖',   label: 'Select',       group: 0, clicks: 0 },
    { id: 'trendline', icon: '/',    label: 'Trend Line',   group: 1, clicks: 2 },
    { id: 'ray',       icon: '→',   label: 'Ray',          group: 1, clicks: 2 },
    { id: 'extline',   icon: '↔',   label: 'Ext. Line',    group: 1, clicks: 2 },
    { id: 'hline',     icon: '─',   label: 'H. Line',      group: 2, clicks: 1 },
    { id: 'vline',     icon: '│',   label: 'V. Line',      group: 2, clicks: 1 },
    { id: 'rectangle', icon: '□',   label: 'Rectangle',    group: 3, clicks: 2 },
    { id: 'fibonacci', icon: 'Fib', label: 'Fibonacci',    group: 3, clicks: 2 },
];

const DRAW_COLORS = ['#f5c518', '#2962ff', '#26a69a', '#ef5350', '#d1d4dc'];
const DRAW_WIDTHS = [1, 1.5, 2.5];

export default function ChartPage() {
    const { accessToken, logout } = useAuth();
    const navigate = useNavigate();

    const chartContainerRef = useRef(null);
    const chartRef = useRef(null);
    const seriesRef = useRef(null);
    const volumeSeriesRef = useRef(null);
    const stompClientRef = useRef(null);
    const requestIdRef = useRef(0);

    // Drawing system refs (accessed in chart event handlers, no stale closure issues)
    const activeToolRef = useRef('select');
    const drawingPointRef = useRef(null);
    const previewPrimRef = useRef(null);
    const drawingsRef = useRef([]);       // [{ id, primitive, type, label }]
    const selectedIdRef = useRef(null);
    const activeColorRef = useRef('#f5c518');
    const activeWidthRef = useRef(1.5);
    const drawingIdRef = useRef(0);

    // Indicator series refs (keyed by indicator id)
    const indicatorSeriesRef = useRef({});
    // Dashed live-price line (updated on each WebSocket price tick)
    const priceLineRef = useRef(null);
    // Cache last candle data for recalculating indicators when toggled
    const lastCandleDataRef = useRef([]);

    // Model prediction overlay
    const predictionRendererRef = useRef(null);
    // Remembered across reloads so a running job's progress is visible again.
    const [showPrediction, setShowPredictionState] = useState(() => {
        try { return localStorage.getItem(SHOW_PREDICTION_KEY) === '1'; } catch { return false; }
    });
    const setShowPrediction = (next) => {
        setShowPredictionState(next);
        try { localStorage.setItem(SHOW_PREDICTION_KEY, next ? '1' : '0'); } catch { /* storage unavailable */ }
    };
    const [predictionModel, setPredictionModel] = useState(null);   // last API response
    const [predictionError, setPredictionError] = useState(null);   // { ticker, message }
    const [chartCandles, setChartCandles] = useState([]);           // candles the overlay is aligned to
    const [predictionReload, setPredictionReload] = useState(0);    // bumped when a run finishes
    const reloadPrediction = useCallback(() => setPredictionReload(n => n + 1), []);

    // OHLCV tooltip state
    const [tooltipData, setTooltipData] = useState(null);
    const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
    const [isDragging, setIsDragging] = useState(false);

    // Indicator toggles
    const [activeIndicators, setActiveIndicators] = useState(new Set());
    const activeIndicatorsRef = useRef(new Set()); // always current, used in candle-fetch closure
    const [showIndicatorMenu, setShowIndicatorMenu] = useState(false);

    // Chart state
    // The last viewed symbol is restored on reload (so a running model job is
    // visible again); BTCUSDT on first visit.
    const [ticker, setTicker] = useState(() => {
        try { return localStorage.getItem(LAST_TICKER_KEY) || 'BTCUSDT'; } catch { return 'BTCUSDT'; }
    });
    const [searchInput, setSearchInput] = useState(ticker);
    useEffect(() => {
        try { localStorage.setItem(LAST_TICKER_KEY, ticker.toUpperCase()); } catch { /* storage unavailable */ }
    }, [ticker]);
    const [interval, setInterval] = useState('1D');
    const [livePrice, setLivePrice] = useState(null);
    const [priceChange, setPriceChange] = useState(null);
    const [priceChangePercent, setPriceChangePercent] = useState(null);
    const [error, setError] = useState('');
    const [symbols, setSymbols] = useState([]);
    const [showDropdown, setShowDropdown] = useState(false);

    // Drawing state (mirror of drawingsRef for rendering)
    const [activeTool, setActiveTool] = useState('select');
    const [awaitingSecondClick, setAwaitingSecondClick] = useState(false);
    const [drawings, setDrawings] = useState([]);   // [{ id, type, label }]
    const [selectedId, setSelectedId] = useState(null);
    const [activeColor, setActiveColor] = useState('#f5c518');
    const [activeWidth, setActiveWidth] = useState(1.5);

    // Watchlist
    const [watchlist, setWatchlist] = useState([]);
    const [watchlistPrices, setWatchlistPrices] = useState({});
    const [watchlistError, setWatchlistError] = useState('');

    // Paper trading
    const [balance, setBalance] = useState(null);
    const [showTradeModal, setShowTradeModal] = useState(false);
    const [tradeType, setTradeType] = useState('BUY');
    const [tradeQuantity, setTradeQuantity] = useState('');
    const [tradeError, setTradeError] = useState('');

    // Alerts
    const [alerts, setAlerts] = useState([]);
    const [userId, setUserId] = useState(null);
    const [showAlertModal, setShowAlertModal] = useState(false);
    const [alertTargetPrice, setAlertTargetPrice] = useState('');
    const [alertCondition, setAlertCondition] = useState('ABOVE');
    const [alertError, setAlertError] = useState('');
    const [alertNotification, setAlertNotification] = useState(null);
    const [showAlerts, setShowAlerts] = useState(false);
    const [showNews, setShowNews] = useState(false);

    const isCrypto = CRYPTO_SYMBOLS.includes(ticker.toUpperCase());
    const INTERVALS = isCrypto ? ['1H', '4H', '1D', '1W'] : ['1D'];

    const predictionEnabled = showPrediction && interval === PREDICTION_INTERVAL;
    const upperTicker = ticker.toUpperCase();
    // Responses for a previous ticker are ignored rather than cleared.
    const currentPrediction = predictionModel?.ticker === upperTicker ? predictionModel : null;
    const currentPredictionError = predictionError?.ticker === upperTicker ? predictionError.message : '';
    const predictionOverlay = useMemo(
        () => (predictionEnabled && currentPrediction ? buildPredictionOverlay(currentPrediction, chartCandles) : null),
        [predictionEnabled, currentPrediction, chartCandles],
    );

    // ─── Drawing: component-level helpers ────────────────────────────────────

    const syncDrawingsState = () =>
        setDrawings(drawingsRef.current.map(({ id, type, label }) => ({ id, type, label })));

    const selectTool = (tool) => {
        if (previewPrimRef.current && seriesRef.current) {
            seriesRef.current.detachPrimitive(previewPrimRef.current);
            previewPrimRef.current = null;
        }
        drawingPointRef.current = null;
        drawingsRef.current.forEach(d => d.primitive.setSelected(false));
        selectedIdRef.current = null;
        activeToolRef.current = tool;
        setActiveTool(tool);
        setAwaitingSecondClick(false);
        setSelectedId(null);
    };

    const changeColor = (c) => {
        activeColorRef.current = c;
        setActiveColor(c);
        if (previewPrimRef.current) previewPrimRef.current.setColor(c);
    };

    const changeWidth = (w) => {
        activeWidthRef.current = w;
        setActiveWidth(w);
    };

    const deleteDrawingById = (id) => {
        const dr = drawingsRef.current.find(d => d.id === id);
        if (!dr || !seriesRef.current) return;
        seriesRef.current.detachPrimitive(dr.primitive);
        drawingsRef.current = drawingsRef.current.filter(d => d.id !== id);
        if (selectedIdRef.current === id) {
            selectedIdRef.current = null;
            setSelectedId(null);
        }
        syncDrawingsState();
    };

    const clearAllDrawings = () => {
        drawingsRef.current.forEach(d => seriesRef.current?.detachPrimitive(d.primitive));
        if (previewPrimRef.current) {
            seriesRef.current?.detachPrimitive(previewPrimRef.current);
            previewPrimRef.current = null;
        }
        drawingsRef.current = [];
        drawingPointRef.current = null;
        selectedIdRef.current = null;
        setDrawings([]);
        setSelectedId(null);
        setAwaitingSecondClick(false);
    };

    // ─── Data loading ─────────────────────────────────────────────────────────

    useEffect(() => {
        if (!accessToken) return;
        getAllSymbols().then(setSymbols).catch(() => {});
    }, [accessToken]);

    useEffect(() => {
        if (!accessToken) return;
        getMyProfile().then(p => setUserId(p.id)).catch(() => {});
    }, [accessToken]);

    const fetchWatchlist = useCallback(() => {
        if (!accessToken) return;
        getWatchlist().then(setWatchlist).catch(() => {});
    }, [accessToken]);

    useEffect(() => { fetchWatchlist(); }, [fetchWatchlist]);

    const fetchAlerts = useCallback(() => {
        if (!accessToken) return;
        getAlerts().then(setAlerts).catch(() => {});
    }, [accessToken]);

    useEffect(() => { fetchAlerts(); }, [fetchAlerts]);

    const fetchBalance = useCallback(() => {
        if (!accessToken) return;
        getPortfolio().then(p => setBalance(p.balance)).catch(() => {});
    }, [accessToken]);

    useEffect(() => { fetchBalance(); }, [fetchBalance]);

    // ─── WebSocket: watchlist live prices ────────────────────────────────────

    useEffect(() => {
        if (watchlist.length === 0) return;
        const cryptoTickers = watchlist.filter(w => w.assetType === 'CRYPTO').map(w => w.ticker);
        if (cryptoTickers.length === 0) return;
        const client = new Client({
            webSocketFactory: () => new SockJS('http://localhost:8080/ws'),
            onConnect: () => {
                cryptoTickers.forEach(t => {
                    client.subscribe(`/topic/price/${t}`, (msg) => {
                        const data = JSON.parse(msg.body);
                        setWatchlistPrices(prev => ({ ...prev, [t]: parseFloat(data.price) }));
                    });
                });
            },
            reconnectDelay: 5000,
        });
        client.activate();
        return () => client.deactivate();
    }, [watchlist]);

    // ─── WebSocket: alert notifications ──────────────────────────────────────

    useEffect(() => {
        if (!userId) return;
        const client = new Client({
            webSocketFactory: () => new SockJS('http://localhost:8080/ws'),
            onConnect: () => {
                client.subscribe(`/topic/alerts/${userId}`, (msg) => {
                    const data = JSON.parse(msg.body);
                    setAlertNotification(data);
                    fetchAlerts();
                    setTimeout(() => setAlertNotification(null), 6000);
                });
            },
            reconnectDelay: 5000,
        });
        client.activate();
        return () => client.deactivate();
    }, [userId]);

    // ─── Watchlist handlers ───────────────────────────────────────────────────

    const handleAddToWatchlist = async () => {
        setWatchlistError('');
        try { await addToWatchlist(ticker); fetchWatchlist(); }
        catch (err) { setWatchlistError(err.response?.data?.message || 'Could not add to watchlist'); }
    };

    const handleRemoveFromWatchlist = async (t) => {
        try { await removeFromWatchlist(t); fetchWatchlist(); } catch {}
    };

    const isInWatchlist = watchlist.some(w => w.ticker === ticker.toUpperCase());

    // ─── Alert handlers ───────────────────────────────────────────────────────

    const handleCreateAlert = async (e) => {
        e.preventDefault();
        setAlertError('');
        try {
            await createAlert(ticker, parseFloat(alertTargetPrice), alertCondition);
            fetchAlerts(); setShowAlertModal(false); setAlertTargetPrice('');
        } catch (err) { setAlertError(err.response?.data?.message || 'Could not create alert'); }
    };

    const handleCancelAlert = async (id) => { try { await cancelAlert(id); fetchAlerts(); } catch {} };
    const handleDeleteAlert = async (id) => { try { await deleteAlert(id); fetchAlerts(); } catch {} };

    const openAlertModal = () => {
        setAlertTargetPrice(livePrice ? livePrice.toString() : '');
        setAlertCondition('ABOVE'); setAlertError(''); setShowAlertModal(true);
    };

    const tickerAlerts = alerts.filter(a => a.ticker === ticker.toUpperCase() && a.status === 'ACTIVE');

    // ─── Indicator management ─────────────────────────────────────────────────

    const INDICATOR_DEFS = [
        { id: 'sma20',  label: 'SMA 20',  color: '#f5c518', group: 'MA' },
        { id: 'sma50',  label: 'SMA 50',  color: '#2962ff', group: 'MA' },
        { id: 'ema9',   label: 'EMA 9',   color: '#26a69a', group: 'MA' },
        { id: 'bb',     label: 'BB 20',   color: '#9c27b0', group: 'BB' },
    ];

    const applyIndicators = useCallback((candles, indicators) => {
        const chart = chartRef.current;
        const series = seriesRef.current;
        if (!chart || !series || candles.length === 0) return;

        const times = candles.map(c => c.time);
        const closes = candles.map(c => c.close);

        INDICATOR_DEFS.forEach(({ id, color }) => {
            const isActive = indicators.has(id);
            const existing = indicatorSeriesRef.current[id];

            if (!isActive) {
                if (existing) {
                    if (existing.primitive) {
                        series.detachPrimitive(existing.primitive);
                    } else {
                        chart.removeSeries(existing.series);
                        if (existing.upper) chart.removeSeries(existing.upper);
                        if (existing.lower) chart.removeSeries(existing.lower);
                    }
                    delete indicatorSeriesRef.current[id];
                }
                return;
            }

            // Compute data
            let data = [];
            if (id === 'sma20') {
                const values = calcSMA(closes, 20);
                data = times.map((t, i) => values[i] !== undefined ? { time: t, value: values[i] } : null).filter(Boolean);
                if (!existing) {
                    const s = chart.addSeries(LineSeries, { color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
                    indicatorSeriesRef.current[id] = { series: s };
                    s.setData(data);
                } else { existing.series.setData(data); }

            } else if (id === 'sma50') {
                const values = calcSMA(closes, 50);
                data = times.map((t, i) => values[i] !== undefined ? { time: t, value: values[i] } : null).filter(Boolean);
                if (!existing) {
                    const s = chart.addSeries(LineSeries, { color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
                    indicatorSeriesRef.current[id] = { series: s };
                    s.setData(data);
                } else { existing.series.setData(data); }

            } else if (id === 'ema9') {
                const values = calcEMA(closes, 9);
                data = times.map((t, i) => values[i] !== undefined ? { time: t, value: values[i] } : null).filter(Boolean);
                if (!existing) {
                    const s = chart.addSeries(LineSeries, { color, lineWidth: 1.5, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
                    indicatorSeriesRef.current[id] = { series: s };
                    s.setData(data);
                } else { existing.series.setData(data); }

            } else if (id === 'bb') {
                // BB uses a filled-area primitive (ported from bands-indicator plugin)
                // instead of 3 separate LineSeries — gives proper fill between upper/lower bands
                const rawBands = calcBollingerBands(closes, 20, 2);
                const bandsData = rawBands
                    .map((b, i) => b ? { time: times[i], upper: b.upper, mid: b.mid, lower: b.lower } : null)
                    .filter(Boolean);

                if (!existing) {
                    const bbPrim = new BollingerBandsPrimitive();
                    series.attachPrimitive(bbPrim);
                    bbPrim.setData(bandsData);
                    indicatorSeriesRef.current[id] = { primitive: bbPrim };
                } else {
                    existing.primitive.setData(bandsData);
                }
            }
        });
    }, []);

    const toggleIndicator = (id) => {
        setActiveIndicators(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            activeIndicatorsRef.current = next;
            applyIndicators(lastCandleDataRef.current, next);
            return next;
        });
    };

    // ─── Chart setup (runs once) ──────────────────────────────────────────────

    useEffect(() => {
        const chart = createChart(chartContainerRef.current, {
            width: chartContainerRef.current.clientWidth,
            height: 420,
            layout: { background: { type: 'solid', color: '#1e222d' }, textColor: '#d1d4dc' },
            grid: { vertLines: { color: '#2a2e39' }, horzLines: { color: '#2a2e39' } },
            crosshair: { mode: CrosshairMode.MagnetOHLC },
            rightPriceScale: { borderColor: '#363a45' },
            timeScale: { borderColor: '#363a45', timeVisible: true },
        });

        // Rounded candles — ported from plugin-examples/rounded-candles-series
        const series = chart.addCustomSeries(new RoundedCandleSeriesView(), {});

        const volumeSeries = chart.addSeries(HistogramSeries, {
            priceFormat: { type: 'volume' }, priceScaleId: 'volume',
        });
        chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

        chartRef.current = chart;
        seriesRef.current = series;
        volumeSeriesRef.current = volumeSeries;

        // ── Live price line — dashed, updates on each WebSocket tick ──────────
        priceLineRef.current = series.createPriceLine({
            price: 0,
            color: '#2962ff',
            lineWidth: 1,
            lineStyle: 2, // LineStyle.Dashed
            axisLabelVisible: true,
            title: '',
        });

        predictionRendererRef.current = new PredictionOverlayRenderer(chart, series);

        // ── Crosshair bar highlight (adapted from highlight-bar-crosshair plugin) ──
        const crosshairHighlight = new CrosshairHighlightPrimitive('rgba(255,255,255,0.07)');
        series.attachPrimitive(crosshairHighlight);

        // ── OHLCV tooltip on crosshair move ───────────────────────────────────
        chart.subscribeCrosshairMove((param) => {
            if (!param.point || !param.time) {
                setTooltipData(null);
                return;
            }
            const d = param.seriesData.get(series);
            if (!d || d.open === undefined) { setTooltipData(null); return; }
            setTooltipData({
                open:  parseFloat(d.open).toFixed(2),
                high:  parseFloat(d.high).toFixed(2),
                low:   parseFloat(d.low).toFixed(2),
                close: parseFloat(d.close).toFixed(2),
                isUp:  d.close >= d.open,
            });
            setTooltipPos({ x: param.point.x, y: param.point.y });
        });

        const handleResize = () => {
            if (chartContainerRef.current) chart.applyOptions({ width: chartContainerRef.current.clientWidth });
        };
        window.addEventListener('resize', handleResize);

        // Inner helpers scoped to this effect (series is in scope here)
        const addDrawing = (type, label, primitive) => {
            const id = ++drawingIdRef.current;
            series.attachPrimitive(primitive);
            drawingsRef.current.push({ id, primitive, type, label });
            setDrawings(drawingsRef.current.map(({ id, type, label }) => ({ id, type, label })));
        };

        const removePreview = () => {
            if (previewPrimRef.current) {
                series.detachPrimitive(previewPrimRef.current);
                previewPrimRef.current = null;
            }
            drawingPointRef.current = null;
            setAwaitingSecondClick(false);
        };

        // ── Click handler ─────────────────────────────────────────────────────

        chart.subscribeClick((param) => {
            if (!param.point || !param.time) return;
            const tool = activeToolRef.current;

            // Select mode: hit-test drawings
            if (tool === 'select') {
                drawingsRef.current.forEach(d => d.primitive.setSelected(false));
                let found = null;
                for (const d of [...drawingsRef.current].reverse()) {
                    if (d.primitive.hitTest(param.point.x, param.point.y)) {
                        d.primitive.setSelected(true);
                        found = d.id;
                        break;
                    }
                }
                selectedIdRef.current = found;
                setSelectedId(found);
                return;
            }

            const price = series.coordinateToPrice(param.point.y);
            if (price === null) return;

            const toolDef = DRAWING_TOOLS.find(t => t.id === tool);
            if (!toolDef) return;

            // Single-click tools (hline, vline)
            if (toolDef.clicks === 1) {
                const p = { time: param.time, price };
                const prim = createPrimitive(tool, p, p, {
                    color: activeColorRef.current,
                    lineWidth: activeWidthRef.current,
                });
                if (prim) addDrawing(tool, toolDef.label, prim);
                return;
            }

            // Two-click tools
            if (!drawingPointRef.current) {
                drawingPointRef.current = { time: param.time, price };
                setAwaitingSecondClick(true);
                const preview = createPrimitive(tool, drawingPointRef.current, drawingPointRef.current, {
                    color: activeColorRef.current,
                    lineWidth: activeWidthRef.current,
                    preview: true,
                });
                if (preview) {
                    previewPrimRef.current = preview;
                    series.attachPrimitive(preview);
                }
            } else {
                const p1 = drawingPointRef.current;
                const p2 = { time: param.time, price };
                removePreview();
                const prim = createPrimitive(tool, p1, p2, {
                    color: activeColorRef.current,
                    lineWidth: activeWidthRef.current,
                });
                if (prim) addDrawing(tool, toolDef.label, prim);
            }
        });

        // ── Crosshair move: update live preview ───────────────────────────────

        chart.subscribeCrosshairMove((param) => {
            if (!previewPrimRef.current || !param.time || !param.point) return;
            const price = series.coordinateToPrice(param.point.y);
            if (price !== null) previewPrimRef.current.setP2({ time: param.time, price });
        });

        // ── Keyboard shortcuts ────────────────────────────────────────────────

        const handleKeyDown = (e) => {
            const tag = e.target.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

            if (e.key === 'Escape') {
                removePreview();
                activeToolRef.current = 'select';
                setActiveTool('select');
            }

            if ((e.key === 'Delete' || e.key === 'Backspace') && selectedIdRef.current != null) {
                e.preventDefault();
                const id = selectedIdRef.current;
                const dr = drawingsRef.current.find(d => d.id === id);
                if (dr) {
                    series.detachPrimitive(dr.primitive);
                    drawingsRef.current = drawingsRef.current.filter(d => d.id !== id);
                    selectedIdRef.current = null;
                    setSelectedId(null);
                    setDrawings(drawingsRef.current.map(({ id, type, label }) => ({ id, type, label })));
                }
            }
        };
        window.addEventListener('keydown', handleKeyDown);

        // ── Drag to move / reshape drawings ───────────────────────────────────
        // Adapted from rectangle-drawing-tool pattern in the LW Charts repo.
        // dragState tracks what we're doing; origCoords stores pixel positions
        // captured at mousedown so we can compute deltas smoothly.

        const chartEl = chartContainerRef.current;
        let dragState = null;
        let origCoords = null;

        const onMouseDown = (e) => {
            if (activeToolRef.current !== 'select') return;
            const id = selectedIdRef.current;
            if (id == null) return;

            const drawing = drawingsRef.current.find(d => d.id === id);
            if (!drawing) return;

            const rect = chartEl.getBoundingClientRect();
            const mx = e.clientX - rect.left;
            const my = e.clientY - rect.top;
            const prim = drawing.primitive;

            // 1. Check endpoint handles first (higher priority)
            const handles = prim.getHandles();
            for (const h of handles) {
                if (Math.hypot(mx - h.x, my - h.y) < 12) {
                    dragState = { mode: 'endpoint', endpoint: h.endpoint, type: drawing.type };
                    origCoords = null;
                    e.stopPropagation();
                    e.preventDefault();
                    setIsDragging(true);
                    return;
                }
            }

            // 2. Check line/body hit
            if (prim.hitTest(mx, my)) {
                const ts = chart.timeScale();
                const p1 = prim.getP1(), p2 = prim.getP2();
                origCoords = {
                    p1x: ts.timeToCoordinate(p1.time) ?? mx,
                    p1y: series.priceToCoordinate(p1.price) ?? my,
                    p2x: ts.timeToCoordinate(p2.time) ?? mx,
                    p2y: series.priceToCoordinate(p2.price) ?? my,
                    mouseX: mx,
                    mouseY: my,
                };
                dragState = { mode: 'line', type: drawing.type };
                e.stopPropagation();
                e.preventDefault();
                setIsDragging(true);
            }
        };

        const onMouseMove = (e) => {
            if (!dragState) return;
            const id = selectedIdRef.current;
            if (id == null) return;
            const drawing = drawingsRef.current.find(d => d.id === id);
            if (!drawing) { dragState = null; return; }

            const rect = chartEl.getBoundingClientRect();
            const mx = e.clientX - rect.left;
            const my = e.clientY - rect.top;
            const prim = drawing.primitive;
            const ts = chart.timeScale();
            const isHLine = dragState.type === 'hline';
            const isVLine = dragState.type === 'vline';

            if (dragState.mode === 'endpoint') {
                // Snap time to nearest bar; free price movement
                const time = isHLine ? prim.getP1().time : (ts.coordinateToTime(mx) ?? prim.getP1().time);
                const price = isVLine ? prim.getP1().price : (series.coordinateToPrice(my) ?? prim.getP1().price);
                if (dragState.endpoint === 1) prim.setP1({ time, price });
                else prim.setP2({ time, price });

            } else if (dragState.mode === 'line' && origCoords) {
                const dx = mx - origCoords.mouseX;
                const dy = my - origCoords.mouseY;

                // Translate each anchor point by pixel delta, then convert back to time/price
                const newP1x = origCoords.p1x + (isHLine ? 0 : dx);
                const newP1y = origCoords.p1y + (isVLine ? 0 : dy);
                const newP2x = origCoords.p2x + (isHLine ? 0 : dx);
                const newP2y = origCoords.p2y + (isVLine ? 0 : dy);

                const t1 = ts.coordinateToTime(newP1x) ?? prim.getP1().time;
                const t2 = ts.coordinateToTime(newP2x) ?? prim.getP2().time;
                const pr1 = series.coordinateToPrice(newP1y) ?? prim.getP1().price;
                const pr2 = series.coordinateToPrice(newP2y) ?? prim.getP2().price;

                prim.setP1({ time: t1, price: pr1 });
                prim.setP2({ time: t2, price: pr2 });
            }

            prim.requestRepaint();
            e.preventDefault();
        };

        const onMouseUp = () => {
            dragState = null;
            origCoords = null;
            setIsDragging(false);
        };

        // Use capture phase so we intercept before the chart's own handlers
        chartEl.addEventListener('mousedown', onMouseDown, true);
        window.addEventListener('mousemove', onMouseMove);
        window.addEventListener('mouseup', onMouseUp);

        return () => {
            window.removeEventListener('resize', handleResize);
            window.removeEventListener('keydown', handleKeyDown);
            chartEl.removeEventListener('mousedown', onMouseDown, true);
            window.removeEventListener('mousemove', onMouseMove);
            window.removeEventListener('mouseup', onMouseUp);
            priceLineRef.current = null;
            predictionRendererRef.current = null;
            chart.remove();
        };
    }, []);

    // ─── Fetch candles ────────────────────────────────────────────────────────

    useEffect(() => {
        if (!seriesRef.current || !accessToken) return;
        const currentId = ++requestIdRef.current;
        setError(''); setLivePrice(null);

        getCandles(ticker, interval)
            .then(candles => {
                if (currentId !== requestIdRef.current) return;
                const formatted = candles
                    .map(c => ({
                        time: Math.floor(new Date(c.timestamp).getTime() / 1000),
                        open: parseFloat(c.open), high: parseFloat(c.high),
                        low: parseFloat(c.low), close: parseFloat(c.close),
                        volume: parseFloat(c.volume),
                    }))
                    .sort((a, b) => a.time - b.time);
                seriesRef.current.setData(formatted);
                volumeSeriesRef.current.setData(formatted.map(c => ({
                    time: c.time, value: c.volume,
                    color: c.close >= c.open ? '#26a69a80' : '#ef535080',
                })));
                chartRef.current.timeScale().fitContent();
                lastCandleDataRef.current = formatted;
                setChartCandles(formatted);
                applyIndicators(formatted, activeIndicatorsRef.current);
            })
            .catch(() => { if (currentId !== requestIdRef.current) return; setError('Failed to load chart data.'); });
    }, [ticker, interval, accessToken]);

    // ─── Model prediction: fetch ──────────────────────────────────────────────

    useEffect(() => {
        if (!predictionEnabled || !accessToken) return;
        let cancelled = false;
        const symbol = ticker.toUpperCase();
        getPrediction(symbol)
            .then(model => {
                if (cancelled) return;
                setPredictionModel(model);
                setPredictionError(null);
            })
            .catch(err => {
                if (cancelled) return;
                setPredictionError({ ticker: symbol, message: predictionErrorMessage(err) });
            });
        // Drop the response if the overlay is turned off or the ticker changes meanwhile.
        return () => { cancelled = true; };
    }, [predictionEnabled, ticker, accessToken, predictionReload]);

    // ─── Model prediction: draw ───────────────────────────────────────────────

    useEffect(() => {
        const renderer = predictionRendererRef.current;
        if (!renderer) return;
        renderer.update(predictionOverlay);
        // The forecast adds a future day to the time scale; bring it into view.
        if (predictionOverlay?.forecastLine.length) chartRef.current?.timeScale().fitContent();
    }, [predictionOverlay]);

    // ─── WebSocket: live price + candle ──────────────────────────────────────

    useEffect(() => {
        const symbol = ticker.toUpperCase();
        const client = new Client({
            webSocketFactory: () => new SockJS('http://localhost:8080/ws'),
            onConnect: () => {
                client.subscribe(`/topic/price/${symbol}`, (msg) => {
                    const data = JSON.parse(msg.body);
                    const price = parseFloat(data.price);
                    setLivePrice(price);
                    setPriceChange(parseFloat(data.change));
                    setPriceChangePercent(parseFloat(data.changePercent));
                    // Update dashed live-price line
                    priceLineRef.current?.applyOptions({ price });
                });
                client.subscribe(`/topic/candle/${symbol}/${interval}`, (msg) => {
                    const data = JSON.parse(msg.body);
                    if (!seriesRef.current) return;
                    const open = parseFloat(data.open), close = parseFloat(data.close);
                    seriesRef.current.update({ time: data.timestamp, open, high: parseFloat(data.high), low: parseFloat(data.low), close });
                    volumeSeriesRef.current.update({ time: data.timestamp, value: parseFloat(data.volume), color: close >= open ? '#26a69a80' : '#ef535080' });
                });
            },
            reconnectDelay: 5000,
        });
        client.activate();
        stompClientRef.current = client;
        return () => client.deactivate();
    }, [ticker, interval]);

    // ─── Search ───────────────────────────────────────────────────────────────

    const handleSearch = (e) => {
        e.preventDefault();
        const symbol = searchInput.trim().toUpperCase();
        if (symbol) { setTicker(symbol); setShowDropdown(false); }
    };

    const handleSymbolSelect = (symbol) => {
        setSearchInput(symbol); setTicker(symbol); setInterval('1D'); setShowDropdown(false);
    };

    const filteredSymbols = symbols
        .filter(s =>
            s.ticker.toUpperCase().includes(searchInput.toUpperCase()) ||
            s.name.toUpperCase().includes(searchInput.toUpperCase())
        )
        .slice(0, 30);

    // ─── Trade handlers ───────────────────────────────────────────────────────

    const openTradeModal = (type) => {
        setTradeType(type);
        setTradeQuantity('');
        setTradeError('');
        setShowTradeModal(true);
    };

    const handleTrade = async (e) => {
        e.preventDefault();
        setTradeError('');
        const qty = parseFloat(tradeQuantity);
        if (!qty || qty <= 0) { setTradeError('Enter a valid quantity'); return; }
        if (!livePrice) { setTradeError('Live price not available yet'); return; }
        try {
            const res = tradeType === 'BUY'
                ? await buyStock(ticker, qty, livePrice)
                : await sellStock(ticker, qty, livePrice);
            setBalance(res.balanceAfter);
            setShowTradeModal(false);
        } catch (err) {
            setTradeError(err.response?.data?.message || 'Trade failed');
        }
    };

    const handleLogout = async () => { await logout(); navigate('/login'); };
    const isPositive = priceChange >= 0;
    const selectedDrawing = drawings.find(d => d.id === selectedId);

    // ─── Render ───────────────────────────────────────────────────────────────

    return (
        <div style={styles.page}>

            {/* Alert notification banner */}
            {alertNotification && (
                <div style={styles.alertBanner}>
                    🔔 Alert Triggered! <strong>{alertNotification.ticker}</strong> is{' '}
                    {alertNotification.condition === 'ABOVE' ? 'above' : 'below'}{' '}
                    ${parseFloat(alertNotification.targetPrice).toLocaleString()}
                    <button style={styles.bannerClose} onClick={() => setAlertNotification(null)}>✕</button>
                </div>
            )}

            {/* Alert modal */}
            {showAlertModal && (
                <div style={styles.modalOverlay} onClick={() => setShowAlertModal(false)}>
                    <div style={styles.modal} onClick={e => e.stopPropagation()}>
                        <h3 style={styles.modalTitle}>Set Price Alert — {ticker}</h3>
                        <form onSubmit={handleCreateAlert} style={styles.modalForm}>
                            <div style={styles.modalField}>
                                <label style={styles.modalLabel}>Condition</label>
                                <select value={alertCondition} onChange={e => setAlertCondition(e.target.value)} style={styles.modalSelect}>
                                    <option value="ABOVE">Price goes ABOVE</option>
                                    <option value="BELOW">Price goes BELOW</option>
                                </select>
                            </div>
                            <div style={styles.modalField}>
                                <label style={styles.modalLabel}>Target Price (USD)</label>
                                <input
                                    type="number" step="any" value={alertTargetPrice}
                                    onChange={e => setAlertTargetPrice(e.target.value)}
                                    placeholder="Enter price..." required style={styles.modalInput}
                                />
                            </div>
                            {alertError && <p style={styles.modalError}>{alertError}</p>}
                            <div style={styles.modalActions}>
                                <button type="button" onClick={() => setShowAlertModal(false)} style={styles.modalCancel}>Cancel</button>
                                <button type="submit" style={styles.modalSubmit}>Create Alert</button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* Trade modal */}
            {showTradeModal && (
                <div style={styles.modalOverlay} onClick={() => setShowTradeModal(false)}>
                    <div style={styles.modal} onClick={e => e.stopPropagation()}>
                        <h3 style={{ ...styles.modalTitle, color: tradeType === 'BUY' ? '#26a69a' : '#ef5350' }}>
                            {tradeType} {ticker}
                        </h3>
                        <form onSubmit={handleTrade} style={styles.modalForm}>
                            <div style={styles.modalField}>
                                <label style={styles.modalLabel}>Current Price</label>
                                <div style={{ ...styles.modalInput, color: '#d1d4dc', pointerEvents: 'none' }}>
                                    ${livePrice ? livePrice.toLocaleString() : '—'}
                                </div>
                            </div>
                            <div style={styles.modalField}>
                                <label style={styles.modalLabel}>Quantity</label>
                                <input
                                    type="number" step="any" min="0"
                                    value={tradeQuantity}
                                    onChange={e => setTradeQuantity(e.target.value)}
                                    placeholder="e.g. 0.01" required style={styles.modalInput}
                                    autoFocus
                                />
                            </div>
                            {tradeQuantity && livePrice && (
                                <div style={styles.modalField}>
                                    <label style={styles.modalLabel}>Total Cost</label>
                                    <div style={{ color: '#d1d4dc', fontSize: '0.95rem', padding: '0.3rem 0' }}>
                                        ${(parseFloat(tradeQuantity || 0) * livePrice).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                    </div>
                                </div>
                            )}
                            {tradeType === 'BUY' && balance !== null && (
                                <div style={{ color: '#787b86', fontSize: '0.8rem' }}>
                                    Available: ${parseFloat(balance).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                </div>
                            )}
                            {tradeError && <p style={styles.modalError}>{tradeError}</p>}
                            <div style={styles.modalActions}>
                                <button type="button" onClick={() => setShowTradeModal(false)} style={styles.modalCancel}>Cancel</button>
                                <button type="submit" style={tradeType === 'BUY' ? styles.buyModalBtn : styles.sellModalBtn}>
                                    {tradeType}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* Watchlist sidebar */}
            <div style={styles.sidebar}>
                <div style={styles.sidebarTitle}>Watchlist</div>
                {watchlist.length === 0 ? (
                    <p style={styles.emptyText}>No symbols added yet</p>
                ) : (
                    watchlist.map(w => (
                        <div
                            key={w.ticker}
                            style={{ ...styles.watchlistItem, ...(w.ticker === ticker ? styles.watchlistItemActive : {}) }}
                            onClick={() => { setTicker(w.ticker); setSearchInput(w.ticker); }}
                        >
                            <div style={styles.watchlistLeft}>
                                <span style={styles.watchlistTicker}>{w.ticker}</span>
                                <span style={styles.watchlistExchange}>{w.exchange}</span>
                            </div>
                            <div style={styles.watchlistRight}>
                                {watchlistPrices[w.ticker]
                                    ? <span style={styles.watchlistPrice}>${watchlistPrices[w.ticker].toLocaleString()}</span>
                                    : <span style={styles.watchlistNoPrice}>—</span>
                                }
                                <button style={styles.removeBtn} onClick={e => { e.stopPropagation(); handleRemoveFromWatchlist(w.ticker); }}>✕</button>
                            </div>
                        </div>
                    ))
                )}
            </div>

            {/* Vertical drawing tool strip */}
            <div style={styles.toolStrip}>
                {DRAWING_TOOLS.map((tool, i) => {
                    const prev = i > 0 ? DRAWING_TOOLS[i - 1] : null;
                    const showSep = prev && prev.group !== tool.group;
                    return (
                        <div key={tool.id}>
                            {showSep && <div style={styles.toolSep} />}
                            <button
                                title={tool.label}
                                onClick={() => selectTool(tool.id)}
                                style={{ ...styles.toolStripBtn, ...(activeTool === tool.id ? styles.toolStripBtnOn : {}) }}
                            >
                                <span style={styles.toolIcon}>{tool.icon}</span>
                                <span style={styles.toolLabel}>{tool.label.split(' ')[0]}</span>
                            </button>
                        </div>
                    );
                })}

                {drawings.length > 0 && (
                    <>
                        <div style={styles.toolSep} />
                        <div style={styles.drawingCountBadge} title={`${drawings.length} drawing${drawings.length > 1 ? 's' : ''} on chart`}>
                            <span style={styles.toolIcon}>◈</span>
                            <span style={styles.toolLabel}>{drawings.length}</span>
                        </div>
                    </>
                )}
            </div>

            {/* Main content */}
            <div style={styles.main}>

                {/* Header */}
                <div style={styles.header}>
                    <span style={styles.logo}>TradingView Clone</span>
                    <form onSubmit={handleSearch} style={styles.searchForm}>
                        <div style={styles.searchWrapper}>
                            <input
                                type="text" value={searchInput}
                                onChange={e => { setSearchInput(e.target.value); setShowDropdown(true); }}
                                onFocus={() => setShowDropdown(true)}
                                onBlur={() => setTimeout(() => setShowDropdown(false), 150)}
                                placeholder="Search symbol..." style={styles.searchInput}
                            />
                            {showDropdown && filteredSymbols.length > 0 && (
                                <div style={styles.dropdown}>
                                    {filteredSymbols.map(s => (
                                        <div key={s.ticker} style={styles.dropdownItem} onMouseDown={() => handleSymbolSelect(s.ticker)}>
                                            <span style={styles.dropdownTicker}>{s.ticker}</span>
                                            <span style={styles.dropdownName}>{s.name}</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                        <button type="submit" style={styles.searchButton}>Search</button>
                    </form>

                    <button
                        onClick={isInWatchlist ? () => handleRemoveFromWatchlist(ticker) : handleAddToWatchlist}
                        style={{ ...styles.headerBtn, ...(isInWatchlist ? styles.headerBtnActive : {}) }}
                    >
                        {isInWatchlist ? '★ Watching' : '☆ Watchlist'}
                    </button>
                    <button onClick={openAlertModal} style={styles.headerBtn}>🔔 Set Alert</button>
                    <button
                        onClick={() => setShowAlerts(v => !v)}
                        style={{ ...styles.headerBtn, ...(showAlerts ? styles.headerBtnActive : {}) }}
                    >
                        Alerts {alerts.filter(a => a.status === 'ACTIVE').length > 0 && `(${alerts.filter(a => a.status === 'ACTIVE').length})`}
                    </button>
                    <button
                        onClick={() => setShowNews(v => !v)}
                        style={{ ...styles.headerBtn, ...(showNews ? styles.headerBtnActive : {}) }}
                    >
                        📰 News
                    </button>
                    <button onClick={() => navigate('/portfolio')} style={styles.headerBtn}>Portfolio</button>
                    <button onClick={handleLogout} style={styles.logoutButton}>Logout</button>
                </div>

                {watchlistError && <p style={styles.errorMsg}>{watchlistError}</p>}

                {/* Price bar */}
                <div style={styles.priceBar}>
                    <span style={styles.tickerName}>{ticker}</span>
                    {livePrice ? (
                        <>
                            <span style={styles.price}>${livePrice.toLocaleString()}</span>
                            {isCrypto && (
                                <span style={{ ...styles.change, color: isPositive ? '#26a69a' : '#ef5350' }}>
                                    {isPositive ? '▲' : '▼'} {Math.abs(priceChange).toFixed(2)} ({Math.abs(priceChangePercent).toFixed(2)}%)
                                </span>
                            )}
                        </>
                    ) : <span style={styles.noPrice}>Connecting...</span>}

                    <div style={styles.tradeBar}>
                        <span style={styles.balanceDisplay}>
                            ${balance !== null ? parseFloat(balance).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '...'}
                        </span>
                        <button onClick={() => openTradeModal('BUY')} style={styles.buyBtn}>Buy</button>
                        <button onClick={() => openTradeModal('SELL')} style={styles.sellBtn}>Sell</button>
                    </div>
                </div>

                {/* Interval + Indicators bar */}
                <div style={styles.intervalBar}>
                    {INTERVALS.map(i => (
                        <button
                            key={i} onClick={() => setInterval(i)}
                            style={{ ...styles.intervalBtn, ...(interval === i ? styles.intervalBtnActive : {}) }}
                        >
                            {i}
                        </button>
                    ))}

                    <div style={styles.intervalSep} />

                    {/* Indicator toggle buttons */}
                    <div style={{ position: 'relative' }}>
                        <button
                            onClick={() => setShowIndicatorMenu(v => !v)}
                            style={{ ...styles.indicatorMenuBtn, ...(activeIndicators.size > 0 ? styles.indicatorMenuBtnOn : {}) }}
                        >
                            ƒ Indicators {activeIndicators.size + (predictionEnabled ? 1 : 0) > 0 && `(${activeIndicators.size + (predictionEnabled ? 1 : 0)})`}
                        </button>

                        {showIndicatorMenu && (
                            <div style={styles.indicatorMenu}>
                                <div style={styles.indicatorMenuTitle}>Moving Averages</div>
                                {INDICATOR_DEFS.filter(d => d.group === 'MA').map(def => (
                                    <button
                                        key={def.id}
                                        onClick={() => toggleIndicator(def.id)}
                                        style={{
                                            ...styles.indicatorMenuItem,
                                            ...(activeIndicators.has(def.id) ? styles.indicatorMenuItemOn : {}),
                                        }}
                                    >
                                        <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', backgroundColor: def.color, marginRight: 6 }} />
                                        {def.label}
                                        {activeIndicators.has(def.id) && <span style={styles.checkMark}>✓</span>}
                                    </button>
                                ))}
                                <div style={{ ...styles.indicatorMenuTitle, marginTop: 8 }}>Bands</div>
                                {INDICATOR_DEFS.filter(d => d.group === 'BB').map(def => (
                                    <button
                                        key={def.id}
                                        onClick={() => toggleIndicator(def.id)}
                                        style={{
                                            ...styles.indicatorMenuItem,
                                            ...(activeIndicators.has(def.id) ? styles.indicatorMenuItemOn : {}),
                                        }}
                                    >
                                        <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', backgroundColor: def.color, marginRight: 6 }} />
                                        {def.label}
                                        {activeIndicators.has(def.id) && <span style={styles.checkMark}>✓</span>}
                                    </button>
                                ))}
                                {interval === PREDICTION_INTERVAL && (
                                    <>
                                        <div style={{ ...styles.indicatorMenuTitle, marginTop: 8 }}>Model</div>
                                        <button
                                            onClick={() => setShowPrediction(!showPrediction)}
                                            aria-pressed={showPrediction}
                                            style={{
                                                ...styles.indicatorMenuItem,
                                                ...(showPrediction ? styles.indicatorMenuItemOn : {}),
                                            }}
                                        >
                                            <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', backgroundColor: PREDICTION_COLOR, marginRight: 6 }} />
                                            Model prediction
                                            {showPrediction && <span style={styles.checkMark}>✓</span>}
                                        </button>
                                    </>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Active indicator pills */}
                    {INDICATOR_DEFS.filter(d => activeIndicators.has(d.id)).map(def => (
                        <span key={def.id} style={{ ...styles.indicatorPill, borderColor: def.color, color: def.color }}>
                            {def.label}
                            <button onClick={() => toggleIndicator(def.id)} style={styles.indicatorPillClose}>✕</button>
                        </span>
                    ))}
                    {predictionEnabled && (
                        <span style={{ ...styles.indicatorPill, borderColor: PREDICTION_COLOR, color: PREDICTION_COLOR }}>
                            Model
                            <button onClick={() => setShowPrediction(false)} style={styles.indicatorPillClose}
                                aria-label="Hide model prediction">✕</button>
                        </span>
                    )}
                </div>

                {error && <p style={styles.errorMsg}>{error}</p>}

                {/* Drawing options bar — visible when a drawing tool is active */}
                {activeTool !== 'select' && (
                    <div style={styles.drawingBar}>
                        <span style={styles.drawingBarLabel}>
                            {DRAWING_TOOLS.find(t => t.id === activeTool)?.label}
                        </span>

                        {/* Color picker */}
                        <div style={styles.swatchRow}>
                            {DRAW_COLORS.map(c => (
                                <button
                                    key={c}
                                    onClick={() => changeColor(c)}
                                    title={c}
                                    style={{
                                        ...styles.swatch,
                                        backgroundColor: c,
                                        outline: activeColor === c ? '2px solid #fff' : 'none',
                                        outlineOffset: '2px',
                                    }}
                                />
                            ))}
                        </div>

                        {/* Line width */}
                        <div style={styles.widthRow}>
                            {DRAW_WIDTHS.map(w => (
                                <button
                                    key={w}
                                    onClick={() => changeWidth(w)}
                                    title={`Width ${w}`}
                                    style={{ ...styles.widthBtn, ...(activeWidth === w ? styles.widthBtnOn : {}) }}
                                >
                                    <div style={{ width: 18, height: Math.max(1, Math.round(w)), backgroundColor: activeColor, borderRadius: 1 }} />
                                </button>
                            ))}
                        </div>

                        <span style={styles.drawingHint}>
                            {awaitingSecondClick ? '📍 Click 2nd point to finish' : '✦ Click on chart to start'}
                        </span>

                        {awaitingSecondClick && (
                            <button
                                style={styles.escBtn}
                                onClick={() => {
                                    if (previewPrimRef.current && seriesRef.current) {
                                        seriesRef.current.detachPrimitive(previewPrimRef.current);
                                        previewPrimRef.current = null;
                                    }
                                    drawingPointRef.current = null;
                                    setAwaitingSecondClick(false);
                                }}
                            >
                                Esc
                            </button>
                        )}
                    </div>
                )}

                {/* Selection info bar — shown when a drawing is selected */}
                {selectedId != null && activeTool === 'select' && selectedDrawing && (
                    <div style={styles.selBar}>
                        <span style={styles.selDot} />
                        <span style={styles.selText}>{selectedDrawing.label} selected</span>
                        <button onClick={() => deleteDrawingById(selectedId)} style={styles.selDelete}>
                            🗑 Delete this drawing
                        </button>
                        <button
                            onClick={() => {
                                drawingsRef.current.forEach(d => d.primitive.setSelected(false));
                                selectedIdRef.current = null;
                                setSelectedId(null);
                            }}
                            style={styles.selClose}
                        >
                            ✕ Deselect
                        </button>
                        <span style={styles.selHint}>or press Delete key</span>
                    </div>
                )}

                {/* Select-mode hint bar — shown when in select mode, nothing selected, but drawings exist */}
                {activeTool === 'select' && selectedId == null && drawings.length > 0 && (
                    <div style={styles.selectHintBar}>
                        <span style={styles.selectHintText}>
                            ↖ Click on a drawing to select it, then press <kbd style={styles.kbd}>Delete</kbd> or use the Delete button
                        </span>
                        <button onClick={clearAllDrawings} style={styles.clearAllBtn}>
                            🗑 Clear All ({drawings.length})
                        </button>
                    </div>
                )}

                {/* Alerts panel */}
                {showAlerts && (
                    <div style={styles.alertsPanel}>
                        <div style={styles.alertsPanelTitle}>
                            Active Alerts for {ticker}
                            {tickerAlerts.length === 0 && <span style={styles.noAlertsText}> — none set</span>}
                        </div>
                        {tickerAlerts.map(a => (
                            <div key={a.id} style={styles.alertRow}>
                                <span style={styles.alertConditionBadge(a.condition)}>
                                    {a.condition === 'ABOVE' ? '▲' : '▼'} {a.condition}
                                </span>
                                <span style={styles.alertPrice}>${parseFloat(a.targetPrice).toLocaleString()}</span>
                                <button style={styles.cancelAlertBtn} onClick={() => handleCancelAlert(a.id)}>Cancel</button>
                            </div>
                        ))}
                        {alerts.filter(a => a.status === 'TRIGGERED').length > 0 && (
                            <>
                                <div style={{ ...styles.alertsPanelTitle, marginTop: '0.75rem' }}>Triggered</div>
                                {alerts.filter(a => a.status === 'TRIGGERED').map(a => (
                                    <div key={a.id} style={styles.alertRow}>
                                        <span style={styles.alertConditionBadge(a.condition)}>
                                            {a.condition === 'ABOVE' ? '▲' : '▼'} {a.condition}
                                        </span>
                                        <span style={styles.alertPrice}>{a.ticker} @ ${parseFloat(a.targetPrice).toLocaleString()}</span>
                                        <span style={{ color: '#26a69a', fontSize: '0.75rem' }}>✓ Triggered</span>
                                        <button style={styles.cancelAlertBtn} onClick={() => handleDeleteAlert(a.id)}>✕ Delete</button>
                                    </div>
                                ))}
                            </>
                        )}
                    </div>
                )}

                {/* Chart + OHLCV tooltip overlay */}
                <div style={styles.chartWrapper}>
                    <div style={{ position: 'relative' }}>
                        <div
                            ref={chartContainerRef}
                            style={{
                            ...styles.chart,
                            cursor: activeTool !== 'select'
                                ? 'crosshair'
                                : isDragging
                                    ? 'grabbing'
                                    : selectedId != null ? 'grab' : 'default'
                        }}
                        />

                        {/* Model prediction legend */}
                        {predictionEnabled && (
                            <div style={styles.predictionLegend} role="status" aria-live="polite">
                                {currentPredictionError ? (
                                    <span style={styles.predictionLegendMuted}>{currentPredictionError}</span>
                                ) : !currentPrediction ? (
                                    <span style={styles.predictionLegendMuted}>Loading model prediction…</span>
                                ) : currentPrediction.status !== 'ready' ? (
                                    <span style={styles.predictionLegendMuted}>No model trained for {upperTicker} yet.</span>
                                ) : (
                                    <>
                                        {currentPrediction.prediction && (
                                            <div style={{
                                                ...styles.predictionLegendHeadline,
                                                color: currentPrediction.prediction.direction === 'UP' ? '#26a69a'
                                                    : currentPrediction.prediction.direction === 'DOWN' ? '#ef5350' : '#d1d4dc',
                                            }}>
                                                Model: {describePrediction(currentPrediction.prediction)}
                                                <span style={styles.predictionLegendMuted}>
                                                    {' '}· as of {currentPrediction.prediction.asOfDate} close
                                                </span>
                                            </div>
                                        )}
                                        {predictionOverlay?.stale && (
                                            <div style={styles.predictionLegendWarn}>Prediction is out of date; refresh it to update.</div>
                                        )}
                                        {currentPrediction.backtest?.metrics && (
                                            <div style={styles.predictionLegendMuted}>
                                                Backtest {currentPrediction.backtest.start} – {currentPrediction.backtest.end}:{' '}
                                                {formatPct(currentPrediction.backtest.metrics.directionalAccuracy)} direction accuracy
                                                {' '}(always-majority baseline {formatPct(currentPrediction.backtest.metrics.majorityBaseline)},
                                                n={currentPrediction.backtest.metrics.n})
                                                {!predictionOverlay?.backtestLine.length && ' · outside the loaded candles'}
                                            </div>
                                        )}
                                        {!currentPrediction.hasNews && (
                                            <div style={styles.predictionLegendMuted}>No news coverage: price and technical features only.</div>
                                        )}
                                        <div style={styles.predictionLegendMuted}>Research model, not trading advice.</div>
                                    </>
                                )}
                            </div>
                        )}

                        {/* OHLCV floating tooltip — adapted from lightweight-charts tooltip plugin */}
                        {tooltipData && (
                            <div style={{
                                ...styles.ohlcvTooltip,
                                left: tooltipPos.x < 200 ? tooltipPos.x + 12 : tooltipPos.x - 148,
                                top: Math.max(8, tooltipPos.y - 72),
                            }}>
                                <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
                                    {[
                                        { label: 'O', value: tooltipData.open },
                                        { label: 'H', value: tooltipData.high },
                                        { label: 'L', value: tooltipData.low },
                                        { label: 'C', value: tooltipData.close },
                                    ].map(({ label, value }) => (
                                        <div key={label} style={styles.ohlcvItem}>
                                            <span style={styles.ohlcvLabel}>{label}</span>
                                            <span style={{ ...styles.ohlcvValue, color: tooltipData.isUp ? '#26a69a' : '#ef5350' }}>
                                                {value}
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Model details and Refresh / Retrain controls. Keyed by ticker so
                        per-ticker UI state (confirmations, messages) starts fresh. */}
                    {predictionEnabled && (
                        <PredictionPanel
                            key={upperTicker}
                            ticker={upperTicker}
                            model={currentPrediction}
                            loadError={currentPredictionError}
                            onFinished={reloadPrediction}
                        />
                    )}
                </div>

            </div>

            {/* News right sidebar */}
            {showNews && <NewsFeed ticker={ticker} />}

        </div>
    );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles = {
    page: { backgroundColor: '#131722', height: '100vh', overflow: 'hidden', display: 'flex', flexDirection: 'row' },

    alertBanner: {
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 1000,
        backgroundColor: '#2962ff', color: '#fff',
        padding: '0.75rem 1.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem',
        fontSize: '0.95rem',
    },
    bannerClose: { marginLeft: 'auto', backgroundColor: 'transparent', border: 'none', color: '#fff', cursor: 'pointer', fontSize: '1rem' },

    modalOverlay: {
        position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.6)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 500,
    },
    modal: { backgroundColor: '#1e222d', border: '1px solid #363a45', borderRadius: '8px', padding: '2rem', width: '340px' },
    modalTitle: { color: '#d1d4dc', fontSize: '1rem', margin: '0 0 1.5rem' },
    modalForm: { display: 'flex', flexDirection: 'column', gap: '1rem' },
    modalField: { display: 'flex', flexDirection: 'column', gap: '0.4rem' },
    modalLabel: { color: '#787b86', fontSize: '0.8rem' },
    modalSelect: { backgroundColor: '#2a2e39', border: '1px solid #363a45', borderRadius: '4px', padding: '0.6rem', color: '#d1d4dc', fontSize: '0.9rem' },
    modalInput: { backgroundColor: '#2a2e39', border: '1px solid #363a45', borderRadius: '4px', padding: '0.6rem', color: '#d1d4dc', fontSize: '0.9rem', outline: 'none' },
    modalError: { color: '#f7525f', fontSize: '0.8rem', margin: 0 },
    modalActions: { display: 'flex', gap: '0.75rem', marginTop: '0.5rem' },
    modalCancel: { flex: 1, backgroundColor: 'transparent', border: '1px solid #363a45', borderRadius: '4px', padding: '0.6rem', color: '#787b86', cursor: 'pointer' },
    modalSubmit: { flex: 1, backgroundColor: '#2962ff', border: 'none', borderRadius: '4px', padding: '0.6rem', color: '#fff', cursor: 'pointer' },

    sidebar: {
        width: '220px', minWidth: '220px', backgroundColor: '#1e222d',
        borderRight: '1px solid #2a2e39', display: 'flex', flexDirection: 'column', overflowY: 'auto',
    },
    sidebarTitle: {
        color: '#787b86', fontSize: '0.7rem', fontWeight: 'bold', letterSpacing: '0.1em',
        padding: '0.75rem 1rem 0.5rem', borderBottom: '1px solid #2a2e39', textTransform: 'uppercase',
    },
    emptyText: { color: '#787b86', fontSize: '0.8rem', padding: '1rem', textAlign: 'center' },
    watchlistItem: {
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '0.6rem 0.75rem', borderBottom: '1px solid #2a2e39', cursor: 'pointer',
    },
    watchlistItemActive: { backgroundColor: '#2a2e39' },
    watchlistLeft: { display: 'flex', flexDirection: 'column', gap: '0.15rem' },
    watchlistTicker: { color: '#d1d4dc', fontSize: '0.85rem', fontWeight: 'bold' },
    watchlistExchange: { color: '#787b86', fontSize: '0.7rem' },
    watchlistRight: { display: 'flex', alignItems: 'center', gap: '0.4rem' },
    watchlistPrice: { color: '#26a69a', fontSize: '0.78rem' },
    watchlistNoPrice: { color: '#787b86', fontSize: '0.78rem' },
    removeBtn: { backgroundColor: 'transparent', border: 'none', color: '#787b86', cursor: 'pointer', fontSize: '0.75rem', padding: '0 2px' },

    // Vertical tool strip (between sidebar and main)
    toolStrip: {
        width: '48px', minWidth: '48px',
        backgroundColor: '#1e222d',
        borderRight: '1px solid #2a2e39',
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        paddingTop: '6px', paddingBottom: '6px', gap: '2px',
        overflowY: 'auto',
    },
    toolSep: { width: '28px', height: '1px', backgroundColor: '#2a2e39', margin: '4px auto' },
    toolStripBtn: {
        width: '40px', height: '40px',
        backgroundColor: 'transparent',
        border: '1px solid transparent',
        borderRadius: '5px',
        cursor: 'pointer',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        gap: '1px', color: '#787b86',
    },
    toolStripBtnOn: { backgroundColor: '#2962ff20', color: '#2962ff', border: '1px solid #2962ff50' },
    toolIcon: { fontSize: '14px', lineHeight: 1 },
    toolLabel: { fontSize: '9px', letterSpacing: '-0.02em', lineHeight: 1 },

    main: { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' },

    header: {
        display: 'flex', alignItems: 'center', padding: '0.75rem 1.5rem',
        backgroundColor: '#1e222d', borderBottom: '1px solid #2a2e39', gap: '0.75rem', flexWrap: 'wrap',
    },
    logo: { color: '#d1d4dc', fontWeight: 'bold', fontSize: '1rem', whiteSpace: 'nowrap' },
    searchForm: { display: 'flex', gap: '0.5rem', flex: 1, maxWidth: '380px' },
    searchWrapper: { position: 'relative', flex: 1 },
    searchInput: {
        width: '100%', boxSizing: 'border-box', backgroundColor: '#2a2e39',
        border: '1px solid #363a45', borderRadius: '4px',
        padding: '0.5rem 0.75rem', color: '#d1d4dc', fontSize: '0.9rem', outline: 'none',
    },
    dropdown: {
        position: 'absolute', top: '100%', left: 0, right: 0,
        backgroundColor: '#1e222d', border: '1px solid #363a45', borderRadius: '4px',
        zIndex: 100, maxHeight: '250px', overflowY: 'auto',
    },
    dropdownItem: {
        display: 'flex', justifyContent: 'space-between',
        padding: '0.6rem 0.75rem', cursor: 'pointer', borderBottom: '1px solid #2a2e39',
    },
    dropdownTicker: { color: '#d1d4dc', fontWeight: 'bold', fontSize: '0.85rem' },
    dropdownName: { color: '#787b86', fontSize: '0.8rem' },
    searchButton: { backgroundColor: '#2962ff', color: '#fff', border: 'none', borderRadius: '4px', padding: '0.5rem 1rem', cursor: 'pointer', fontSize: '0.9rem' },
    headerBtn: {
        backgroundColor: 'transparent', color: '#787b86', border: '1px solid #363a45',
        borderRadius: '4px', padding: '0.45rem 0.75rem', cursor: 'pointer', fontSize: '0.82rem', whiteSpace: 'nowrap',
    },
    headerBtnActive: { color: '#f5c518', borderColor: '#f5c518' },
    logoutButton: { backgroundColor: 'transparent', color: '#787b86', border: '1px solid #363a45', borderRadius: '4px', padding: '0.45rem 0.75rem', cursor: 'pointer', fontSize: '0.82rem' },
    balanceDisplay: { color: '#26a69a', fontSize: '0.82rem', fontWeight: 'bold', whiteSpace: 'nowrap', padding: '0.45rem 0.75rem', border: '1px solid #26a69a40', borderRadius: '4px', backgroundColor: '#26a69a10' },
    buyBtn: { backgroundColor: '#26a69a', color: '#fff', border: 'none', borderRadius: '4px', padding: '0.45rem 0.9rem', cursor: 'pointer', fontSize: '0.82rem', fontWeight: 'bold', whiteSpace: 'nowrap' },
    sellBtn: { backgroundColor: '#ef5350', color: '#fff', border: 'none', borderRadius: '4px', padding: '0.45rem 0.9rem', cursor: 'pointer', fontSize: '0.82rem', fontWeight: 'bold', whiteSpace: 'nowrap' },
    buyModalBtn: { flex: 1, backgroundColor: '#26a69a', border: 'none', borderRadius: '4px', padding: '0.6rem', color: '#fff', cursor: 'pointer', fontWeight: 'bold' },
    sellModalBtn: { flex: 1, backgroundColor: '#ef5350', border: 'none', borderRadius: '4px', padding: '0.6rem', color: '#fff', cursor: 'pointer', fontWeight: 'bold' },

    priceBar: { display: 'flex', alignItems: 'center', gap: '1rem', padding: '0.6rem 1.5rem', backgroundColor: '#1e222d', borderBottom: '1px solid #2a2e39' },
    tradeBar: { marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.6rem' },
    tickerName: { color: '#d1d4dc', fontWeight: 'bold', fontSize: '1.1rem' },
    price: { color: '#d1d4dc', fontSize: '1.1rem' },
    change: { fontSize: '0.95rem' },
    noPrice: { color: '#787b86', fontSize: '0.85rem' },

    intervalBar: { display: 'flex', gap: '0.4rem', padding: '0.6rem 1.5rem', backgroundColor: '#131722', borderBottom: '1px solid #2a2e39' },
    intervalBtn: { backgroundColor: 'transparent', color: '#787b86', border: 'none', borderRadius: '4px', padding: '0.3rem 0.7rem', cursor: 'pointer', fontSize: '0.85rem' },
    intervalBtnActive: { backgroundColor: '#2962ff', color: '#ffffff' },

    errorMsg: { color: '#f7525f', padding: '0.5rem 1.5rem', fontSize: '0.85rem', margin: 0 },

    // Drawing options bar
    drawingBar: {
        display: 'flex', alignItems: 'center', gap: '0.75rem',
        padding: '0.4rem 1rem', backgroundColor: '#1e222d', borderBottom: '1px solid #2a2e39',
    },
    drawingBarLabel: { color: '#d1d4dc', fontSize: '0.78rem', fontWeight: 600, minWidth: '72px' },
    swatchRow: { display: 'flex', gap: '5px', alignItems: 'center' },
    swatch: { width: '16px', height: '16px', borderRadius: '50%', border: '1px solid rgba(255,255,255,0.15)', cursor: 'pointer', padding: 0 },
    widthRow: { display: 'flex', gap: '3px', alignItems: 'center' },
    widthBtn: { width: '28px', height: '24px', backgroundColor: '#131722', border: '1px solid #2a2e39', borderRadius: '3px', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' },
    widthBtnOn: { border: '1px solid #2962ff', backgroundColor: '#2962ff20' },
    drawingHint: { color: '#f5c518', fontSize: '0.75rem', marginLeft: '0.25rem' },
    escBtn: { backgroundColor: 'transparent', border: '1px solid #363a45', borderRadius: '3px', padding: '0.15rem 0.5rem', color: '#787b86', cursor: 'pointer', fontSize: '0.75rem' },

    // Drawing count badge in tool strip (informational, no click)
    drawingCountBadge: {
        width: '40px', height: '36px',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        gap: '1px', color: '#787b86',
    },

    // Selection bar (appears when a drawing IS selected)
    selBar: {
        display: 'flex', alignItems: 'center', gap: '0.75rem',
        padding: '0.35rem 1rem', backgroundColor: '#2a3a2a', borderBottom: '1px solid #26a69a40',
    },
    selDot: {
        width: '8px', height: '8px', borderRadius: '50%',
        backgroundColor: '#26a69a', flexShrink: 0,
    },
    selText: { color: '#d1d4dc', fontSize: '0.82rem', fontWeight: 500 },
    selDelete: {
        backgroundColor: '#ef535015', border: '1px solid #ef535060',
        borderRadius: '4px', padding: '0.2rem 0.65rem',
        color: '#ef5350', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 500,
    },
    selClose: {
        backgroundColor: 'transparent', border: '1px solid #363a45',
        borderRadius: '4px', padding: '0.2rem 0.55rem',
        color: '#787b86', cursor: 'pointer', fontSize: '0.78rem',
    },
    selHint: { color: '#454c55', fontSize: '0.72rem', marginLeft: 'auto' },

    // Select-mode hint bar (shown in Select mode when no drawing is selected but drawings exist)
    selectHintBar: {
        display: 'flex', alignItems: 'center', gap: '1rem',
        padding: '0.35rem 1rem', backgroundColor: '#1e222d', borderBottom: '1px solid #2a2e39',
    },
    selectHintText: { color: '#787b86', fontSize: '0.78rem', flex: 1 },
    kbd: {
        backgroundColor: '#2a2e39', border: '1px solid #363a45',
        borderRadius: '3px', padding: '0 4px', fontSize: '0.72rem', color: '#d1d4dc',
        fontFamily: 'monospace',
    },
    clearAllBtn: {
        backgroundColor: 'transparent', border: '1px solid #ef535040',
        borderRadius: '4px', padding: '0.2rem 0.65rem',
        color: '#ef535090', cursor: 'pointer', fontSize: '0.78rem',
        whiteSpace: 'nowrap',
    },

    alertsPanel: { backgroundColor: '#1e222d', borderBottom: '1px solid #2a2e39', padding: '0.75rem 1.5rem' },
    alertsPanelTitle: { color: '#787b86', fontSize: '0.75rem', fontWeight: 'bold', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '0.5rem' },
    noAlertsText: { color: '#787b86', fontWeight: 'normal' },
    alertRow: { display: 'flex', alignItems: 'center', gap: '0.75rem', padding: '0.4rem 0', borderBottom: '1px solid #2a2e39' },
    alertConditionBadge: (condition) => ({
        backgroundColor: condition === 'ABOVE' ? '#26a69a20' : '#ef535020',
        color: condition === 'ABOVE' ? '#26a69a' : '#ef5350',
        border: `1px solid ${condition === 'ABOVE' ? '#26a69a' : '#ef5350'}`,
        borderRadius: '3px', padding: '0.15rem 0.4rem', fontSize: '0.75rem', fontWeight: 'bold',
    }),
    alertPrice: { color: '#d1d4dc', fontSize: '0.85rem', flex: 1 },
    cancelAlertBtn: { backgroundColor: 'transparent', border: '1px solid #363a45', borderRadius: '3px', padding: '0.15rem 0.5rem', color: '#787b86', cursor: 'pointer', fontSize: '0.75rem' },

    chartWrapper: { flex: 1, padding: '0.5rem 1.5rem 1rem' },
    chart: { width: '100%' },

    // ─── Indicator controls ───────────────────────────────────────────────────
    intervalSep: { width: '1px', height: '16px', backgroundColor: '#2a2e39', margin: '0 4px', alignSelf: 'center' },
    indicatorMenuBtn: {
        backgroundColor: 'transparent', color: '#787b86',
        border: '1px solid #2a2e39', borderRadius: '4px',
        padding: '0.28rem 0.65rem', cursor: 'pointer', fontSize: '0.82rem',
    },
    indicatorMenuBtnOn: { color: '#d1d4dc', borderColor: '#787b86' },
    indicatorMenu: {
        position: 'absolute', top: '100%', left: 0, marginTop: '4px',
        backgroundColor: '#1e222d', border: '1px solid #363a45', borderRadius: '6px',
        padding: '8px 4px', zIndex: 200, minWidth: '150px', boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
    },
    indicatorMenuTitle: {
        color: '#454c55', fontSize: '0.68rem', fontWeight: 'bold',
        textTransform: 'uppercase', letterSpacing: '0.08em',
        padding: '2px 10px 4px',
    },
    indicatorMenuItem: {
        display: 'flex', alignItems: 'center', width: '100%',
        backgroundColor: 'transparent', border: 'none',
        padding: '6px 10px', cursor: 'pointer',
        color: '#787b86', fontSize: '0.82rem', textAlign: 'left', borderRadius: '4px',
    },
    indicatorMenuItemOn: { color: '#d1d4dc', backgroundColor: '#2a2e39' },
    checkMark: { marginLeft: 'auto', color: '#26a69a', fontSize: '0.78rem' },
    indicatorPill: {
        display: 'inline-flex', alignItems: 'center', gap: '4px',
        border: '1px solid', borderRadius: '3px',
        padding: '0.18rem 0.45rem', fontSize: '0.75rem',
    },
    indicatorPillClose: {
        background: 'none', border: 'none', cursor: 'pointer',
        color: 'inherit', padding: 0, fontSize: '0.7rem', opacity: 0.7, lineHeight: 1,
    },

    // ─── Model prediction legend ─────────────────────────────────────────────
    predictionLegend: {
        position: 'absolute', top: 8, left: 8, zIndex: 40, pointerEvents: 'none',
        maxWidth: 'min(460px, 70%)', padding: '6px 9px', borderRadius: '4px',
        backgroundColor: 'rgba(30,34,45,0.88)', border: '1px solid #2a2e39',
        fontSize: '0.75rem', lineHeight: 1.45, display: 'flex', flexDirection: 'column', gap: '2px',
    },
    predictionLegendHeadline: { fontWeight: 600, fontSize: '0.8rem' },
    predictionLegendMuted: { color: '#9598a1' },
    predictionLegendWarn: { color: '#f5c518' },

    // ─── OHLCV tooltip (adapted from tooltip plugin) ──────────────────────────
    ohlcvTooltip: {
        position: 'absolute', pointerEvents: 'none', zIndex: 50,
        backgroundColor: 'rgba(30,34,45,0.92)',
        border: '1px solid #363a45', borderRadius: '5px',
        padding: '5px 10px',
        backdropFilter: 'blur(4px)',
        boxShadow: '0 2px 8px rgba(0,0,0,0.35)',
    },
    ohlcvItem: { display: 'flex', flexDirection: 'column', alignItems: 'center' },
    ohlcvLabel: { color: '#787b86', fontSize: '0.65rem', fontWeight: 'bold', textTransform: 'uppercase', letterSpacing: '0.04em' },
    ohlcvValue: { fontSize: '0.78rem', fontWeight: 600, fontVariantNumeric: 'tabular-nums' },
};
