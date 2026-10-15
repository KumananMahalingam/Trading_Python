import { ArrowDownRight, ArrowUpRight } from 'lucide-react';

/** Tinted +/- percentage badge. `value` is a percentage (1.5 = 1.5%). */
export default function ChangePill({ value, suffix = '%', digits = 2, label }) {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        return <span className="pill pill-neutral">–</span>;
    }
    const up = value > 0;
    const down = value < 0;
    const Icon = up ? ArrowUpRight : ArrowDownRight;
    return (
        <span className={`pill ${up ? 'pill-up' : down ? 'pill-down' : 'pill-neutral'}`}>
            {(up || down) && <Icon size={12} aria-hidden="true" />}
            {up ? '+' : down ? '−' : ''}{Math.abs(value).toFixed(digits)}{suffix}
            {label && <span className="visually-hidden"> {label}</span>}
        </span>
    );
}
