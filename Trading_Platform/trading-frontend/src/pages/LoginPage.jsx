import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { login as loginApi } from '../api/authApi';
import { useAuth } from '../context/AuthContext';
import AuthLayout from '../components/AuthLayout';

export default function LoginPage() {
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const { login } = useAuth();
    const navigate = useNavigate();

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setLoading(true);

        try {
            const data = await loginApi(email, password);
            login(data.accessToken);
            navigate('/chart');
        } catch (err) {
            setError(err.response?.data?.message || 'Login failed. Please try again.');
        } finally {
            setLoading(false);
        }
    };

    return (
        <AuthLayout
            title="Welcome back"
            subtitle="Sign in to your account"
            footer={<>Don't have an account? <Link to="/register">Create one</Link></>}
        >
            <form onSubmit={handleSubmit}>
                <label className="field">
                    <span className="field-label">Email</span>
                    <input
                        className="input" type="email" autoComplete="email" required
                        value={email} onChange={(e) => setEmail(e.target.value)}
                        placeholder="Enter your email"
                    />
                </label>

                <label className="field">
                    <span className="field-label">Password</span>
                    <input
                        className="input" type="password" autoComplete="current-password" required
                        value={password} onChange={(e) => setPassword(e.target.value)}
                        placeholder="Enter your password"
                    />
                </label>

                {error && <p className="form-error" role="alert">{error}</p>}

                <button type="submit" disabled={loading} className="btn btn-primary btn-lg btn-block">
                    {loading ? 'Signing in...' : 'Sign In'}
                </button>
            </form>
        </AuthLayout>
    );
}
