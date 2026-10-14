// ─── Geometry Helpers ────────────────────────────────────────────────────────

export function ptSegDist(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1;
    const lenSq = dx * dx + dy * dy;
    if (lenSq === 0) return Math.hypot(px - x1, py - y1);
    const t = Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / lenSq));
    return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

function extendToBounds(x1, y1, x2, y2, w, h) {
    const dx = x2 - x1, dy = y2 - y1;
    if (Math.abs(dx) < 0.001 && Math.abs(dy) < 0.001) return null;
    const candidates = [];
    if (Math.abs(dx) > 0.001) {
        candidates.push(-x1 / dx);
        candidates.push((w - x1) / dx);
    }
    if (Math.abs(dy) > 0.001) {
        candidates.push(-y1 / dy);
        candidates.push((h - y1) / dy);
    }
    const pts = candidates
        .map(t => ({ t, x: x1 + t * dx, y: y1 + t * dy }))
        .filter(p => p.x >= -1 && p.x <= w + 1 && p.y >= -1 && p.y <= h + 1)
        .sort((a, b) => a.t - b.t);
    if (pts.length < 2) return null;
    return { x1: pts[0].x, y1: pts[0].y, x2: pts[pts.length - 1].x, y2: pts[pts.length - 1].y };
}

// ─── Base Primitive ───────────────────────────────────────────────────────────

class BasePrimitive {
    constructor(p1, p2, opts = {}) {
        this._p1 = p1;
        this._p2 = p2;
        this._color = opts.color || '#f5c518';
        this._lineWidth = opts.lineWidth || 1.5;
        this._lineStyle = opts.lineStyle || 'solid';
        this._selected = false;
        this._preview = opts.preview || false;
        this._attachedParams = null;  // stores full SeriesAttachedParameter
        this._chart = null;
        this._series = null;
        this._views = [this._makeView()];
    }

    // Store the full param so we can call requestUpdate() for live drag repaints
    attached(param) {
        this._attachedParams = param;
        this._chart = param.chart;
        this._series = param.series;
    }
    detached() {
        this._attachedParams = null;
        this._chart = null;
        this._series = null;
    }
    updateAllViews() {}
    paneViews() { return this._views; }

    setP1(p1) { this._p1 = p1; }
    setP2(p2) { this._p2 = p2; }
    setSelected(v) { this._selected = v; }
    isSelected() { return this._selected; }
    getColor() { return this._color; }
    setColor(c) { this._color = c; }
    getP1() { return { ...this._p1 }; }
    getP2() { return { ...this._p2 }; }
    isPreview() { return this._preview; }

    // Triggers a primitive repaint — key for smooth dragging
    requestRepaint() { this._attachedParams?.requestUpdate(); }

    // Returns { x, y } CSS-pixel positions of draggable handles (used for hit-testing).
    // Two-point tools expose both endpoints; single-axis tools expose none (body-drag only).
    getHandles() {
        const x1 = this._cx1(), y1 = this._cy1();
        const x2 = this._cx2(), y2 = this._cy2();
        const handles = [];
        if (x1 != null && y1 != null) handles.push({ x: x1, y: y1, endpoint: 1 });
        if (x2 != null && y2 != null) handles.push({ x: x2, y: y2, endpoint: 2 });
        return handles;
    }

    _cx1() { return this._chart?.timeScale().timeToCoordinate(this._p1.time) ?? null; }
    _cy1() { return this._series?.priceToCoordinate(this._p1.price) ?? null; }
    _cx2() { return this._chart?.timeScale().timeToCoordinate(this._p2.time) ?? null; }
    _cy2() { return this._series?.priceToCoordinate(this._p2.price) ?? null; }

    _applyStroke(ctx, pr) {
        ctx.strokeStyle = this._selected ? '#ffffff' : this._color;
        ctx.lineWidth = this._lineWidth * pr;
        if (this._preview || this._lineStyle === 'dashed') {
            ctx.setLineDash([8 * pr, 4 * pr]);
        } else if (this._lineStyle === 'dotted') {
            ctx.setLineDash([2 * pr, 4 * pr]);
        } else {
            ctx.setLineDash([]);
        }
        ctx.globalAlpha = this._preview ? 0.55 : 1;
    }

