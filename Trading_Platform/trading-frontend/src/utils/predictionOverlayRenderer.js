// Draws buildPredictionOverlay() output on a chart:
//  - backtest: dotted line of predicted closes + shaded +/- band
//  - forecast: dashed segment from the last close to the predicted next close,
//    with a widening cone and a price-axis label
// Bands reuse BollingerBandsPrimitive. The forecast LineSeries carries the
// target time, which puts that future day on the time scale so the cone can
// be drawn past the last candle.

import { LineSeries, LineStyle } from 'lightweight-charts';
import { BollingerBandsPrimitive } from './bollingerBandsPrimitive';

export const PREDICTION_COLOR = '#ff9800';

const lineOptions = {
    priceLineVisible: false,
    crosshairMarkerVisible: false,
};

export class PredictionOverlayRenderer {
    constructor(chart, candleSeries) {
        this._chart = chart;
        this._series = candleSeries;
        this._backtestLine = null;
        this._forecastLine = null;
        this._backtestBand = null;
        this._forecastBand = null;
    }

    update(overlay) {
        if (!overlay) {
            this.clear();
            return;
        }
        this._ensure();
        this._backtestLine.setData(overlay.backtestLine);
        this._backtestBand.setData(overlay.backtestBand);
        this._forecastLine.setData(overlay.forecastLine);
        this._forecastBand.setData(overlay.forecastBand);
    }

    clear() {
        if (this._backtestLine) this._chart.removeSeries(this._backtestLine);
        if (this._forecastLine) this._chart.removeSeries(this._forecastLine);
        if (this._backtestBand) this._series.detachPrimitive(this._backtestBand);
        if (this._forecastBand) this._series.detachPrimitive(this._forecastBand);
        this._backtestLine = this._forecastLine = this._backtestBand = this._forecastBand = null;
    }

    _ensure() {
        if (this._backtestLine) return;
        this._backtestBand = new BollingerBandsPrimitive({
            lineColor: 'rgba(255, 152, 0, 0.35)',
            midColor: 'rgba(0, 0, 0, 0)',          // the dotted LineSeries is the mid line
            fillColor: 'rgba(255, 152, 0, 0.06)',
        });
        this._forecastBand = new BollingerBandsPrimitive({
            lineColor: 'rgba(255, 152, 0, 0.6)',
            midColor: 'rgba(0, 0, 0, 0)',
            fillColor: 'rgba(255, 152, 0, 0.14)',
        });
        this._series.attachPrimitive(this._backtestBand);
        this._series.attachPrimitive(this._forecastBand);

        this._backtestLine = this._chart.addSeries(LineSeries, {
            ...lineOptions,
            color: 'rgba(255, 152, 0, 0.8)',
            lineWidth: 1,
            lineStyle: LineStyle.Dotted,
            lastValueVisible: false,
            title: '',
        });
        this._forecastLine = this._chart.addSeries(LineSeries, {
            ...lineOptions,
            color: PREDICTION_COLOR,
            lineWidth: 2,
            lineStyle: LineStyle.Dashed,
            pointMarkersVisible: true,
            pointMarkersRadius: 3,
            lastValueVisible: true,
            title: 'Model',
        });
    }
}
