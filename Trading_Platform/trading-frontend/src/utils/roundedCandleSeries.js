// Ported from lightweight-charts/plugin-examples/src/plugins/rounded-candles-series
import { customSeriesDefaultOptions } from 'lightweight-charts';

// ── Dimension helpers (from plugin-examples/src/helpers/dimensions) ──────────

function positionsLine(positionMedia, pixelRatio, desiredWidthMedia = 1) {
    const scaledPosition = Math.round(pixelRatio * positionMedia);
    const lineBitmapWidth = Math.round(desiredWidthMedia * pixelRatio);
    const offset = Math.floor(lineBitmapWidth * 0.5);
    return { position: scaledPosition - offset, length: lineBitmapWidth };
}

function positionsBox(position1Media, position2Media, pixelRatio) {
    const scaledPosition1 = Math.round(pixelRatio * position1Media);
    const scaledPosition2 = Math.round(pixelRatio * position2Media);
    return {
        position: Math.min(scaledPosition1, scaledPosition2),
        length: Math.abs(scaledPosition2 - scaledPosition1) + 1,
    };
}

function optimalCandlestickWidth(barSpacing, pixelRatio) {
    const specialCaseFrom = 2.5, specialCaseTo = 4;
    if (barSpacing >= specialCaseFrom && barSpacing <= specialCaseTo) {
        return Math.floor(3 * pixelRatio);
    }
    const coeff = 1 - (0.2 * Math.atan(Math.max(specialCaseTo, barSpacing) - specialCaseTo)) / (Math.PI * 0.5);
    const res = Math.floor(barSpacing * coeff * pixelRatio);
    const scaledBarSpacing = Math.floor(barSpacing * pixelRatio);
    return Math.max(Math.floor(pixelRatio), Math.min(res, scaledBarSpacing));
}

function candlestickWidth(barSpacing, horizontalPixelRatio) {
    let width = optimalCandlestickWidth(barSpacing, horizontalPixelRatio);
    if (width >= 2) {
        const wickWidth = Math.floor(horizontalPixelRatio);
        if (wickWidth % 2 !== width % 2) width--;
    }
    return width;
}

// ── Renderer ──────────────────────────────────────────────────────────────────

class RoundedCandleRenderer {
    constructor() {
        this._data = null;
        this._options = null;
    }

    update(data, options) {
        this._data = data;
        this._options = options;
    }

    draw(target, priceConverter) {
        target.useBitmapCoordinateSpace(scope => this._drawImpl(scope, priceConverter));
    }

    _drawImpl({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr }, priceToCoordinate) {
        if (!this._data || !this._options || !this._data.visibleRange || this._data.bars.length === 0) return;

        const { from, to } = this._data.visibleRange;
        const opts = this._options;

        // Body width in media coords (pixelRatio=1); positionsLine will scale to bitmap
        const bodyWidthMedia = candlestickWidth(this._data.barSpacing, 1);
        // Radius in bitmap coords (scale media barSpacing/3 by pixel ratio)
        const radiusMedia = opts.radius(this._data.barSpacing);
        const radiusBitmap = radiusMedia * Math.min(hr, vr);

        for (let i = from; i < to; i++) {
            const bar = this._data.bars[i];
            const d = bar.originalData;
            if (d.open == null || d.close == null || d.high == null || d.low == null) continue;

            const isUp = d.close >= d.open;

            const openY  = priceToCoordinate(d.open)  ?? 0;
            const closeY = priceToCoordinate(d.close) ?? 0;
            const highY  = priceToCoordinate(d.high)  ?? 0;
            const lowY   = priceToCoordinate(d.low)   ?? 0;

            // Wick — 1 media-px wide, spans low→high
            ctx.fillStyle = isUp ? opts.wickUpColor : opts.wickDownColor;
            const wickV = positionsBox(highY, lowY, vr);
            const wickH = positionsLine(bar.x, hr, 1);
            ctx.fillRect(wickH.position, wickV.position, wickH.length, wickV.length);

            // Body — rounded rectangle
            ctx.fillStyle = isUp ? opts.upColor : opts.downColor;
            const bodyV = positionsBox(Math.min(openY, closeY), Math.max(openY, closeY), vr);
            const bodyH = positionsLine(bar.x, hr, bodyWidthMedia);

            const r = Math.min(radiusBitmap, bodyH.length / 2, bodyV.length / 2);
            if (ctx.roundRect && r > 0 && bodyV.length > 1) {
                ctx.beginPath();
                ctx.roundRect(bodyH.position, bodyV.position, bodyH.length, bodyV.length, r);
                ctx.fill();
            } else {
                ctx.fillRect(bodyH.position, bodyV.position, bodyH.length, bodyV.length);
            }
        }
    }
}

// ── Custom Series Pane View ────────────────────────────────────────────────────

export class RoundedCandleSeriesView {
    constructor() {
        this._renderer = new RoundedCandleRenderer();
    }

    priceValueBuilder(plotRow) {
        return [plotRow.high, plotRow.low, plotRow.close];
    }

    isWhitespace(data) {
        return data.close === undefined;
    }

    renderer() {
        return this._renderer;
    }

    update(data, options) {
        this._renderer.update(data, options);
    }

    defaultOptions() {
        return {
            ...customSeriesDefaultOptions,
            upColor: '#26a69a',
            downColor: '#ef5350',
            wickUpColor: '#26a69a',
            wickDownColor: '#ef5350',
            radius: (bs) => (bs < 4 ? 0 : bs / 3),
        };
    }
}