    _handle(ctx, x, y, pr) {
        ctx.save();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
        ctx.fillStyle = '#1e222d';
        ctx.strokeStyle = this._color;
        ctx.lineWidth = 1.5 * pr;
        ctx.beginPath();
        ctx.arc(x, y, 5 * pr, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.restore();
    }

    // Must override
    _makeView() { return { zOrder: () => 'normal', renderer: () => ({ draw: () => {} }) }; }
    hitTest(_x, _y) { return false; }
}

// ─── TrendLine ────────────────────────────────────────────────────────────────

export class TrendLinePrimitive extends BasePrimitive {
    _makeView() {
        return {
            zOrder: () => 'normal',
            renderer: () => ({
                draw: (target) => {
                    const x1 = this._cx1(), y1 = this._cy1(), x2 = this._cx2(), y2 = this._cy2();
                    if (x1 == null || y1 == null || x2 == null || y2 == null) return;
                    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
                        ctx.save();
                        this._applyStroke(ctx, Math.min(hr, vr));
                        ctx.beginPath();
                        ctx.moveTo(x1 * hr, y1 * vr);
                        ctx.lineTo(x2 * hr, y2 * vr);
                        ctx.stroke();
                        if (this._selected) {
                            this._handle(ctx, x1 * hr, y1 * vr, Math.min(hr, vr));
                            this._handle(ctx, x2 * hr, y2 * vr, Math.min(hr, vr));
                        }
                        ctx.restore();
                    });
                }
            })
        };
    }

    hitTest(px, py) {
        const x1 = this._cx1(), y1 = this._cy1(), x2 = this._cx2(), y2 = this._cy2();
        if (x1 == null || y1 == null || x2 == null || y2 == null) return false;
        return ptSegDist(px, py, x1, y1, x2, y2) < 8;
    }
}

// ─── Ray ─────────────────────────────────────────────────────────────────────

export class RayPrimitive extends BasePrimitive {
    _makeView() {
        return {
            zOrder: () => 'normal',
            renderer: () => ({
                draw: (target) => {
                    const x1 = this._cx1(), y1 = this._cy1(), x2 = this._cx2(), y2 = this._cy2();
                    if (x1 == null || y1 == null || x2 == null || y2 == null) return;
                    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
                        const pr = Math.min(hr, vr);
                        const w = bitmapSize.width / hr, h = bitmapSize.height / vr;
                        // Ray goes from p1 through p2 and extends forward
                        const dx = x2 - x1, dy = y2 - y1;
                        const ts = [];
                        if (Math.abs(dx) > 0.001) {
                            const t1 = (w - x1) / dx, t0 = -x1 / dx;
                            if (dx > 0) ts.push(t1); else ts.push(t0);
                        }
                        if (Math.abs(dy) > 0.001) {
                            const t1 = (h - y1) / dy, t0 = -y1 / dy;
                            if (dy > 0) ts.push(t1); else ts.push(t0);
                        }
                        const t = ts.filter(v => v > 0).sort((a, b) => a - b)[0] ?? 1;
                        const ex = x1 + t * dx, ey = y1 + t * dy;
                        ctx.save();
                        this._applyStroke(ctx, pr);
                        ctx.beginPath();
                        ctx.moveTo(x1 * hr, y1 * vr);
                        ctx.lineTo(ex * hr, ey * vr);
                        ctx.stroke();
                        if (this._selected) this._handle(ctx, x1 * hr, y1 * vr, pr);
                        ctx.restore();
                    });
                }
            })
        };
    }

    hitTest(px, py) {
        const x1 = this._cx1(), y1 = this._cy1(), x2 = this._cx2(), y2 = this._cy2();
        if (x1 == null || y1 == null || x2 == null || y2 == null) return false;
        return ptSegDist(px, py, x1, y1, x2, y2) < 8;
    }
}

// ─── Extended Line ────────────────────────────────────────────────────────────

export class ExtendedLinePrimitive extends BasePrimitive {
    _makeView() {
        return {
            zOrder: () => 'normal',
            renderer: () => ({
                draw: (target) => {
                    const x1 = this._cx1(), y1 = this._cy1(), x2 = this._cx2(), y2 = this._cy2();
                    if (x1 == null || y1 == null || x2 == null || y2 == null) return;
                    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
                        const pr = Math.min(hr, vr);
                        const w = bitmapSize.width / hr, h = bitmapSize.height / vr;
                        const ext = extendToBounds(x1, y1, x2, y2, w, h);
                        if (!ext) return;
                        ctx.save();
                        this._applyStroke(ctx, pr);
                        ctx.beginPath();
                        ctx.moveTo(ext.x1 * hr, ext.y1 * vr);
                        ctx.lineTo(ext.x2 * hr, ext.y2 * vr);
                        ctx.stroke();
                        if (this._selected) {
                            this._handle(ctx, x1 * hr, y1 * vr, pr);
                            this._handle(ctx, x2 * hr, y2 * vr, pr);
                        }
                        ctx.restore();
                    });
                }
            })
        };
    }

    hitTest(px, py) {
        const x1 = this._cx1(), y1 = this._cy1(), x2 = this._cx2(), y2 = this._cy2();
        if (x1 == null || y1 == null || x2 == null || y2 == null) return false;
        return ptSegDist(px, py, x1, y1, x2, y2) < 8;
    }
}

