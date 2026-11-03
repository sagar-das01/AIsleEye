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
  sku?: string;
}

export interface Health {
  status: string;
  models: { vision_agent: string; segmentation: string };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    throw new Error(`${init?.method ?? "GET"} ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/health"),
};
