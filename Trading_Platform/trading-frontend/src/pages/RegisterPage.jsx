import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { register as registerApi } from '../api/authApi';

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
        <div style={styles.container}>
            <div style={styles.card}>
                <h1 style={styles.title}>TradingView Clone</h1>
                <p style={styles.subtitle}>Create your account</p>

                <form onSubmit={handleSubmit} style={styles.form}>
                    <div style={styles.inputGroup}>
                        <label style={styles.label}>Username</label>
                        <input
                            type="text"
                            value={username}
                            onChange={(e) => setUsername(e.target.value)}
                            placeholder="Enter your username"
                            required
                            style={styles.input}
                        />
                    </div>

                    <div style={styles.inputGroup}>
                        <label style={styles.label}>Email</label>
                        <input
                            type="email"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            placeholder="Enter your email"
                            required
                            style={styles.input}
                        />
                    </div>

                    <div style={styles.inputGroup}>
                        <label style={styles.label}>Password</label>
                        <input
                            type="password"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            placeholder="Enter your password"
                            required
                            style={styles.input}
                        />
                    </div>

                    {error && <p style={styles.error}>{error}</p>}

                    <button type="submit" disabled={loading} style={styles.button}>
                        {loading ? 'Creating account...' : 'Create Account'}
                    </button>
                </form>

                <p style={styles.footer}>
                    Already have an account?{' '}
                    <Link to="/login" style={styles.link}>Sign in here</Link>
                </p>
            </div>
        </div>
    );
}

const styles = {
    container: {
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '100vh',
        backgroundColor: '#131722',
    },
    card: {
        backgroundColor: '#1e222d',
        padding: '2.5rem',
        borderRadius: '8px',
        width: '100%',
        maxWidth: '400px',
        boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
    },
    title: {
        color: '#d1d4dc',
        fontSize: '1.5rem',
        marginBottom: '0.5rem',
        textAlign: 'center',
    },
    subtitle: {
        color: '#787b86',
        fontSize: '0.9rem',
        textAlign: 'center',
        marginBottom: '2rem',
    },
    form: {
        display: 'flex',
        flexDirection: 'column',
        gap: '1.2rem',
    },
    inputGroup: {
        display: 'flex',
        flexDirection: 'column',
        gap: '0.4rem',
    },
    label: {
        color: '#d1d4dc',
        fontSize: '0.85rem',
    },
    input: {
        backgroundColor: '#2a2e39',
        border: '1px solid #363a45',
        borderRadius: '4px',
        padding: '0.75rem 1rem',
        color: '#d1d4dc',
        fontSize: '0.95rem',
        outline: 'none',
    },
    error: {
        color: '#f7525f',
        fontSize: '0.85rem',
        margin: 0,
    },
    button: {
        backgroundColor: '#2962ff',
        color: '#ffffff',
        border: 'none',
        borderRadius: '4px',
        padding: '0.85rem',
        fontSize: '1rem',
        cursor: 'pointer',
        marginTop: '0.5rem',
    },
    footer: {
        color: '#787b86',
        fontSize: '0.85rem',
        textAlign: 'center',
        marginTop: '1.5rem',
    },
    link: {
        color: '#2962ff',
        textDecoration: 'none',
    },
};
