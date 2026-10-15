import { useEffect, useId, useRef } from 'react';

/** Dialog with a dimmed backdrop. Closes on Escape or a backdrop click. */
export default function Modal({ title, titleClassName, onClose, children }) {
    const titleId = useId();
    const dialogRef = useRef(null);
    const onCloseRef = useRef(onClose);
    useEffect(() => { onCloseRef.current = onClose; });

    useEffect(() => {
        const previouslyFocused = document.activeElement;
        const onKey = (e) => { if (e.key === 'Escape') onCloseRef.current?.(); };
        window.addEventListener('keydown', onKey);
        // Focus the first field unless a child already took focus (autoFocus).
        if (!dialogRef.current?.contains(document.activeElement)) {
            dialogRef.current?.querySelector('input, select, button')?.focus();
        }
        return () => {
            window.removeEventListener('keydown', onKey);
            previouslyFocused?.focus?.();
        };
    }, []);

    return (
        <div className="modal-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
            <div ref={dialogRef} className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId}>
                <h2 id={titleId} className={titleClassName}>{title}</h2>
                {children}
            </div>
        </div>
    );
}
