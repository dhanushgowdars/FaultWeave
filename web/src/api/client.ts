import type {
  AnalysisResponse,
  DemoScenario,
  DemoSession,
  LoginResponse,
  PaymentFormValue,
  TransferEvidence,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export class FaultWeaveApi {
  constructor(private token: string | null = null) {}

  setToken(token: string | null) {
    this.token = token;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body) headers.set("Content-Type", "application/json");
    if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
    const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
    if (!response.ok) {
      let message = `Request failed with HTTP ${response.status}`;
      try {
        const value = (await response.json()) as { detail?: string };
        if (value.detail) message = value.detail;
      } catch {
        // Preserve the status-based message when the response is not JSON.
      }
      throw new ApiError(message, response.status);
    }
    return (await response.json()) as T;
  }

  login(username: string, password: string) {
    return this.request<LoginResponse>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
  }

  health() {
    return this.request<{ service: string; status: string }>("/health/ready");
  }

  contract() {
    return this.request<Record<string, unknown>>("/api/v1/demo/contract");
  }

  async scenarios() {
    const response = await this.request<{ items: DemoScenario[] }>("/api/v1/demo/scenarios");
    return response.items;
  }

  async sessions(limit = 50) {
    const response = await this.request<{ items: DemoSession[] }>(
      `/api/v1/demo/sessions?limit=${limit}`,
    );
    return response.items;
  }

  createSession(value: PaymentFormValue) {
    return this.request<DemoSession>("/api/v1/demo/sessions", {
      method: "POST",
      body: JSON.stringify(value),
    });
  }

  getSession(sessionId: string) {
    return this.request<DemoSession>(`/api/v1/demo/sessions/${sessionId}`);
  }

  arm(sessionId: string) {
    return this.request<DemoSession>(`/api/v1/demo/sessions/${sessionId}/arm`, {
      method: "POST",
    });
  }

  execute(sessionId: string) {
    return this.request<{ session: DemoSession; evidence: TransferEvidence }>(
      `/api/v1/demo/sessions/${sessionId}/execute`,
      { method: "POST" },
    );
  }

  analyze(sessionId: string) {
    return this.request<AnalysisResponse>(`/api/v1/demo/sessions/${sessionId}/analyze`, {
      method: "POST",
    });
  }

  approve(sessionId: string, note: string) {
    return this.request<DemoSession>(`/api/v1/demo/sessions/${sessionId}/approve`, {
      method: "POST",
      body: JSON.stringify({ note }),
    });
  }

  recover(sessionId: string) {
    return this.request<{ session: DemoSession; evidence: TransferEvidence }>(
      `/api/v1/demo/sessions/${sessionId}/recover`,
      { method: "POST" },
    );
  }

  evidence(transferId: string) {
    return this.request<TransferEvidence>(`/api/v1/transfers/${transferId}/evidence`);
  }

  exportEvidence(transferId: string) {
    return this.request<Record<string, unknown>>(
      `/api/v1/transfers/${transferId}/evidence/export`,
    );
  }
}
