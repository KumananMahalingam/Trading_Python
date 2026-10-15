import { Activity } from 'lucide-react';
import { BRAND_NAME } from '../brand';

/** Centred card used by the sign-in and register pages. */
export default function AuthLayout({ title, subtitle, children, footer }) {
    return (
        <main className="auth-page">
            <div className="card auth-card">
                <div className="brand">
                    <span className="brand-mark" aria-hidden="true"><Activity size={16} strokeWidth={2.5} /></span>
                    {BRAND_NAME}
                </div>
                <h1>{title}</h1>
                {subtitle && <p className="page-subtitle">{subtitle}</p>}
                {children}
                {footer && <p className="auth-footer">{footer}</p>}
            </div>
        </main>
    );
}
