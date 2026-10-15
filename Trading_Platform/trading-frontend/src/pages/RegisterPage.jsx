import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { register as registerApi } from '../api/authApi';
import AuthLayout from '../components/AuthLayout';

export default function RegisterPage() {
    const [username, setUsername] = useState('');
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const navigate = useNavigate();

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setLoading(true);

        try {
            await registerApi(username, email, password);
            navigate('/login');
        } catch (err) {
            setError(err.response?.data?.message || 'Registration failed. Please try again.');
        } finally {
            setLoading(false);
        }
    };

    return (
        <AuthLayout
            title="Create your account"
            subtitle="Start paper trading with $100,000"
            footer={<>Already have an account? <Link to="/login">Sign in</Link></>}
        >
            <form onSubmit={handleSubmit}>
                <label className="field">
                    <span className="field-label">Username</span>
                    <input
                        className="input" type="text" autoComplete="username" required
                        value={username} onChange={(e) => setUsername(e.target.value)}
                        placeholder="Enter your username"
                    />
                </label>

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
                        className="input" type="password" autoComplete="new-password" required
                        value={password} onChange={(e) => setPassword(e.target.value)}
                        placeholder="Enter your password"
                    />
                </label>

                {error && <p className="form-error" role="alert">{error}</p>}

                <button type="submit" disabled={loading} className="btn btn-primary btn-lg btn-block">
                    {loading ? 'Creating account...' : 'Create Account'}
                </button>
            </form>
        </AuthLayout>
    );
}
