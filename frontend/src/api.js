const KEY = 'meet_token';
let token = sessionStorage.getItem(KEY); // cleared when the tab closes

export const getToken = () => token;
export function setToken(t) {
  token = t;
  if (t) sessionStorage.setItem(KEY, t);
  else sessionStorage.removeItem(KEY);
}

export async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch('/api' + path, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    if (res.status === 401 && token) {
      setToken(null);
      if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('auth:expired'));
    }
    const d = data?.detail;
    throw new Error(typeof d === 'string' ? d : 'Something went wrong. Please try again.');
  }
  return data;
}

export const parseCode = (text) => (text.match(/[a-z]{3}-[a-z]{4}-[a-z]{3}/i) || [])[0]?.toLowerCase() || null;
