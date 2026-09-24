/*
 * HTTP client and shared session state.
 *
 * The token lives in module scope rather than localStorage: a JWT in
 * localStorage is readable by any script on the page, and the session is
 * short-lived anyway.
 */

import { reactive, readonly } from "vue";

export const API_BASE = "http://localhost:8000";

const state = reactive({
  token: null,
  user: null,
  health: { status: "checking", postgres: false, mongodb: false },
});

export const session = readonly(state);

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;

  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch {
    throw new ApiError("Cannot reach the API. Is the server running?", 0);
  }

  const body = res.status === 204 ? null : await res.json().catch(() => null);

  if (!res.ok) {
    // The API answers with {"detail": "..."} consistently, so the UI never
    // has to invent its own wording for a server-side failure.
    let detail = body?.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join("; ");
    throw new ApiError(detail || `Request failed (${res.status})`, res.status);
  }
  return body;
}

export async function signIn(email, password) {
  const r = await api("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  state.token = r.access_token;
  state.user = await api("/api/v1/users/me/profile");
  return state.user;
}

// Registration returns the new user but no token, so sign straight in
// afterwards; the user should not have to type the same password twice.
export async function register({ email, password, full_name, phone }) {
  await api("/api/v1/users", {
    method: "POST",
    body: JSON.stringify({ email, password, full_name, phone: phone || null }),
  });
  return signIn(email, password);
}

// Sends only the fields that changed; the server leaves the rest untouched.
export async function updateProfile(changes) {
  state.user = await api("/api/v1/users/me", {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
  return state.user;
}

export function signOut() {
  state.token = null;
  state.user = null;
}

export async function refreshHealth() {
  try {
    state.health = await api("/health");
  } catch {
    state.health = { status: "unreachable", postgres: false, mongodb: false };
  }
  return state.health;
}

export const money = (v, currency = "THB") =>
  `${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;

export const SERVICE_MINUTES = 90;

export function windowFor(startsAtLocal) {
  const start = new Date(startsAtLocal);
  const end = new Date(start.getTime() + SERVICE_MINUTES * 60 * 1000);
  return { start, end };
}

export const CITIES = ["Chiang Mai", "Bangkok", "Phuket", "Khon Kaen", "Hat Yai"];
export const CUISINES = [
  "Thai", "Japanese", "Italian", "Indian", "Chinese",
  "Korean", "Mexican", "Vietnamese", "French", "Vegan",
];
