import type { AuditEntry, Dispute, HealthResponse, Schemas } from '@recoup/shared';

export const API_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8080';

const TOKEN_KEY = 'recoup-token';

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: session lasts until reload */
  }
  window.dispatchEvent(new Event('recoup-auth'));
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: unknown,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  const text = await res.text();
  let body: unknown;
  try {
    body = text ? JSON.parse(text) : undefined;
  } catch {
    body = text;
  }
  if (res.status === 401 && token) setToken(null);
  if (!res.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : res.statusText || `HTTP ${res.status}`;
    throw new ApiError(res.status, detail, body);
  }
  return body as T;
}

const post = <T>(path: string, data?: unknown) =>
  api<T>(path, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) });

export type LoginResponse = Schemas['LoginResponse'];

export type DemoScenario = Schemas['DemoChargeRequest']['scenario'] & string;

export type DemoChargeResponse = Schemas['DemoChargeResponse'];

export const getHealth = () => api<HealthResponse>('/healthz');
export const login = (password: string) => post<LoginResponse>('/auth/login', { password });
export const getMe = () => api<{ user: string }>('/auth/me');
export const listDisputes = () => api<Dispute[]>('/disputes');
export const getAudit = (subjectId?: string, limit = 40) =>
  api<AuditEntry[]>(
    `/audit?limit=${limit}${subjectId ? `&subject_id=${encodeURIComponent(subjectId)}` : ''}`,
  );
export const createDemoCharge = (scenario: DemoScenario, amount?: number) =>
  post<DemoChargeResponse>('/demo/charges', { scenario, amount });
export const syncNow = () => post<Record<string, number>>('/admin/sync');
