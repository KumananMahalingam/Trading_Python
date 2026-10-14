// Adapted from lightweight-charts/plugin-examples/src/plugins/highlight-bar-crosshair
// Draws a subtle column highlight behind the hovered candle bar

class CrosshairHighlightRenderer {
    constructor(data, color) {
        this._data = data;
        this._color = color;
    }

    draw(target) {
        if (!this._data.visible) return;
        target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, bitmapSize }) => {
            const x = this._data.x * hr;
            const half = (Math.max(1, this._data.barSpacing) * hr) / 2;
            ctx.fillStyle = this._color;
            ctx.fillRect(
                Math.max(0, Math.round(x - half)),
                0,
                Math.round(half * 2),
                bitmapSize.height
            );
        });
    }
}

class CrosshairHighlightView {
    constructor(source) { this._source = source; }
    zOrder() { return 'bottom'; }
    renderer() { return new CrosshairHighlightRenderer(this._source._data, this._source._color); }
}

export class CrosshairHighlightPrimitive {
    constructor(color = 'rgba(255, 255, 255, 0.06)') {
        this._color = color;
        this._data = { x: 0, visible: false, barSpacing: 6 };
        this._views = [new CrosshairHighlightView(this)];
        this._attachedParams = null;
    }

    attached(param) {
        this._attachedParams = param;
        param.chart.subscribeCrosshairMove(this._moveHandler);
    }

    detached() {
        this._attachedParams?.chart.unsubscribeCrosshairMove(this._moveHandler);
        this._attachedParams = null;
    }

    paneViews() { return this._views; }
    updateAllViews() {}

    _barSpacing() {
        const chart = this._attachedParams?.chart;
        if (!chart) return 6;
        const ts = chart.timeScale();
        const range = ts.getVisibleLogicalRange();
        if (!range) return 6;
        return ts.width() / (range.to - range.from);
    }

    _moveHandler = (param) => {
        const chart = this._attachedParams?.chart;
        if (!chart || !param.logical) {
            this._data = { x: 0, visible: false, barSpacing: this._barSpacing() };
        } else {
            const coord = chart.timeScale().logicalToCoordinate(param.logical);
            this._data = { x: coord ?? 0, visible: coord !== null, barSpacing: this._barSpacing() };
        }
        this.updateAllViews();
        this._attachedParams?.requestUpdate();
    };
}
