// src/api.js
// In dev, calls go to "/api/*" and Vite proxies them to the backend (see
// vite.config.js). Set VITE_API_BASE to point at a deployed backend instead.
export const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export function apiFetch(path, options) {
  return fetch(`${API_BASE}${path}`, options);
}
