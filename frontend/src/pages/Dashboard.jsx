import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, parseCode } from '../api.js';
import { useAuth } from '../App.jsx';

export default function Dashboard({ theme, setTheme }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const [rooms, setRooms] = useState(null);
  const [name, setName] = useState('');
  const [join, setJoin] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState('');

  const load = () => api('/rooms').then(setRooms).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  async function create(e) {
    e.preventDefault();
    setError(''); setBusy(true);
    try {
      const room = await api('/rooms', { method: 'POST', body: { name } });
      nav(`/room/${room.code}`);
    } catch (err) { setError(err.message); setBusy(false); }
  }

  function joinRoom(e) {
    e.preventDefault();
    const code = parseCode(join);
    if (!code) { setError('Enter a valid meeting code or link (like abc-defg-hij)'); return; }
    nav(`/room/${code}`);
  }

  async function copy(code) {
    try {
      await navigator.clipboard.writeText(`${location.origin}/room/${code}`);
      setCopied(code); setTimeout(() => setCopied(''), 1500);
    } catch { setError('Could not copy. Copy the link from the address bar instead.'); }
  }

  return (
    <>
      <header className="topbar">
        <strong>🎥 Meet</strong>
        <span className="spacer" />
        <span className="muted">{user.username}</span>
        <button className="btn ghost" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
                aria-label="Toggle dark mode">{theme === 'dark' ? '☀️' : '🌙'}</button>
        <button className="btn" onClick={logout}>Sign out</button>
      </header>
      <main id="main" className="page">
        {error && <p className="error" role="alert">{error}</p>}
        <div className="cards">
          <form className="card stack" onSubmit={create}>
            <h2>New meeting</h2>
            <label htmlFor="create-room-name" className="sr-only">Meeting name</label>
            <input id="create-room-name" placeholder="Meeting name" aria-label="Meeting name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} />
            <button className="btn primary" disabled={busy}>{busy ? 'Creating…' : 'Start meeting'}</button>
          </form>
          <form className="card stack" onSubmit={joinRoom}>
            <h2>Join a meeting</h2>
            <label htmlFor="join-room-code" className="sr-only">Meeting code or invite link</label>
            <input id="join-room-code" placeholder="Code or invite link" aria-label="Meeting code or invite link" value={join} onChange={(e) => setJoin(e.target.value)} required />
            <button className="btn">Join</button>
          </form>
        </div>
        <h2>Your meetings</h2>
        {rooms === null ? <div className="spinner" aria-label="Loading" /> :
         rooms.length === 0 ? <p className="empty">No meetings yet. Start one above.</p> : (
          <ul className="list">
            {rooms.map((r) => (
              <li key={r.code} className="card row">
                <div>
                  <strong>{r.name}</strong> <code>{r.code}</code>
                  <div className="muted">{r.is_active ? 'Active' : 'Ended'} · {r.created_at} UTC</div>
                </div>
                <span className="spacer" />
                <button className="btn" onClick={() => copy(r.code)}>{copied === r.code ? 'Copied!' : 'Copy link'}</button>
                {r.is_active && <button className="btn primary" onClick={() => nav(`/room/${r.code}`)}>Join</button>}
              </li>
            ))}
          </ul>
        )}
      </main>
    </>
  );
}
