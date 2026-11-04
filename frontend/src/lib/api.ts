// Same-origin by default: Next.js proxies /api to the backend so the HttpOnly session cookie also covers PDF links.
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api";

export type Severity = "low" | "medium" | "high";
export type FindingType = "box_damage" | "walkway_blocked";

export interface BBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface Finding {
  id: string;
  type: FindingType;
  severity: Severity;
  confidence: number;
  bbox: BBox;
  description: string;
  sku?: string | null;
  needs_review?: boolean;
  review_reason?: string | null;
}

export interface DetectedObject {
  id: string;
  label: "box" | "walkway";
  bbox: BBox;
  confidence: number;
  mask_svg: string;
}

export interface ScanResult {
  scan_id: string;
  image_size: { width: number; height: number };
  objects: DetectedObject[];
  findings: Finding[];
  timings_ms: Record<string, number>;
  replay?: boolean;
  privacy?: "off" | "blurred" | "failed";
  uninspected_boxes?: string[];
}

export type ActionStatus = "executed" | "rejected" | "invalid" | "failed";

export interface ActionRecord {
  tool: string;
  args: Record<string, unknown>;
  status: ActionStatus;
  reason: string | null;
  result: Record<string, unknown> | null;
}

export type TicketStatus = "open" | "in_progress" | "resolved";

export interface TicketCapabilities {
  assign: boolean;
  acknowledge: boolean;
  resolve: boolean;
  note: boolean;
}

export interface Ticket {
  ticket_id: string;
  finding_id: string;
  title: string;
  severity: Severity;
  assignee_team: string;
  status: string;
  pdf_url: string;
  description?: string;
  finding_type?: FindingType | null;
  location?: string | null;
  instructions?: string | null;
  sku?: string | null;
  created_at?: string;
  updated_at?: string;
  version?: number;
  assigned_to_user_id?: string | null;
  assigned_to_name?: string | null;
  assignment_version?: number;
  acknowledged_at?: string | null;
  resolved_at?: string | null;
  resolution_notes?: string | null;
  capabilities?: TicketCapabilities;
}

export interface TicketEvent {
  event_id: string;
  ticket_id: string;
  type: string;
  actor_id: string;
  actor_name: string;
  at: string;
  note?: string;
  reason?: string | null;
  assignee_id?: string;
}

export interface Technician {
  user_id: string;
  display_name: string;
  active_tickets: number;
}

export interface InboxItem {
  notification_id: string;
  ticket_id: string;
  event_type: string;
  created_at: string;
  read_at: string | null;
  current: boolean;
  title: string;
  severity: Severity | null;
}

export interface Inbox {
  unread: number;
  items: InboxItem[];
}

export interface Persona {
  id: string;
  label: string;
  display_name: string;
  description: string;
  core: boolean;
  active: boolean;
}

export interface PersonaListing {
  enabled: boolean;
  notice: string;
  personas: Persona[];
}

export interface Session {
  user_id: string;
  display_name: string;
  roles: string[];
  permissions: string[];
  csrf_token: string;
  expires_at: string;
  notice: string;
}

export interface Account {
  user_id: string;
  display_name: string;
  roles: string[];
  permissions: string[];
  active: boolean;
}

export interface StockRow {
  sku: string;
  available: number;
  quarantine: number;
  total: number;
  reorder_point: number;
  low_stock: boolean;
}

export interface ShelfCount {
  scan_id: string;
  boxes: number;
  damaged: number;
  counted: number;
  exclude_damaged: boolean;
  low_confidence: number;
  needs_review: boolean;
}

export interface ReconcileResult {
  sku: string;
  previous_available: number;
  change: number;
  available: number;
  quarantine: number;
  counted: number;
}

export interface FindingDetails {
  location?: string;
  instructions?: string;
}

export interface ApproveResult {
  actions: ActionRecord[];
  tickets: Ticket[];
  trace_id: string;
  iterations: number;
  within_iteration_cap: boolean;
  summary: string | null;
  fallback?: string | null;
}

export interface DemoPhoto {
  id: string;
  title: string;
  description: string;
  sku: string | null;
  scan_id: string;
  image_url: string;
  pinned: boolean;
}

export type Inventory = Record<string, { available: number; quarantine: number; reorder_point?: number }>;

export interface ScoreCard {
  value: number | null;
  n: number;
  label: string;
}

export interface Scores {
  pipeline_agreement: ScoreCard;
  agent_score: ScoreCard;
}

