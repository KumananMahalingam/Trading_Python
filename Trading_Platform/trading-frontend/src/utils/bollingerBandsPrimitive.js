// Ported from lightweight-charts/plugin-examples/src/plugins/bands-indicator
// Draws Bollinger Bands as a filled region primitive attached to the candle series.
// fill is rendered via drawBackground (behind candles), lines via draw (in front).

class BBRenderer {
    constructor(source) {
        this._source = source;
    }

    // Convert stored time/price data to canvas coordinates at draw time
    _getPoints() {
        const params = this._source._attachedParams;
        if (!params || this._source._bands.length < 2) return null;
        const ts = params.chart.timeScale();
        const series = params.series;
        const points = this._source._bands.map(b => ({
            x:     ts.timeToCoordinate(b.time)       ?? -9999,
            upper: series.priceToCoordinate(b.upper)  ?? -9999,
            mid:   series.priceToCoordinate(b.mid)    ?? -9999,
            lower: series.priceToCoordinate(b.lower)  ?? -9999,
        })).filter(p => p.x > -9000);
        return points.length < 2 ? null : points;
    }

    // Semi-transparent fill behind candles
    drawBackground(target) {
        const points = this._getPoints();
        if (!points) return;
        target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
            ctx.save();
            ctx.scale(hr, vr);

            const region = new Path2D();
            region.moveTo(points[0].x, points[0].upper);
            for (const p of points) region.lineTo(p.x, p.upper);
            const last = points[points.length - 1];
            region.lineTo(last.x, last.lower);
            for (let i = points.length - 2; i >= 0; i--) region.lineTo(points[i].x, points[i].lower);
            region.closePath();

            ctx.fillStyle = this._source._options.fillColor;
            ctx.fill(region);
            ctx.restore();
        });
    }

    // Upper, mid, lower lines on top of candles
    draw(target) {
        const points = this._getPoints();
        if (!points) return;
        const opts = this._source._options;

        target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
            ctx.save();
            ctx.scale(hr, vr);

            const drawLine = (getY, dashed = false) => {
                ctx.beginPath();
                if (dashed) ctx.setLineDash([4, 3]);
                ctx.moveTo(points[0].x, getY(points[0]));
                for (const p of points) ctx.lineTo(p.x, getY(p));
                ctx.stroke();
                if (dashed) ctx.setLineDash([]);
            };

            // Outer band lines
            ctx.strokeStyle = opts.lineColor;
            ctx.lineWidth = opts.lineWidth;
            drawLine(p => p.upper);
            drawLine(p => p.lower);

            // Mid (basis) line — dashed, slightly more transparent
            ctx.strokeStyle = opts.midColor;
            ctx.lineWidth = opts.lineWidth;
            drawLine(p => p.mid, true);

            ctx.restore();
        });
    }
}

class BBPaneView {
    constructor(source) {
        this._source = source;
        this._renderer = new BBRenderer(source);
    }
    renderer() { return this._renderer; }
    // Default zOrder='normal': drawBackground fires before candles, draw fires after
}

export class BollingerBandsPrimitive {
    constructor(options = {}) {
        this._bands = [];
        this._attachedParams = null;
        this._options = {
            lineColor:  'rgba(156, 39, 176, 0.85)',
            midColor:   'rgba(156, 39, 176, 0.45)',
            fillColor:  'rgba(156, 39, 176, 0.07)',
            lineWidth:  1,
            ...options,
        };
        this._views = [new BBPaneView(this)];
    }

    attached(param) {
        this._attachedParams = param;
    }

    detached() {
        this._attachedParams = null;
    }

    paneViews() { return this._views; }
    updateAllViews() {}

    setData(bands) {
        this._bands = bands;
        this._attachedParams?.requestUpdate();
    }
}
