export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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

export interface Ticket {
  ticket_id: string;
  finding_id: string;
  title: string;
  severity: Severity;
  assignee_team: string;
  status: string;
  pdf_url: string;
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

export type Inventory = Record<string, { available: number; quarantine: number }>;

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    let detail: string | undefined;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // Non-JSON error bodies carry nothing worth showing.
    }
    throw new ApiError(`${init?.method ?? "GET"} ${path} failed: ${res.status}`, res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/health"),
  scan: (file: File) => {
    const form = new FormData();
    form.append("image", file);
    return request<ScanResult>("/scan", { method: "POST", body: form });
  },
  approve: (scanId: string, findingIds: string[], skus: Record<string, string> = {}) =>
    request<ApproveResult>(`/scan/${scanId}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ finding_ids: findingIds, skus }),
    }),
  tickets: () => request<Ticket[]>("/tickets"),
  inventory: () => request<Inventory>("/inventory"),
  scores: () => request<Scores>("/scores"),
  demos: () => request<DemoPhoto[]>("/demo"),
  rejectFinding: (scanId: string, findingId: string, reason = "") =>
    request<Record<string, unknown>>(`/scan/${scanId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ finding_id: findingId, action: "reject", reason }),
    }),
  demoPhoto: async (demo: Pick<DemoPhoto, "id" | "image_url">): Promise<File> => {
    const res = await fetch(`${API_URL}${demo.image_url}`, { cache: "no-store" });
    if (!res.ok) throw new ApiError(`GET ${demo.image_url} failed: ${res.status}`, res.status);
    return new File([await res.arrayBuffer()], `${demo.id}.jpg`, { type: "image/jpeg" });
  },
};

export function pdfUrl(ticket: Pick<Ticket, "pdf_url">): string {
  return `${API_URL}${ticket.pdf_url}`;
}