export interface Health {
  status: string;
  models: { vision_agent: string; segmentation: string };
  demo_mode?: string;
  demo_sign_in?: boolean;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let csrfToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

/** Called on any 401 from a signed-in request, so an expired or revoked session returns to the sign-in screen. */
export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET";
  const headers = new Headers(init?.headers);
  if (method !== "GET" && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", credentials: "same-origin", ...init, headers });
  if (res.status === 401 && csrfToken && onUnauthorized) onUnauthorized();
  if (!res.ok) {
    let detail: string | undefined;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // Non-JSON error bodies carry nothing worth showing.
    }
    throw new ApiError(`${method} ${path} failed: ${res.status}`, res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/health"),
  scan: (file: File) => {
    const form = new FormData();
    form.append("image", file);
    return request<ScanResult>("/scan", { method: "POST", body: form });
  },
  approve: (scanId: string, findingIds: string[], skus: Record<string, string> = {}, details: Record<string, FindingDetails> = {}) =>
    request<ApproveResult>(`/scan/${scanId}/approve`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ finding_ids: findingIds, skus, details }),
    }),
  tickets: (status?: TicketStatus | "active") => request<Ticket[]>(status ? `/tickets?status=${status}` : "/tickets"),
  ticket: (id: string) => request<Ticket>(`/tickets/${encodeURIComponent(id)}`),
  ticketHistory: (id: string) => request<TicketEvent[]>(`/tickets/${encodeURIComponent(id)}/history`),
  technicians: (ticketId?: string) =>
    request<Technician[]>(ticketId ? `/technicians?ticket_id=${encodeURIComponent(ticketId)}` : "/technicians"),
  assign: (
    id: string,
    body: { assignee_id: string; expected_version?: number; reason?: string; location?: string; instructions?: string },
  ) => post<Ticket>(`/tickets/${encodeURIComponent(id)}/assignment`, body),
  acknowledge: (id: string, expectedVersion?: number) =>
    post<Ticket>(`/tickets/${encodeURIComponent(id)}/acknowledge`, { expected_version: expectedVersion }),
  addNote: (id: string, text: string) => post<TicketEvent>(`/tickets/${encodeURIComponent(id)}/notes`, { text }),
  resolve: (id: string, notes: string, expectedVersion?: number) =>
    post<Ticket>(`/tickets/${encodeURIComponent(id)}/resolve`, { notes, expected_version: expectedVersion }),
  notifications: () => request<Inbox>("/notifications"),
  readNotification: (id: string) => post<{ read_at: string }>(`/notifications/${encodeURIComponent(id)}/read`),
  inventory: () => request<Inventory>("/inventory"),
  stockBoard: () => request<StockRow[]>("/inventory/board"),
  shelfCount: (scanId: string, excludeDamaged = true) =>
    request<ShelfCount>(`/scan/${scanId}/count?exclude_damaged=${excludeDamaged}`),
  reconcile: (scanId: string, sku: string, excludeDamaged: boolean, confirmNeedsReview: boolean) =>
    post<ReconcileResult>(`/scan/${scanId}/reconcile`, {
      sku,
      exclude_damaged: excludeDamaged,
      confirm_needs_review: confirmNeedsReview,
    }),
  personas: () => request<PersonaListing>("/auth/demo-personas"),
  session: () => request<Session>("/auth/session"),
  signIn: (personaId: string) => post<Session>("/auth/demo-sign-in", { persona_id: personaId }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  accounts: () => request<Account[]>("/admin/accounts"),
  setAccountActive: (userId: string, active: boolean) =>
    post<Account>(`/admin/accounts/${encodeURIComponent(userId)}/active`, { active }),
  scores: () => request<Scores>("/scores"),
  demos: () => request<DemoPhoto[]>("/demo"),
  rejectFinding: (scanId: string, findingId: string, reason = "") =>
    request<Record<string, unknown>>(`/scan/${scanId}/feedback`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ finding_id: findingId, action: "reject", reason }),
    }),
  demoPhoto: async (demo: Pick<DemoPhoto, "id" | "image_url">): Promise<File> => {
    const res = await fetch(`${API_URL}${demo.image_url}`, { cache: "no-store", credentials: "same-origin" });
    if (!res.ok) throw new ApiError(`GET ${demo.image_url} failed: ${res.status}`, res.status);
    return new File([await res.arrayBuffer()], `${demo.id}.jpg`, { type: "image/jpeg" });
  },
};

export function pdfUrl(ticket: Pick<Ticket, "pdf_url">): string {
  return `${API_URL}${ticket.pdf_url}`;
}