// ─── Horizontal Line ─────────────────────────────────────────────────────────

export class HorizontalLinePrimitive extends BasePrimitive {
    _makeView() {
        return {
            zOrder: () => 'normal',
            renderer: () => ({
                draw: (target) => {
                    const y = this._cy1();
                    if (y == null) return;
                    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
                        const pr = Math.min(hr, vr);
                        const w = bitmapSize.width;
                        ctx.save();
                        this._applyStroke(ctx, pr);
                        ctx.beginPath();
                        ctx.moveTo(0, y * vr);
                        ctx.lineTo(w, y * vr);
                        ctx.stroke();
                        // Price label
                        ctx.setLineDash([]);
                        ctx.globalAlpha = 1;
                        ctx.fillStyle = this._selected ? '#ffffff' : this._color;
                        ctx.font = `${11 * pr}px sans-serif`;
                        ctx.fillText(`$${this._p1.price.toFixed(2)}`, 6 * hr, y * vr - 4 * vr);
                        ctx.restore();
                    });
                }
            })
        };
    }

    hitTest(px, py) {
        const y = this._cy1();
        if (y == null) return false;
        return Math.abs(py - y) < 8;
    }

    // HLine has no endpoint handles — drag the body only (Y axis)
    getHandles() { return []; }
}

// ─── Vertical Line ────────────────────────────────────────────────────────────

export class VerticalLinePrimitive extends BasePrimitive {
    _makeView() {
        return {
            zOrder: () => 'normal',
            renderer: () => ({
                draw: (target) => {
                    const x = this._cx1();
                    if (x == null) return;
                    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
                        const pr = Math.min(hr, vr);
                        ctx.save();
                        this._applyStroke(ctx, pr);
                        ctx.beginPath();
                        ctx.moveTo(x * hr, 0);
                        ctx.lineTo(x * hr, bitmapSize.height);
                        ctx.stroke();
                        ctx.restore();
                    });
                }
            })
        };
    }

    hitTest(px, py) {
        const x = this._cx1();
        if (x == null) return false;
        return Math.abs(px - x) < 8;
    }

    // VLine has no endpoint handles — drag the body only (X axis)
    getHandles() { return []; }
}

// ─── Rectangle ────────────────────────────────────────────────────────────────

export class RectanglePrimitive extends BasePrimitive {
    _makeView() {
        return {
            zOrder: () => 'normal',
            renderer: () => ({
                draw: (target) => {
                    const x1 = this._cx1(), y1 = this._cy1(), x2 = this._cx2(), y2 = this._cy2();
                    if (x1 == null || y1 == null || x2 == null || y2 == null) return;
                    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
                        const pr = Math.min(hr, vr);
                        const rx = Math.min(x1, x2) * hr;
                        const ry = Math.min(y1, y2) * vr;
                        const rw = Math.abs(x2 - x1) * hr;
                        const rh = Math.abs(y2 - y1) * vr;
                        ctx.save();
                        // Fill
                        ctx.globalAlpha = this._preview ? 0.1 : 0.12;
                        ctx.fillStyle = this._color;
                        ctx.fillRect(rx, ry, rw, rh);
                        // Border
                        this._applyStroke(ctx, pr);
                        ctx.strokeRect(rx, ry, rw, rh);
                        if (this._selected) {
                            this._handle(ctx, x1 * hr, y1 * vr, pr);
                            this._handle(ctx, x2 * hr, y2 * vr, pr);
                            this._handle(ctx, x1 * hr, y2 * vr, pr);
                            this._handle(ctx, x2 * hr, y1 * vr, pr);
                        }
                        ctx.restore();
                    });
                }
            })
        };
    }

    hitTest(px, py) {
        const x1 = this._cx1(), y1 = this._cy1(), x2 = this._cx2(), y2 = this._cy2();
        if (x1 == null || y1 == null || x2 == null || y2 == null) return false;
        const minX = Math.min(x1, x2), maxX = Math.max(x1, x2);
        const minY = Math.min(y1, y2), maxY = Math.max(y1, y2);
        // Hit border edges
        if (px >= minX - 8 && px <= maxX + 8 && py >= minY - 8 && py <= maxY + 8) {
            return (
                ptSegDist(px, py, minX, minY, maxX, minY) < 8 ||
                ptSegDist(px, py, maxX, minY, maxX, maxY) < 8 ||
                ptSegDist(px, py, maxX, maxY, minX, maxY) < 8 ||
                ptSegDist(px, py, minX, maxY, minX, minY) < 8
            );
        }
        return false;
    }
}

// ─── Fibonacci Retracement ────────────────────────────────────────────────────

