export interface Principal {
  username: string;
  role: string;
}

export interface SessionDocument {
  authenticated: true;
  principal: Principal;
  expiresAt?: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    cache: "no-store",
    credentials: "same-origin",
    headers: { Accept: "application/json", ...init.headers },
  });
  if (!response.ok) {
    let code = "request_failed";
    let message = "GitFerry could not complete the request.";
    try {
      const body = (await response.json()) as { error?: { code?: unknown; message?: unknown } };
      if (typeof body.error?.code === "string") code = body.error.code;
      if (typeof body.error?.message === "string") message = body.error.message;
    } catch {
      // Empty and non-JSON errors still fail closed.
    }
    throw new ApiError(response.status, code, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function getAuthStatus(): Promise<{ bootstrapRequired: boolean }> {
  return request("/api/v1/auth/status");
}

export function getSession(): Promise<SessionDocument> {
  return request("/api/v1/auth/session");
}

export function login(username: string, password: string): Promise<SessionDocument> {
  return request("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export function logout(): Promise<void> {
  const csrf = readCookie("gitferry_csrf");
  return request("/api/v1/auth/logout", {
    method: "POST",
    headers: csrf ? { "x-gitferry-csrf": csrf } : {},
  });
}

function readCookie(name: string): string | undefined {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const value = part.trim();
    if (value.startsWith(prefix)) return decodeURIComponent(value.slice(prefix.length));
  }
  return undefined;
}
