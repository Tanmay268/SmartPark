import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function Login() {
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
      const user = await login(email, password);
      navigate(user.role === 'admin' ? '/admin' : '/dashboard');
    } catch (err) {
      setError(err.response?.data?.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-box">
        <div className="auth-logo">
          <h1>SmartPark</h1>
          <p>IoT-powered parking with camera-based gate automation.</p>
        </div>
        <div className="card">
          <h2 style={{ marginBottom: '1.5rem', fontSize: '1.2rem' }}>Sign In</h2>
          {error && <div className="alert alert-error">{error}</div>}
          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label>Email</label>
              <input
                className="input"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                required
              />
            </div>
            <div className="form-group">
              <label>Password</label>
              <input
                className="input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
                required
              />
            </div>
            <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center' }} type="submit" disabled={loading}>
              {loading ? 'Signing in...' : 'Sign In'}
            </button>
          </form>
          <div className="divider">
            Don't have an account? <Link to="/register" style={{ color: 'var(--accent-strong)' }}>Register</Link>
          </div>
          <div style={{ background: 'var(--surface2)', borderRadius: 14, padding: '0.9rem', fontSize: '0.82rem', color: 'var(--text-muted)' }}>
            <strong>Demo credentials:</strong>
            <br />
            Admin -> admin@parking.com / admin123
          </div>
        </div>
      </div>
    </div>
  );
}
