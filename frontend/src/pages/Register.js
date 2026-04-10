import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function Register() {
  const [form, setForm] = useState({ name: '', email: '', password: '', phone: '' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { register } = useAuth();
  const navigate = useNavigate();

  const handleChange = (e) => setForm({ ...form, [e.target.name]: e.target.value });

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await register(form.name, form.email, form.password, form.phone);
      navigate('/dashboard');
    } catch (err) {
      setError(err.response?.data?.message || 'Registration failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-box">
        <div className="auth-logo">
          <h1>SmartPark</h1>
          <p>Create your account and reserve a spot in seconds.</p>
        </div>
        <div className="card">
          <h2 style={{ marginBottom: '1.5rem', fontSize: '1.2rem' }}>Create Account</h2>
          {error && <div className="alert alert-error">{error}</div>}
          <form onSubmit={handleSubmit}>
            {[
              { name: 'name', label: 'Full Name', type: 'text', placeholder: 'Tanmay Kaushik' },
              { name: 'email', label: 'Email', type: 'email', placeholder: 'you@example.com' },
              { name: 'password', label: 'Password', type: 'password', placeholder: 'Create a password' },
              { name: 'phone', label: 'Phone', type: 'tel', placeholder: '+91 9876543210' },
            ].map((field) => (
              <div className="form-group" key={field.name}>
                <label>{field.label}</label>
                <input
                  className="input"
                  name={field.name}
                  type={field.type}
                  value={form[field.name]}
                  onChange={handleChange}
                  placeholder={field.placeholder}
                  required={field.name !== 'phone'}
                />
              </div>
            ))}
            <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center' }} type="submit" disabled={loading}>
              {loading ? 'Creating account...' : 'Create Account'}
            </button>
          </form>
          <div className="divider">
            Already have an account? <Link to="/login" style={{ color: 'var(--accent-strong)' }}>Sign In</Link>
          </div>
        </div>
      </div>
    </div>
  );
}
