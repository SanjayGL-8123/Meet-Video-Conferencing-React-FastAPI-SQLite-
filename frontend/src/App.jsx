import { createContext, useContext, useEffect, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { api, getToken, setToken } from './api.js';
import Auth from './pages/Auth.jsx';
import Dashboard from './pages/Dashboard.jsx';
import Room from './pages/Room.jsx';

const AuthCtx = createContext(null);
export const useAuth = () => useContext(AuthCtx);

function Guard({ children }) {
  const { user } = useAuth();
  const loc = useLocation();
  return user ? children : <Navigate to="/login" replace state={{ from: loc.pathname }} />;
}

function NotFound() {
  return (
    <main id="main" className="center-card">
      <h1>404</h1>
      <p>That page doesn't exist.</p>
      <Link className="btn primary" to="/">Go home</Link>
    </main>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  const [theme, setTheme] = useState(
    () => localStorage.getItem('theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('theme', theme);
  }, [theme]);

  useEffect(() => {
    if (!getToken()) { setReady(true); return; }
    api('/me').then(setUser).catch(() => setToken(null)).finally(() => setReady(true));
  }, []);

  useEffect(() => {
    const onExpired = () => { logout(); };
    window.addEventListener('auth:expired', onExpired);
    return () => window.removeEventListener('auth:expired', onExpired);
  }, []);

  const login = (token, u) => { setToken(token); setUser(u); };
  const logout = () => { setToken(null); setUser(null); };

  if (!ready) return <div className="splash"><div className="spinner" aria-label="Loading" /></div>;

  return (
    <AuthCtx.Provider value={{ user, login, logout }}>
      <a className="skip" href="#main">Skip to content</a>
      <Routes>
        <Route path="/login" element={<Auth mode="login" theme={theme} setTheme={setTheme} />} />
        <Route path="/register" element={<Auth mode="register" theme={theme} setTheme={setTheme} />} />
        <Route path="/" element={<Guard><Dashboard theme={theme} setTheme={setTheme} /></Guard>} />
        <Route path="/room/:code" element={<Guard><Room /></Guard>} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </AuthCtx.Provider>
  );
}
