import { useEffect, useState } from 'react';
import { getAllSymbols } from '../api/symbolApi';
import { useAuth } from '../context/AuthContext';

// One request per page load, shared by every component that needs symbols.
let cache = null;   // { promise, data }

function loadSymbols() {
    if (!cache) {
        const entry = { data: null, promise: null };
        entry.promise = getAllSymbols()
            .then(data => { entry.data = data; return data; })
            .catch(err => { if (cache === entry) cache = null; throw err; });
        cache = entry;
    }
    return cache.promise;
}

/** All active symbols ([] until loaded or when signed out). */
export function useSymbols() {
    const { accessToken } = useAuth();
    const [symbols, setSymbols] = useState(() => cache?.data ?? []);

    useEffect(() => {
        if (!accessToken) return;
        let cancelled = false;
        loadSymbols()
            .then(data => { if (!cancelled) setSymbols(data); })
            .catch(() => { /* search simply has no suggestions */ });
        return () => { cancelled = true; };
    }, [accessToken]);

    return symbols;
}
