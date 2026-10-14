import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from './context/AuthContext';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import ChartPage from './pages/ChartPage';
import PortfolioPage from './pages/PortfolioPage';

function ProtectedRoute({ children }) {
    const { accessToken, loading } = useAuth();

    if (loading) return <div style={{ color: '#d1d4dc', padding: '2rem' }}>Loading...</div>;

    return accessToken ? children : <Navigate to="/login" />;
}

function RootRedirect() {
    const { accessToken, loading } = useAuth();

    if (loading) return <div style={{ color: '#d1d4dc', padding: '2rem' }}>Loading...</div>;

    return <Navigate to={accessToken ? '/chart' : '/login'} />;
}

function App() {
    return (
        <BrowserRouter>
            <Routes>
                <Route path="/" element={<RootRedirect />} />
                <Route path="/login" element={<LoginPage />} />
                <Route path="/register" element={<RegisterPage />} />
                <Route path="/chart" element={
                    <ProtectedRoute>
                        <ChartPage />
                    </ProtectedRoute>
                } />
                <Route path="/portfolio" element={
                    <ProtectedRoute>
                        <PortfolioPage />
                    </ProtectedRoute>
                } />
                <Route path="*" element={<Navigate to="/" />} />
            </Routes>
        </BrowserRouter>
    );
}

export default App;
