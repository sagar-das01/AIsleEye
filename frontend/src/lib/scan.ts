import { ApiError, type DetectedObject, type Finding } from "./api";

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const ACCEPTED_TYPES = ["image/jpeg", "image/png", "image/webp"];

export interface BoxStatus {
  damage: Finding | null;
  blocked: Finding | null;
}

/** The detected box a finding belongs to: explicit for supervisor-added findings, else encoded in the model's id. */
export function objectIdOf(finding: Finding): string {
  return finding.object_id ?? finding.id.replace(/^(dmg|hz)_/, "");
}

export function boxStatus(object: DetectedObject, findings: Finding[]): BoxStatus {
  // Uses the reviewed type, so a supervisor's correction or rejection is what the overlay shows.
  const live = findings.filter((f) => objectIdOf(f) === object.id && f.review_status !== "rejected");
  return {
    damage: live.find((f) => f.type === "box_damage") ?? null,
    blocked: live.find((f) => f.type === "walkway_blocked") ?? null,
  };
}

/** Stable, human-friendly box numbers ("#1", "#2", …) in detection order, shared by the overlay and the review form. */
export function boxNumbers(objects: DetectedObject[]): Map<string, number> {
  return new Map(objects.filter((o) => o.label === "box").map((o, i) => [o.id, i + 1]));
}

export function validateUpload(file: File): string | null {
  if (!ACCEPTED_TYPES.includes(file.type)) return "Choose a JPEG, PNG or WebP photo.";
  if (file.size > MAX_UPLOAD_BYTES) return "That photo is over 10 MB. Choose a smaller one.";
  if (file.size === 0) return "That file is empty.";
  return null;
}

export interface FriendlyError {
  message: string;
  retryable: boolean;
}

export function describeError(error: unknown): FriendlyError {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return { message: "Your session has ended. Sign in again to continue.", retryable: false };
    }
    if (error.status === 403) {
      return { message: error.detail ?? "Your role does not allow this action.", retryable: false };
    }
    if (error.status === 409) {
      return { message: error.detail ?? "This changed since you loaded it. Refresh and try again.", retryable: false };
    }
    if (error.status === 504) {
      return { message: "The analysis took too long. Please try again, or use a smaller photo.", retryable: true };
    }
    if ([502, 503].includes(error.status)) {
      return { message: "The analysis service is busy or unavailable. Please try again in a moment.", retryable: true };
    }
    if ([413, 415, 422].includes(error.status)) {
      return { message: error.detail ?? "That photo could not be used.", retryable: false };
    }
    if (error.status === 404) {
      return { message: error.detail ?? "Nothing was found for that request.", retryable: false };
    }
    return { message: "Something went wrong on the server. Please try again.", retryable: true };
  }
  return { message: "Can't reach the backend. Check that it is running, then try again.", retryable: true };
}

export function summarizeArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .filter(([key]) => key !== "finding_id")
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" · ");
}

export const SEVERITY_ICON = { low: "▽", medium: "▲", high: "▲▲" } as const;
