import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { createChart, CrosshairMode, HistogramSeries, LineSeries } from 'lightweight-charts';
import {
    Bell, BellRing, Check, Layers, Minus, MousePointer2, MoveHorizontal, MoveUpRight, Newspaper,
    Percent, Plus, SeparatorVertical, Slash, Square, Star, Trash2, X,
} from 'lucide-react';
import { createPrimitive } from '../utils/drawingTools';
import { CrosshairHighlightPrimitive } from '../utils/crosshairHighlight';
import { calcSMA, calcEMA, calcBollingerBands } from '../utils/indicators';
import { RoundedCandleSeriesView } from '../utils/roundedCandleSeries';
import { BollingerBandsPrimitive } from '../utils/bollingerBandsPrimitive';
import { Client } from '@stomp/stompjs';
import SockJS from 'sockjs-client';
import { useAuth } from '../context/AuthContext';
import { getCandles } from '../api/candleApi';
import { getWatchlist, addToWatchlist, removeFromWatchlist } from '../api/watchlistApi';
import { getAlerts, createAlert, cancelAlert, deleteAlert } from '../api/alertApi';
import { getMyProfile } from '../api/userApi';
import { buyStock, sellStock } from '../api/tradeApi';
import { getPortfolio } from '../api/portfolioApi';
import { getPrediction } from '../api/predictionApi';
import { buildPredictionOverlay, describePrediction, formatPct } from '../utils/predictionOverlay';
import { PredictionOverlayRenderer, PREDICTION_COLOR } from '../utils/predictionOverlayRenderer';
import { DEFAULT_TICKER, LAST_TICKER_KEY, SHOW_PREDICTION_KEY, readPreference, rememberTicker, writePreference } from '../utils/preferences';
import { useSymbols } from '../hooks/useSymbols';
import AppShell from '../components/AppShell';
import ChangePill from '../components/ChangePill';
import Modal from '../components/Modal';
import NewsFeed from '../components/NewsFeed';
import PredictionPanel from '../components/PredictionPanel';
import TickerAvatar from '../components/TickerAvatar';

// Model predictions are daily, so the overlay is only offered on this interval.
const PREDICTION_INTERVAL = '1D';

// Chart palette, matching styles/theme.css
const CHART = {
    bg: '#111113',
    text: '#a1a1aa',
    grid: '#18181b',
    border: '#2c2c31',
    crosshair: '#52525b',
    label: '#27272a',
    up: '#22c55e',
    down: '#ef4444',
    upVolume: 'rgba(34, 197, 94, 0.28)',
    downVolume: 'rgba(239, 68, 68, 0.28)',
    priceLine: '#a1a1aa',
    font: "'Geist Mono Variable', ui-monospace, Menlo, Consolas, monospace",
};

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
    { id: 'select',    Icon: MousePointer2,     label: 'Select',     group: 0, clicks: 0 },
    { id: 'trendline', Icon: Slash,             label: 'Trend Line', group: 1, clicks: 2 },
    { id: 'ray',       Icon: MoveUpRight,       label: 'Ray',        group: 1, clicks: 2 },
    { id: 'extline',   Icon: MoveHorizontal,    label: 'Ext. Line',  group: 1, clicks: 2 },
    { id: 'hline',     Icon: Minus,             label: 'H. Line',    group: 2, clicks: 1 },
    { id: 'vline',     Icon: SeparatorVertical, label: 'V. Line',    group: 2, clicks: 1 },
    { id: 'rectangle', Icon: Square,            label: 'Rectangle',  group: 3, clicks: 2 },
    { id: 'fibonacci', Icon: Percent,           label: 'Fibonacci',  group: 3, clicks: 2 },
];

const DRAW_COLORS = ['#f5c518', '#3b82f6', '#22c55e', '#ef4444', '#ededef'];

const fmtMoney = (n) => Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const DRAW_WIDTHS = [1, 1.5, 2.5];

