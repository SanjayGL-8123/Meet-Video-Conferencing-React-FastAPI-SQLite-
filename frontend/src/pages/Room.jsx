import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, getToken } from '../api.js';
import { useAuth } from '../App.jsx';

// STUN only. Behind strict NATs/firewalls you also need a TURN server (see README).
const ICE = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };

function Tile({ stream, label, muted, audioOff, videoOff, mirror }) {
  const ref = useRef(null);
  useEffect(() => { if (ref.current) ref.current.srcObject = stream || null; }, [stream]);
  return (
    <figure className="tile">
      <video ref={ref} autoPlay playsInline muted={muted} className={mirror ? 'mirror' : ''} />
      {(videoOff || !stream) && <div className="avatar">{label[0]?.toUpperCase()}</div>}
      <figcaption>{label}{audioOff ? ' 🔇' : ''}</figcaption>
    </figure>
  );
}

export default function Room() {
  const { code } = useParams();
  const nav = useNavigate();
  const { user } = useAuth();
  const [status, setStatus] = useState('connecting'); // connecting | live | ended | error
  const [error, setError] = useState('');
  const [room, setRoom] = useState(null);
  const [peers, setPeers] = useState({});
  const [messages, setMessages] = useState([]);
  const [preview, setPreview] = useState(null);
  const [mic, setMic] = useState(false);
  const [cam, setCam] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [isHost, setIsHost] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [copied, setCopied] = useState(false);
  const roomEndedByHost = useRef(false);

  const wsRef = useRef(null);
  const pcs = useRef({});
  const streams = useRef({});
  const pending = useRef({});
  const camStream = useRef(null);
  const screen = useRef(null);
  const logRef = useRef(null);

  const send = useCallback((m) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(m));
  }, []);

  const closePeer = useCallback((id) => {
    pcs.current[id]?.close();
    delete pcs.current[id]; delete streams.current[id]; delete pending.current[id];
  }, []);

  const teardown = useCallback(() => {
    wsRef.current?.close();
    Object.keys(pcs.current).forEach(closePeer);
    camStream.current?.getTracks().forEach((t) => t.stop());
    screen.current?.getTracks().forEach((t) => t.stop());
  }, [closePeer]);

  const swapVideo = useCallback(async (track) => {
    await Promise.all(Object.values(pcs.current).map((pc) => {
      const s = pc.getSenders().find((x) => x.track?.kind === 'video');
      if (!s && track) {
        return pc.addTrack(track);
      }
      return s ? s.replaceTrack(track) : null;
    }));
  }, []);

  const stopShare = useCallback(async () => {
    if (!screen.current) return;
    screen.current.getTracks().forEach((t) => t.stop());
    screen.current = null;
    const ct = camStream.current?.getVideoTracks()[0] || null;
    await swapVideo(ct);
    setPreview(camStream.current);
    setSharing(false);
  }, [swapVideo]);

  useEffect(() => {
    let cancelled = false;

    const createPc = (id) => {
      const pc = new RTCPeerConnection(ICE);
      const local = camStream.current;
      for (const kind of ['audio', 'video']) {
        const track = local?.getTracks().find((t) => t.kind === kind);
        if (track) pc.addTrack(track, local);
        else pc.addTransceiver(kind, { direction: 'recvonly' });
      }
      pc.onicecandidate = (e) => e.candidate && send({ type: 'signal', to: id, data: { candidate: e.candidate } });
      pc.ontrack = (e) => {
        const ms = (streams.current[id] ||= new MediaStream());
        if (!ms.getTracks().includes(e.track)) ms.addTrack(e.track);
        setPeers((p) => (p[id] ? { ...p, [id]: { ...p[id], stream: ms } } : p));
      };
      pcs.current[id] = pc;
      return pc;
    };

    const flush = async (id) => {
      const pc = pcs.current[id];
      const list = pending.current[id] || [];
      pending.current[id] = [];
      for (const c of list) await pc.addIceCandidate(c).catch(() => {});
    };

    const onSignal = async (from, data) => {
      let pc = pcs.current[from];
      if (data.sdp) {
        if (data.sdp.type === 'offer') {
          pc = pc || createPc(from);
          await pc.setRemoteDescription(data.sdp);
          await flush(from);
          const answer = await pc.createAnswer();
          await pc.setLocalDescription(answer);
          send({ type: 'signal', to: from, data: { sdp: pc.localDescription } });
        } else if (pc) {
          await pc.setRemoteDescription(data.sdp);
          await flush(from);
        }
      } else if (data.candidate) {
        if (pc && pc.remoteDescription) await pc.addIceCandidate(data.candidate).catch(() => {});
        else (pending.current[from] ||= []).push(data.candidate);
      }
    };

    async function start() {
      let info;
      try { info = await api(`/rooms/${code}`); }
      catch (e) { if (!cancelled) { setError(e.message); setStatus('error'); } return; }
      if (cancelled) return;
      if (!info.is_active) { setStatus('ended'); return; }
      setRoom(info);

      let media = null;
      const attempts = [{ audio: true, video: { width: { ideal: 1280 }, height: { ideal: 720 } } }, { audio: true }, { video: true }];
      for (const c of attempts) {
        try { media = await navigator.mediaDevices.getUserMedia(c); break; } catch { /* try next */ }
      }
      if (cancelled) { media?.getTracks().forEach((t) => t.stop()); return; }
      const hasAudio = !!media?.getAudioTracks().length;
      const hasVideo = !!media?.getVideoTracks().length;
      camStream.current = media;
      setPreview(media); setMic(hasAudio); setCam(hasVideo);

      const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/rooms/${code}`);
      wsRef.current = ws;
      ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token: getToken() }));
      ws.onclose = () => {
        if (!cancelled) {
          setStatus((s) => (s === 'error' ? s : roomEndedByHost.current ? 'ended' : 'disconnected'));
        }
      };
      ws.onmessage = async (ev) => {
        let m;
        try { m = JSON.parse(ev.data); } catch { return; }
        switch (m.type) {
          case 'welcome': {
            setStatus('live'); setIsHost(m.is_host);
            setPeers(Object.fromEntries(m.peers.map((p) => [p.id, { ...p }])));
            send({ type: 'state', audio: hasAudio, video: hasVideo });
            api(`/rooms/${code}/messages`).then((hist) => setMessages((prev) => {
              const ids = new Set(prev.map((x) => x.id));
              return [...hist.filter((h) => !ids.has(h.id)), ...prev];
            })).catch(() => {});
            for (const p of m.peers) { // the newcomer makes the offers
              const pc = createPc(p.id);
              const offer = await pc.createOffer();
              await pc.setLocalDescription(offer);
              send({ type: 'signal', to: p.id, data: { sdp: pc.localDescription } });
            }
            break;
          }
          case 'peer-joined': setPeers((p) => ({ ...p, [m.peer.id]: m.peer })); break;
          case 'peer-left':
            closePeer(m.peer_id);
            setPeers((p) => { const n = { ...p }; delete n[m.peer_id]; return n; });
            break;
          case 'signal': await onSignal(m.from, m.data); break;
          case 'state':
            setPeers((p) => (p[m.peer_id] ? { ...p, [m.peer_id]: { ...p[m.peer_id], audio: m.audio, video: m.video } } : p));
            break;
          case 'chat': setMessages((l) => [...l, m]); break;
          case 'ended':
            roomEndedByHost.current = true;
            setStatus('ended');
            break;
          case 'error':
            if (['unauthorized', 'room_full', 'room_unavailable', 'auth_required'].includes(m.reason)) {
              setError({ room_full: 'This meeting is full.', room_unavailable: 'This meeting has ended.' }[m.reason] || 'Could not join.');
              setStatus('error');
            } else if (m.reason === 'rate_limited') setError('You are sending messages too fast.');
            break;
          default: break;
        }
      };
    }
    start();
    return () => { cancelled = true; teardown(); };
  }, [code, send, closePeer, teardown]);

  useEffect(() => { if (status === 'ended' || status === 'error' || status === 'disconnected') teardown(); }, [status, teardown]);
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [messages, chatOpen]);

  const toggle = (kind) => {
    const tracks = kind === 'audio' ? camStream.current?.getAudioTracks() : camStream.current?.getVideoTracks();
    if (!tracks?.length) return;
    const on = !tracks[0].enabled;
    tracks.forEach((t) => { t.enabled = on; });
    if (kind === 'audio') { setMic(on); send({ type: 'state', audio: on, video: cam }); }
    else { setCam(on); send({ type: 'state', audio: mic, video: on }); }
  };

  async function toggleShare() {
    if (sharing) return stopShare();
    try {
      const s = await navigator.mediaDevices.getDisplayMedia({ video: true });
      screen.current = s;
      const track = s.getVideoTracks()[0];
      track.onended = stopShare;
      await swapVideo(track);
      setPreview(s); setSharing(true);
    } catch { /* user cancelled the picker */ }
  }

  const submit = (e) => {
    e.preventDefault();
    const t = draft.trim();
    if (!t) return;
    send({ type: 'chat', content: t });
    setDraft('');
  };

  const copyInvite = async () => {
    try { await navigator.clipboard.writeText(location.href); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { setError('Could not copy the link.'); }
  };

  const endForAll = () => { if (window.confirm('End this meeting for everyone?')) send({ type: 'end' }); };

  if (status === 'connecting') return <div className="splash"><div className="spinner" aria-label="Joining" /><p>Joining meeting…</p></div>;
  if (status === 'error' || status === 'ended' || status === 'disconnected') {
    const isConnLoss = status === 'disconnected';
    return (
      <main id="main" className="center-card">
        <h1>{isConnLoss ? 'Connection lost' : status === 'ended' ? 'Meeting ended' : 'Cannot join'}</h1>
        <p className="muted">
          {isConnLoss ? 'You were disconnected from the meeting. Check your internet connection.' :
           status === 'ended' ? 'This meeting was ended by the host.' : error}
        </p>
        <div className="row" style={{ justifyContent: 'center', marginTop: '1rem' }}>
          {isConnLoss && <button className="btn primary" onClick={() => { setStatus('connecting'); setError(''); }}>Reconnect</button>}
          <button className={`btn ${isConnLoss ? '' : 'primary'}`} onClick={() => nav('/')}>Back to dashboard</button>
        </div>
      </main>
    );
  }

  const others = Object.entries(peers);
  return (
    <div className="room">
      <header className="room-bar">
        <strong>{room?.name}</strong> <code>{code}</code>
        <span className="spacer" />
        <button className="btn" onClick={copyInvite}>{copied ? 'Copied!' : 'Copy invite link'}</button>
      </header>
      {error && <p className="error banner" role="alert">{error}</p>}
      <div className="room-main">
        <main id="main" className="grid">
          <Tile stream={preview} label={`${user.username} (You)`} muted mirror={!sharing}
                audioOff={!mic} videoOff={!cam && !sharing} />
          {others.map(([id, p]) => (
            <Tile key={id} stream={p.stream} label={p.username} audioOff={!p.audio} videoOff={!p.video} />
          ))}
          {others.length === 0 && <p className="empty">You're the only one here. Copy the invite link and share it.</p>}
        </main>
        {chatOpen && (
          <aside className="chat" aria-label="In-meeting chat">
            <div className="chat-log" ref={logRef}>
              {messages.length === 0 && <p className="empty">No messages yet.</p>}
              {messages.map((m) => (
                <p key={m.id} className={m.user_id === user.id ? 'msg me' : 'msg'}>
                  <strong>{m.user}</strong><span>{m.content}</span>
                </p>
              ))}
            </div>
            <form onSubmit={submit} className="chat-form">
              <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Message…" aria-label="Chat message" maxLength={2000} />
              <button className="btn primary">Send</button>
            </form>
          </aside>
        )}
      </div>
      <footer className="controls">
        <button className={`btn ${mic ? '' : 'off'}`} onClick={() => toggle('audio')} disabled={!camStream.current?.getAudioTracks().length} aria-label={mic ? 'Mute microphone' : 'Unmute microphone'} aria-pressed={mic}>{mic ? '🎤 Mute' : '🔇 Unmute'}</button>
        <button className={`btn ${cam ? '' : 'off'}`} onClick={() => toggle('video')} disabled={!camStream.current?.getVideoTracks().length} aria-label={cam ? 'Turn camera off' : 'Turn camera on'} aria-pressed={cam}>{cam ? '📷 Camera off' : '🚫 Camera on'}</button>
        <button className={`btn ${sharing ? 'primary' : ''}`} onClick={toggleShare} aria-label={sharing ? 'Stop sharing screen' : 'Share screen'} aria-pressed={sharing}>{sharing ? '⏹ Stop sharing' : '🖥 Share screen'}</button>
        <button className="btn" onClick={() => setChatOpen(!chatOpen)} aria-label="Toggle in-meeting chat" aria-expanded={chatOpen}>💬 Chat</button>
        <button className="btn danger" onClick={() => nav('/')}>Leave</button>
        {isHost && <button className="btn danger" onClick={endForAll}>End for all</button>}
      </footer>
    </div>
  );
}
