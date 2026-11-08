import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../api.js';
import { useAuth } from '../App.jsx';

export default function Auth({ mode, theme, setTheme }) {
  const isLogin = mode === 'login';
  const { login } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [form, setForm] = useState({ username: '', email: '', identifier: '', password: '' });
  const [show, setShow] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setError(''); setBusy(true);
    try {
      const body = isLogin
        ? { identifier: form.identifier, password: form.password }
        : { username: form.username, email: form.email, password: form.password };
      const data = await api(isLogin ? '/auth/login' : '/auth/register', { method: 'POST', body });
      login(data.token, data.user);
      nav(loc.state?.from || '/', { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main id="main" className="center-card">
      <button className="btn ghost theme-btn" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              aria-label="Toggle dark mode">{theme === 'dark' ? '☀️' : '🌙'}</button>
      <h1>🎥 Meet</h1>
      <p className="muted">{isLogin ? 'Sign in to start or join a meeting' : 'Create your account'}</p>
      <form onSubmit={submit} className="stack">
        {isLogin ? (
          <div>
            <label htmlFor="login-ident">Username or email</label>
            <input id="login-ident" value={form.identifier} onChange={set('identifier')} required autoComplete="username" maxLength={254} />
          </div>
        ) : (
          <>
            <div>
              <label htmlFor="reg-user">Username</label>
              <input id="reg-user" value={form.username} onChange={set('username')} required autoComplete="username"
                     pattern="[A-Za-z0-9_]{3,30}" title="3-30 letters, digits or underscores" />
            </div>
            <div>
              <label htmlFor="reg-email">Email</label>
              <input id="reg-email" type="email" value={form.email} onChange={set('email')} required autoComplete="email" maxLength={254} />
            </div>
          </>
        )}
        <div>
          <label htmlFor="auth-password">Password</label>
          <div className="pw">
            <input id="auth-password" type={show ? 'text' : 'password'} value={form.password} onChange={set('password')} required
                   minLength={8} maxLength={72} autoComplete={isLogin ? 'current-password' : 'new-password'} />
            <button type="button" className="btn ghost" onClick={() => setShow(!show)}
                    aria-label={show ? 'Hide password' : 'Show password'}>{show ? '🙈' : '👁️'}</button>
          </div>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="btn primary" disabled={busy}>{busy ? 'Please wait…' : isLogin ? 'Sign in' : 'Create account'}</button>
      </form>
      <p className="muted">
        {isLogin ? <>No account? <Link to="/register" state={loc.state}>Register</Link></>
                 : <>Have an account? <Link to="/login" state={loc.state}>Sign in</Link></>}
      </p>
    </main>
  );
}
