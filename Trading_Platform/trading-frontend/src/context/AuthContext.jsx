import { createContext, useContext, useState, useEffect } from 'react';
import { refreshToken, logout as logoutApi } from '../api/authApi';
import { setAuthToken } from '../api/axiosInstance';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
    const [accessToken, setAccessToken] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        refreshToken()
            .then(data => {
                setAuthToken(data.accessToken);
                setAccessToken(data.accessToken);
            })
            .catch(() => {
                setAuthToken(null);
                setAccessToken(null);
            })
            .finally(() => setLoading(false));
    }, []);

    const login = (token) => {
        setAuthToken(token);
        setAccessToken(token);
    };

    const logout = async () => {
        try {
            await logoutApi();
        } catch (e) {}
        setAuthToken(null);
        setAccessToken(null);
    };

    return (
        <AuthContext.Provider value={{ accessToken, login, logout, loading }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    return useContext(AuthContext);
}
