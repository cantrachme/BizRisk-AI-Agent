import {
  InvestigationListItem,
  InvestigationDetail,
  EvidenceItem,
  RiskAnalysis,
  HistoricalReport,
  HumanInterventionStatus
} from '../types';

function getBaseUrl(): string {
  const envUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000/api/v1';
  let clean = envUrl.trim().replace(/\/+$/, '');
  if (!clean.endsWith('/api/v1')) {
    clean = `${clean}/api/v1`;
  }
  return clean;
}

export const API_BASE_URL = getBaseUrl();

export class APIError extends Error {
  status: number;
  detail: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = 'APIError';
    this.status = status;
    this.detail = detail;
  }
}

function getAuthHeader(): Record<string, string> {
  if (typeof window !== 'undefined') {
    const token = localStorage.getItem('bizrisk_token');
    if (token) {
      return { 'Authorization': `Bearer ${token}` };
    }
  }
  return {};
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const url = `${API_BASE_URL}${normalizedPath}`;
  const headers = {
    'Content-Type': 'application/json',
    ...getAuthHeader(),
    ...(options.headers || {}),
  };

  let response: Response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch {
    throw new APIError('Network connection failed. Please check if the server is running.', 500);
  }

  if (response.status === 401) {
    if (typeof window !== 'undefined') {
      localStorage.removeItem('bizrisk_token');
      // Force reload to login screen if unauthorized in the browser context
      window.dispatchEvent(new Event('auth_logout'));
    }
    throw new APIError('Unauthorized access. Please log in again.', 401);
  }

  let data: Record<string, unknown> | null = null;
  const contentType = response.headers.get('content-type');
  if (contentType && contentType.includes('application/json')) {
    data = await response.json() as Record<string, unknown>;
  }

  if (!response.ok) {
    const detail = data?.detail || 'An unexpected error occurred';
    const message = typeof detail === 'string' ? detail : JSON.stringify(detail);
    throw new APIError(message, response.status, detail);
  }

  return data as unknown as T;
}

export const api = {
  // Investigations List
  getInvestigations: () => request<InvestigationListItem[]>('/investigations/'),
  getIncompleteInvestigations: () => request<InvestigationListItem[]>('/investigations/incomplete'),
  
  // Investigation Detail
  getInvestigation: (id: string) => request<InvestigationDetail>(`/investigations/${id}`),
  
  // Create Investigation
  createInvestigation: (payload: {
    business_name?: string;
    gstin?: string;
    cin?: string;
    website?: string;
    location?: string;
    additional_information?: string;
  }) => request<{ id: string; status: string }>('/investigations/', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),

  // Get Evidence
  getEvidence: (id: string) => request<EvidenceItem[]>(`/investigations/${id}/evidence`),

  // Get Risk Score & Signals
  getRisk: (id: string) => request<RiskAnalysis>(`/investigations/${id}/risk`),

  // Get Latest Report
  getReport: (id: string) => request<Record<string, unknown>>(`/investigations/${id}/report`),

  // Get Historical Reports
  getReports: (id: string) => request<HistoricalReport[]>(`/investigations/${id}/reports`),

  // Get Human Intervention Status
  getHumanIntervention: (id: string) => request<HumanInterventionStatus>(`/investigations/${id}/human-intervention`),

  // Resume Investigation
  resumeInvestigation: (id: string) => request<{ id: string; status: string }>(`/investigations/${id}/resume`, {
    method: 'POST',
  }),

  // Get Events List
  getEvents: (id: string) => request<any[]>(`/investigations/${id}/events`),

  // Get Events stream URL helper
  getEventsStreamUrl: (id: string) => `${API_BASE_URL}/investigations/${id}/events/stream`,

  // Complete Human Intervention on Task
  completeHumanIntervention: (investigationId: string, taskId: string) => request<{ status: string; investigation_status: string; task_status: string }>(`/investigations/${investigationId}/tasks/${taskId}/human-intervention`, {
    method: 'POST',
  }),

  // Browser Session Diagnostics & HITL Controls
  getBrowserSession: (investigationId: string, taskId: string) => request<{ status: string; has_session: boolean; requires_interaction: boolean; metadata?: Record<string, unknown> }>(`/investigations/${investigationId}/tasks/${taskId}/browser-session`),

  sendClick: (investigationId: string, taskId: string, payload: { x: number; y: number }) => request<{ status: string }>(`/investigations/${investigationId}/tasks/${taskId}/click`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),

  sendType: (investigationId: string, taskId: string, payload: { text: string }) => request<{ status: string }>(`/investigations/${investigationId}/tasks/${taskId}/type`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),

  getScreenshotUrl: (investigationId: string, taskId: string, cb?: number) => `${API_BASE_URL}/investigations/${investigationId}/tasks/${taskId}/screenshot?cb=${cb || 0}`,
};