export default function ChartPage() {
    const { accessToken } = useAuth();
    const symbols = useSymbols();

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
    const [showPrediction, setShowPredictionState] = useState(() => readPreference(SHOW_PREDICTION_KEY, '0') === '1');
    const setShowPrediction = (next) => {
        setShowPredictionState(next);
        writePreference(SHOW_PREDICTION_KEY, next ? '1' : '0');
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
    const [ticker, setTicker] = useState(() => readPreference(LAST_TICKER_KEY, '') || DEFAULT_TICKER);
    useEffect(() => { rememberTicker(ticker); }, [ticker]);
    const [interval, setInterval] = useState('1D');
    const [livePrice, setLivePrice] = useState(null);
    const [priceChange, setPriceChange] = useState(null);
    const [priceChangePercent, setPriceChangePercent] = useState(null);
    const [error, setError] = useState('');

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
        // autoSize follows the container (ResizeObserver), so the chart reflows
        // when side panels open or the window resizes.
        const chart = createChart(chartContainerRef.current, {
            autoSize: true,
            layout: {
                background: { type: 'solid', color: CHART.bg },
                textColor: CHART.text,
                fontFamily: CHART.font,
                fontSize: 11,
            },
            grid: { vertLines: { color: CHART.grid }, horzLines: { color: CHART.grid } },
            crosshair: {
                mode: CrosshairMode.MagnetOHLC,
                vertLine: { color: CHART.crosshair, labelBackgroundColor: CHART.label },
                horzLine: { color: CHART.crosshair, labelBackgroundColor: CHART.label },
            },
            rightPriceScale: { borderColor: CHART.border },
            timeScale: { borderColor: CHART.border, timeVisible: true },
        });

        // Rounded candles — ported from plugin-examples/rounded-candles-series
        const series = chart.addCustomSeries(new RoundedCandleSeriesView(), {
            upColor: CHART.up, downColor: CHART.down, wickUpColor: CHART.up, wickDownColor: CHART.down,
            priceLineColor: CHART.crosshair,
        });

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
            color: CHART.priceLine,
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
                    color: c.close >= c.open ? CHART.upVolume : CHART.downVolume,
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
                    volumeSeriesRef.current.update({ time: data.timestamp, value: parseFloat(data.volume), color: close >= open ? CHART.upVolume : CHART.downVolume });
                });
            },
            reconnectDelay: 5000,
        });
        client.activate();
        stompClientRef.current = client;
        return () => client.deactivate();
    }, [ticker, interval]);

    // ─── Search ───────────────────────────────────────────────────────────────

    const handleSymbolSelect = (symbol) => {
        setTicker(symbol);
        setInterval('1D');
    };

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

    const selectedDrawing = drawings.find(d => d.id === selectedId);
    const symbolInfo = symbols.find(s => s.ticker === upperTicker);
    const activeAlertCount = alerts.filter(a => a.status === 'ACTIVE').length;
    const triggeredAlerts = alerts.filter(a => a.status === 'TRIGGERED');
    const indicatorCount = activeIndicators.size + (predictionEnabled ? 1 : 0);

    // ─── Render ───────────────────────────────────────────────────────────────

    const watchlistSection = (
        <section className="nav-section" aria-labelledby="watchlist-label">
            <div className="nav-label">
                <span id="watchlist-label">Watchlist</span>
                <button
                    type="button"
                    className="icon-btn"
                    onClick={handleAddToWatchlist}
                    disabled={isInWatchlist}
                    aria-label={isInWatchlist ? `${upperTicker} is in your watchlist` : `Add ${upperTicker} to watchlist`}
                    title={isInWatchlist ? 'Already in watchlist' : `Add ${upperTicker}`}
                >
                    <Plus size={14} aria-hidden="true" />
                </button>
            </div>
            {watchlist.length === 0 ? (
                <p className="nav-empty">No symbols yet. Use + to add the current one.</p>
            ) : (
                <ul style={{ listStyle: 'none' }}>
                    {watchlist.map(w => (
                        <li key={w.ticker} className={`watch-item${w.ticker === upperTicker ? ' active' : ''}`}>
                            <button
                                type="button"
                                className="watch-open"
                                onClick={() => setTicker(w.ticker)}
                                aria-current={w.ticker === upperTicker ? 'true' : undefined}
                            >
                                <TickerAvatar ticker={w.ticker} />
                                <span className="mono">{w.ticker}</span>
                                <span className="price mono">
                                    {watchlistPrices[w.ticker] ? `$${watchlistPrices[w.ticker].toLocaleString()}` : (w.exchange || '')}
                                </span>
                            </button>
                            <button
                                type="button"
                                className="remove"
                                onClick={() => handleRemoveFromWatchlist(w.ticker)}
                                aria-label={`Remove ${w.ticker} from watchlist`}
                            >
                                <X size={12} aria-hidden="true" />
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </section>
    );

    const topbarActions = (
        <>
            <button
                type="button"
                className={`btn btn-ghost btn-sm${isInWatchlist ? ' on' : ''}`}
                onClick={isInWatchlist ? () => handleRemoveFromWatchlist(upperTicker) : handleAddToWatchlist}
                aria-pressed={isInWatchlist}
            >
                <Star size={14} aria-hidden="true" fill={isInWatchlist ? 'currentColor' : 'none'} />
                {isInWatchlist ? 'Watching' : 'Watch'}
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={openAlertModal}>
                <Bell size={14} aria-hidden="true" /> Set alert
            </button>
            <button
                type="button"
                className={`btn btn-ghost btn-sm${showAlerts ? ' on' : ''}`}
                onClick={() => setShowAlerts(v => !v)}
                aria-pressed={showAlerts}
            >
                Alerts {activeAlertCount > 0 && <span className="count mono">{activeAlertCount}</span>}
            </button>
            <button
                type="button"
                className={`btn btn-ghost btn-sm${showNews ? ' on' : ''}`}
                onClick={() => setShowNews(v => !v)}
                aria-pressed={showNews}
            >
                <Newspaper size={14} aria-hidden="true" /> News
            </button>
        </>
    );

    const changePct = isCrypto && livePrice && Number.isFinite(priceChangePercent) ? priceChangePercent : null;

    return (
        <AppShell
            breadcrumb={['Markets', upperTicker]}
            actions={topbarActions}
            sidebarContent={watchlistSection}
            onSymbolSelect={handleSymbolSelect}
            aside={showNews ? <NewsFeed ticker={upperTicker} onClose={() => setShowNews(false)} /> : null}
        >
            {/* Alert notification */}
            {alertNotification && (
                <div className="toast" role="status">
                    <BellRing size={16} aria-hidden="true" style={{ color: 'var(--warn)' }} />
                    <span>
                        Alert triggered: <strong className="mono">{alertNotification.ticker}</strong> is{' '}
                        {alertNotification.condition === 'ABOVE' ? 'above' : 'below'}{' '}
                        <span className="mono">${parseFloat(alertNotification.targetPrice).toLocaleString()}</span>
                    </span>
                    <button type="button" className="icon-btn" onClick={() => setAlertNotification(null)} aria-label="Dismiss alert">
                        <X size={14} aria-hidden="true" />
                    </button>
                </div>
            )}

            {/* Alert modal */}
            {showAlertModal && (
                <Modal title={<>Set price alert · <span className="mono">{upperTicker}</span></>} onClose={() => setShowAlertModal(false)}>
                    <form onSubmit={handleCreateAlert}>
                        <label className="field">
                            <span className="field-label">Condition</span>
                            <select className="select" value={alertCondition} onChange={e => setAlertCondition(e.target.value)}>
                                <option value="ABOVE">Price goes above</option>
                                <option value="BELOW">Price goes below</option>
                            </select>
                        </label>
                        <label className="field">
                            <span className="field-label">Target price (USD)</span>
                            <input
                                className="input mono" type="number" step="any" value={alertTargetPrice}
                                onChange={e => setAlertTargetPrice(e.target.value)}
                                placeholder="Enter price..." required
                            />
                        </label>
                        {alertError && <p className="form-error">{alertError}</p>}
                        <div className="modal-actions">
                            <button type="button" className="btn" onClick={() => setShowAlertModal(false)}>Cancel</button>
                            <button type="submit" className="btn btn-primary">Create alert</button>
                        </div>
                    </form>
                </Modal>
            )}

            {/* Trade modal */}
            {showTradeModal && (
                <Modal
                    title={<>{tradeType === 'BUY' ? 'Buy' : 'Sell'} <span className="mono">{upperTicker}</span></>}
                    titleClassName={tradeType === 'BUY' ? 'up' : 'down'}
                    onClose={() => setShowTradeModal(false)}
                >
                    <form onSubmit={handleTrade}>
                        <div className="field">
                            <span className="field-label">Current price</span>
                            <div className="input input-static mono">${livePrice ? livePrice.toLocaleString() : '—'}</div>
                        </div>
                        <label className="field">
                            <span className="field-label">Quantity</span>
                            <input
                                className="input mono" type="number" step="any" min="0"
                                value={tradeQuantity}
                                onChange={e => setTradeQuantity(e.target.value)}
                                placeholder="e.g. 0.01" required autoFocus
                            />
                        </label>
                        {tradeQuantity && livePrice && (
                            <div className="field">
                                <span className="field-label">Total</span>
                                <span className="mono" style={{ fontSize: 16 }}>${fmtMoney(parseFloat(tradeQuantity || 0) * livePrice)}</span>
                            </div>
                        )}
                        {tradeType === 'BUY' && balance !== null && (
                            <p className="note">Available: <span className="mono">${fmtMoney(balance)}</span></p>
                        )}
                        {tradeError && <p className="form-error">{tradeError}</p>}
                        <div className="modal-actions">
                            <button type="button" className="btn" onClick={() => setShowTradeModal(false)}>Cancel</button>
                            <button type="submit" className={`btn ${tradeType === 'BUY' ? 'btn-up' : 'btn-down'}`}>
                                {tradeType === 'BUY' ? 'Buy' : 'Sell'}
                            </button>
                        </div>
                    </form>
                </Modal>
            )}

            {/* Symbol header */}
            <div className="symbol-header">
                <div>
                    <p className="eyebrow">
                        {[symbolInfo?.exchange || (isCrypto ? 'BINANCE' : null), symbolInfo?.name, isCrypto ? 'Crypto' : 'Stock']
                            .filter(Boolean).join(' · ')}
                    </p>
                    <div className="symbol-title">
                        <TickerAvatar ticker={upperTicker} size={30} />
                        <h1 className="mono">{upperTicker}</h1>
                        {livePrice ? (
                            <>
                                <span className="symbol-price mono">${livePrice.toLocaleString()}</span>
                                {changePct !== null && <ChangePill value={changePct} label="24h change" />}
                                {changePct !== null && Number.isFinite(priceChange) && (
                                    <span className={`mono ${priceChange >= 0 ? 'up' : 'down'}`} style={{ fontSize: 13 }}>
                                        {priceChange >= 0 ? '+' : '−'}${Math.abs(priceChange).toLocaleString('en-US', { maximumFractionDigits: 2 })}
                                    </span>
                                )}
                            </>
                        ) : (
                            <span className="muted">Waiting for live price…</span>
                        )}
                    </div>
                </div>
                <div className="trade-actions">
                    <div className="balance">
                        <span className="label">Cash balance</span>
                        <span className="value mono">${balance !== null ? fmtMoney(balance) : '…'}</span>
                    </div>
                    <button type="button" className="btn btn-up btn-lg" onClick={() => openTradeModal('BUY')}>Buy</button>
                    <button type="button" className="btn btn-down btn-lg" onClick={() => openTradeModal('SELL')}>Sell</button>
                </div>
            </div>

            {watchlistError && <p className="banner-error">{watchlistError}</p>}
            {error && <p className="banner-error">{error}</p>}

            {/* Alerts */}
            {showAlerts && (
                <section className="card" aria-labelledby="alerts-title">
                    <div className="card-header">
                        <div>
                            <h2 id="alerts-title" className="card-title">Alerts</h2>
                            <p className="card-subtitle">Active price alerts for <span className="mono">{upperTicker}</span></p>
                        </div>
                        <button type="button" className="btn btn-sm" onClick={openAlertModal}><Plus size={14} aria-hidden="true" /> New alert</button>
                    </div>
                    <div className="card-body">
                        {tickerAlerts.length === 0 && <p className="muted">No active alerts for {upperTicker}.</p>}
                        {tickerAlerts.map(a => (
                            <div key={a.id} className="alert-row">
                                <span className={`pill ${a.condition === 'ABOVE' ? 'pill-up' : 'pill-down'}`}>
                                    {a.condition === 'ABOVE' ? '▲ Above' : '▼ Below'}
                                </span>
                                <span className="grow mono">${parseFloat(a.targetPrice).toLocaleString()}</span>
                                <button type="button" className="btn btn-sm" onClick={() => handleCancelAlert(a.id)}>Cancel</button>
                            </div>
                        ))}
                        {triggeredAlerts.length > 0 && (
                            <>
                                <p className="section-label">Triggered</p>
                                {triggeredAlerts.map(a => (
                                    <div key={a.id} className="alert-row">
                                        <span className={`pill ${a.condition === 'ABOVE' ? 'pill-up' : 'pill-down'}`}>
                                            {a.condition === 'ABOVE' ? '▲ Above' : '▼ Below'}
                                        </span>
                                        <span className="grow mono">{a.ticker} @ ${parseFloat(a.targetPrice).toLocaleString()}</span>
                                        <span className="pill pill-neutral"><Check size={12} aria-hidden="true" /> Triggered</span>
                                        <button type="button" className="btn btn-sm btn-ghost" onClick={() => handleDeleteAlert(a.id)}>Delete</button>
                                    </div>
                                ))}
                            </>
                        )}
                    </div>
                </section>
            )}

            {/* Chart */}
            <section className="card chart-card" aria-label={`${upperTicker} chart`}>
                <div className="chart-toolbar">
                    <div className="segmented" role="group" aria-label="Interval">
                        {INTERVALS.map(i => (
                            <button key={i} type="button" onClick={() => setInterval(i)} aria-pressed={interval === i}>{i}</button>
                        ))}
                    </div>

                    <span className="toolbar-sep" aria-hidden="true" />

                    <div style={{ position: 'relative' }}>
                        <button
                            type="button"
                            className={`btn btn-sm${showIndicatorMenu ? ' on' : ''}`}
                            onClick={() => setShowIndicatorMenu(v => !v)}
                            aria-expanded={showIndicatorMenu}
                        >
                            <Layers size={14} aria-hidden="true" /> Indicators
                            {indicatorCount > 0 && <span className="count mono">{indicatorCount}</span>}
                        </button>

                        {showIndicatorMenu && (
                            <div className="menu">
                                <div className="menu-label">Moving averages</div>
                                {INDICATOR_DEFS.filter(d => d.group === 'MA').map(def => (
                                    <button key={def.id} type="button" className="menu-item" onClick={() => toggleIndicator(def.id)} aria-pressed={activeIndicators.has(def.id)}>
                                        <span className="dot" style={{ background: def.color }} aria-hidden="true" />
                                        {def.label}
                                        {activeIndicators.has(def.id) && <Check size={14} className="check" aria-hidden="true" />}
                                    </button>
                                ))}
                                <div className="menu-label">Bands</div>
                                {INDICATOR_DEFS.filter(d => d.group === 'BB').map(def => (
                                    <button key={def.id} type="button" className="menu-item" onClick={() => toggleIndicator(def.id)} aria-pressed={activeIndicators.has(def.id)}>
                                        <span className="dot" style={{ background: def.color }} aria-hidden="true" />
                                        {def.label}
                                        {activeIndicators.has(def.id) && <Check size={14} className="check" aria-hidden="true" />}
                                    </button>
                                ))}
                                {interval === PREDICTION_INTERVAL && (
                                    <>
                                        <div className="menu-label">Model</div>
                                        <button type="button" className="menu-item" onClick={() => setShowPrediction(!showPrediction)} aria-pressed={showPrediction}>
                                            <span className="dot" style={{ background: PREDICTION_COLOR }} aria-hidden="true" />
                                            Model prediction
                                            {showPrediction && <Check size={14} className="check" aria-hidden="true" />}
                                        </button>
                                    </>
                                )}
                            </div>
                        )}
                    </div>

                    {INDICATOR_DEFS.filter(d => activeIndicators.has(d.id)).map(def => (
                        <span key={def.id} className="chip">
                            <span className="dot" style={{ background: def.color }} aria-hidden="true" />
                            {def.label}
                            <button type="button" onClick={() => toggleIndicator(def.id)} aria-label={`Remove ${def.label}`}><X size={12} aria-hidden="true" /></button>
                        </span>
                    ))}
                    {predictionEnabled && (
                        <span className="chip">
                            <span className="dot" style={{ background: PREDICTION_COLOR }} aria-hidden="true" />
                            Model
                            <button type="button" onClick={() => setShowPrediction(false)} aria-label="Hide model prediction"><X size={12} aria-hidden="true" /></button>
                        </span>
                    )}
                </div>

                {/* Drawing options — while a drawing tool is active */}
                {activeTool !== 'select' && (
                    <div className="subbar">
                        <strong style={{ color: 'var(--text)', fontWeight: 500 }}>{DRAWING_TOOLS.find(t => t.id === activeTool)?.label}</strong>
                        <div className="button-row" role="group" aria-label="Line colour">
                            {DRAW_COLORS.map(c => (
                                <button key={c} type="button" className="swatch" onClick={() => changeColor(c)}
                                    aria-label={`Colour ${c}`} aria-pressed={activeColor === c} style={{ background: c }} />
                            ))}
                        </div>
                        <div className="button-row" role="group" aria-label="Line width" style={{ gap: 4 }}>
                            {DRAW_WIDTHS.map(w => (
                                <button key={w} type="button" className="width-btn" onClick={() => changeWidth(w)}
                                    aria-label={`Width ${w}`} aria-pressed={activeWidth === w}>
                                    <span style={{ width: 16, height: Math.max(1, Math.round(w)), background: activeColor, borderRadius: 1 }} />
                                </button>
                            ))}
                        </div>
                        <span className={awaitingSecondClick ? 'hint-warn' : 'hint'}>
                            {awaitingSecondClick ? 'Click the second point to finish' : 'Click on the chart to start'}
                        </span>
                        {awaitingSecondClick && (
                            <button
                                type="button"
                                className="btn btn-sm spacer"
                                onClick={() => {
                                    if (previewPrimRef.current && seriesRef.current) {
                                        seriesRef.current.detachPrimitive(previewPrimRef.current);
                                        previewPrimRef.current = null;
                                    }
                                    drawingPointRef.current = null;
                                    setAwaitingSecondClick(false);
                                }}
                            >
                                Cancel <span className="kbd">Esc</span>
                            </button>
                        )}
                    </div>
                )}

                {/* A drawing is selected */}
                {selectedId != null && activeTool === 'select' && selectedDrawing && (
                    <div className="subbar">
                        <span className="dot" style={{ background: 'var(--up)' }} aria-hidden="true" />
                        <span style={{ color: 'var(--text)' }}>{selectedDrawing.label} selected</span>
                        <button type="button" className="btn btn-sm btn-danger-ghost" onClick={() => deleteDrawingById(selectedId)}>
                            <Trash2 size={13} aria-hidden="true" /> Delete
                        </button>
                        <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => {
                                drawingsRef.current.forEach(d => d.primitive.setSelected(false));
                                selectedIdRef.current = null;
                                setSelectedId(null);
                            }}
                        >
                            Deselect
                        </button>
                        <span className="hint spacer">or press <span className="kbd">Delete</span></span>
                    </div>
                )}

                {/* Select mode with drawings on the chart */}
                {activeTool === 'select' && selectedId == null && drawings.length > 0 && (
                    <div className="subbar">
                        <span className="hint">Click a drawing to select it, then press <span className="kbd">Delete</span>.</span>
                        <button type="button" className="btn btn-sm btn-danger-ghost spacer" onClick={clearAllDrawings}>
                            <Trash2 size={13} aria-hidden="true" /> Clear all ({drawings.length})
                        </button>
                    </div>
                )}

                <div className="chart-body">
                    <div className="tool-rail" role="toolbar" aria-label="Drawing tools" aria-orientation="vertical">
                        {DRAWING_TOOLS.map((tool, i) => {
                            const prev = i > 0 ? DRAWING_TOOLS[i - 1] : null;
                            const { Icon } = tool;
                            return (
                                <div key={tool.id} style={{ display: 'contents' }}>
                                    {prev && prev.group !== tool.group && <span className="rail-sep" aria-hidden="true" />}
                                    <button
                                        type="button"
                                        className="tool"
                                        title={tool.label}
                                        aria-label={tool.label}
                                        aria-pressed={activeTool === tool.id}
                                        onClick={() => selectTool(tool.id)}
                                    >
                                        <Icon size={16} aria-hidden="true" />
                                    </button>
                                </div>
                            );
                        })}
                        {drawings.length > 0 && (
                            <span className="rail-count mono" title={`${drawings.length} drawing${drawings.length > 1 ? 's' : ''} on chart`}>
                                {drawings.length}
                            </span>
                        )}
                    </div>

                    <div className="chart-area">
                        <div
                            ref={chartContainerRef}
                            className="chart-canvas"
                            style={{
                                cursor: activeTool !== 'select'
                                    ? 'crosshair'
                                    : isDragging ? 'grabbing' : selectedId != null ? 'grab' : 'default',
                            }}
                        />

                        {/* Model prediction legend */}
                        {predictionEnabled && (
                            <div className="chart-overlay prediction-legend" role="status" aria-live="polite">
                                {currentPredictionError ? (
                                    <span>{currentPredictionError}</span>
                                ) : !currentPrediction ? (
                                    <span>Loading model prediction…</span>
                                ) : currentPrediction.status !== 'ready' ? (
                                    <span>No model trained for {upperTicker} yet.</span>
                                ) : (
                                    <>
                                        {currentPrediction.prediction && (
                                            <div className={`headline ${currentPrediction.prediction.direction === 'UP' ? 'up' : currentPrediction.prediction.direction === 'DOWN' ? 'down' : ''}`}>
                                                <span className="dot" style={{ background: PREDICTION_COLOR }} aria-hidden="true" />
                                                Model: {describePrediction(currentPrediction.prediction)}
                                                <span className="muted"> · as of {currentPrediction.prediction.asOfDate} close</span>
                                            </div>
                                        )}
                                        {predictionOverlay?.stale && (
                                            <div className="warn">Prediction is out of date; refresh it to update.</div>
                                        )}
                                        {currentPrediction.backtest?.metrics && (
                                            <div>
                                                Backtest {currentPrediction.backtest.start} – {currentPrediction.backtest.end}:{' '}
                                                <span className="mono">{formatPct(currentPrediction.backtest.metrics.directionalAccuracy)}</span> direction accuracy
                                                {' '}(always-majority baseline <span className="mono">{formatPct(currentPrediction.backtest.metrics.majorityBaseline)}</span>,
                                                n={currentPrediction.backtest.metrics.n})
                                                {!predictionOverlay?.backtestLine.length && ' · outside the loaded candles'}
                                            </div>
                                        )}
                                        {!currentPrediction.hasNews && (
                                            <div>No news coverage: price and technical features only.</div>
                                        )}
                                        <div className="muted">Research model, not trading advice.</div>
                                    </>
                                )}
                            </div>
                        )}

                        {/* OHLC tooltip */}
                        {tooltipData && (
                            <div
                                className="chart-overlay ohlc-tooltip"
                                style={{
                                    left: tooltipPos.x < 200 ? tooltipPos.x + 60 : tooltipPos.x - 100,
                                    top: Math.max(8, tooltipPos.y - 64),
                                }}
                            >
                                {[
                                    { label: 'O', value: tooltipData.open },
                                    { label: 'H', value: tooltipData.high },
                                    { label: 'L', value: tooltipData.low },
                                    { label: 'C', value: tooltipData.close },
                                ].map(({ label, value }) => (
                                    <div key={label} className="item">
                                        <span className="k">{label}</span>
                                        <span className={`v mono ${tooltipData.isUp ? 'up' : 'down'}`}>{value}</span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            </section>

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
        </AppShell>
    );
}
