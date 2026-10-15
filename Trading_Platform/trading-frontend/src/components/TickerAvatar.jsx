// Coloured initial badge for a ticker, like the token icons in a market table.
// The hue is derived from the symbol so it stays stable across renders.

function hueFor(ticker) {
    let h = 0;
    for (const ch of ticker) h = (h * 31 + ch.charCodeAt(0)) % 360;
    return h;
}

export default function TickerAvatar({ ticker, size = 22 }) {
    const symbol = (ticker || '?').toUpperCase();
    const hue = hueFor(symbol);
    return (
        <span
            className="avatar"
            aria-hidden="true"
            style={{
                width: size,
                height: size,
                fontSize: Math.max(9, Math.round(size * 0.42)),
                background: `hsl(${hue} 70% 55% / 0.16)`,
                color: `hsl(${hue} 80% 70%)`,
            }}
        >
            {symbol[0]}
        </span>
    );
}
