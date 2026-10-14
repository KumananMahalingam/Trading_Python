class TrendlinePaneRenderer {
    constructor(coords, color) {
        this._coords = coords;
        this._color = color;
    }

    draw(target) {
        const c = this._coords;
        if (!c) return;

        target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio, verticalPixelRatio }) => {
            ctx.save();
            ctx.strokeStyle = this._color;
            ctx.lineWidth = Math.round(1.5 * Math.min(horizontalPixelRatio, verticalPixelRatio));
            ctx.setLineDash([]);

            ctx.beginPath();
            ctx.moveTo(Math.round(c.x1 * horizontalPixelRatio), Math.round(c.y1 * verticalPixelRatio));
            ctx.lineTo(Math.round(c.x2 * horizontalPixelRatio), Math.round(c.y2 * verticalPixelRatio));
            ctx.stroke();

            // draw small circles at endpoints
            const r = 3 * Math.min(horizontalPixelRatio, verticalPixelRatio);
            ctx.fillStyle = this._color;
            ctx.beginPath();
            ctx.arc(Math.round(c.x1 * horizontalPixelRatio), Math.round(c.y1 * verticalPixelRatio), r, 0, Math.PI * 2);
            ctx.fill();
            ctx.beginPath();
            ctx.arc(Math.round(c.x2 * horizontalPixelRatio), Math.round(c.y2 * verticalPixelRatio), r, 0, Math.PI * 2);
            ctx.fill();

            ctx.restore();
        });
    }
}

class TrendlinePaneView {
    constructor(primitive) {
        this._primitive = primitive;
    }

    zOrder() {
        return 'normal';
    }

    renderer() {
        return new TrendlinePaneRenderer(this._primitive._getCoords(), this._primitive._color);
    }
}

export class TrendlinePrimitive {
    constructor(p1, p2, color = '#f5c518') {
        this._p1 = p1;
        this._p2 = p2;
        this._color = color;
        this._chart = null;
        this._series = null;
        this._paneViews = [new TrendlinePaneView(this)];
    }

    attached({ chart, series }) {
        this._chart = chart;
        this._series = series;
    }

    detached() {
        this._chart = null;
        this._series = null;
    }

    updateAllViews() {}

    paneViews() {
        return this._paneViews;
    }

    _getCoords() {
        if (!this._chart || !this._series) return null;
        const x1 = this._chart.timeScale().timeToCoordinate(this._p1.time);
        const y1 = this._series.priceToCoordinate(this._p1.price);
        const x2 = this._chart.timeScale().timeToCoordinate(this._p2.time);
        const y2 = this._series.priceToCoordinate(this._p2.price);
        if (x1 === null || y1 === null || x2 === null || y2 === null) return null;
        return { x1, y1, x2, y2 };
    }
}