const FIB_LEVELS = [
    { level: 0,     label: '0' },
    { level: 0.236, label: '0.236' },
    { level: 0.382, label: '0.382' },
    { level: 0.5,   label: '0.5' },
    { level: 0.618, label: '0.618' },
    { level: 0.786, label: '0.786' },
    { level: 1,     label: '1' },
];

const FIB_COLORS = ['#ef5350', '#ff9800', '#ffd700', '#ffffff', '#26a69a', '#2196f3', '#9c27b0'];

export class FibRetracementPrimitive extends BasePrimitive {
    _makeView() {
        return {
            zOrder: () => 'normal',
            renderer: () => ({
                draw: (target) => {
                    const x1 = this._cx1(), y1 = this._cy1(), x2 = this._cx2(), y2 = this._cy2();
                    if (x1 == null || y1 == null || x2 == null || y2 == null) return;
                    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
                        const pr = Math.min(hr, vr);
                        const leftX = Math.min(x1, x2) * hr;
                        const rightX = Math.max(x1, x2) * hr;
                        const totalW = bitmapSize.width;

                        ctx.save();
                        ctx.globalAlpha = this._preview ? 0.5 : 1;

                        FIB_LEVELS.forEach(({ level, label }, i) => {
                            const price = this._p1.price + (this._p2.price - this._p1.price) * level;
                            const y = this._series?.priceToCoordinate(price);
                            if (y == null) return;
                            const yBmp = y * vr;
                            const fibColor = FIB_COLORS[i];

                            // Fill between levels
                            if (i < FIB_LEVELS.length - 1) {
                                const nextPrice = this._p1.price + (this._p2.price - this._p1.price) * FIB_LEVELS[i + 1].level;
                                const nextY = this._series?.priceToCoordinate(nextPrice);
                                if (nextY != null) {
                                    ctx.fillStyle = fibColor;
                                    ctx.globalAlpha = this._preview ? 0.03 : 0.07;
                                    ctx.fillRect(leftX, yBmp, rightX - leftX, nextY * vr - yBmp);
                                }
                            }

                            // Horizontal line
                            ctx.globalAlpha = this._preview ? 0.5 : 1;
                            ctx.setLineDash([]);
                            ctx.strokeStyle = fibColor;
                            ctx.lineWidth = 1 * pr;
                            ctx.beginPath();
                            ctx.moveTo(leftX, yBmp);
                            ctx.lineTo(rightX, yBmp);
                            ctx.stroke();

                            // Label
                            ctx.fillStyle = fibColor;
                            ctx.font = `bold ${10 * pr}px sans-serif`;
                            ctx.fillText(`${label} ($${price.toFixed(2)})`, rightX + 4 * hr, yBmp - 3 * vr);
                        });

                        // Vertical boundary lines
                        ctx.setLineDash([4 * pr, 3 * pr]);
                        ctx.strokeStyle = this._color;
                        ctx.lineWidth = 1 * pr;
                        ctx.globalAlpha = 0.4;
                        ctx.beginPath();
                        ctx.moveTo(x1 * hr, 0); ctx.lineTo(x1 * hr, bitmapSize.height);
                        ctx.stroke();
                        ctx.beginPath();
                        ctx.moveTo(x2 * hr, 0); ctx.lineTo(x2 * hr, bitmapSize.height);
                        ctx.stroke();

                        if (this._selected) {
                            this._handle(ctx, x1 * hr, y1 * vr, pr);
                            this._handle(ctx, x2 * hr, y2 * vr, pr);
                        }

                        ctx.restore();
                    });
                }
            })
        };
    }

    hitTest(px, py) {
        const x1 = this._cx1(), x2 = this._cx2();
        if (x1 == null || x2 == null) return false;
        const minX = Math.min(x1, x2), maxX = Math.max(x1, x2);
        if (px < minX - 8 || px > maxX + 8) return false;

        for (const { level } of FIB_LEVELS) {
            const price = this._p1.price + (this._p2.price - this._p1.price) * level;
            const y = this._series?.priceToCoordinate(price);
            if (y != null && Math.abs(py - y) < 8) return true;
        }
        return false;
    }
}

// ─── Factory ──────────────────────────────────────────────────────────────────

export function createPrimitive(toolType, p1, p2, opts = {}) {
    switch (toolType) {
        case 'trendline':  return new TrendLinePrimitive(p1, p2, opts);
        case 'ray':        return new RayPrimitive(p1, p2, opts);
        case 'extline':    return new ExtendedLinePrimitive(p1, p2, opts);
        case 'hline':      return new HorizontalLinePrimitive(p1, p2, opts);
        case 'vline':      return new VerticalLinePrimitive(p1, p2, opts);
        case 'rectangle':  return new RectanglePrimitive(p1, p2, opts);
        case 'fibonacci':  return new FibRetracementPrimitive(p1, p2, opts);
        default:           return null;
    }
}
