import type { ApproveResult, ScanResult } from "@/lib/api";

const square = (x: number, y: number, s: number) => `M${x} ${y}L${x + s} ${y}L${x + s} ${y + s}L${x} ${y + s}Z`;

export function makeScan(overrides: Partial<ScanResult> = {}): ScanResult {
  return {
    scan_id: "0123456789abcdef",
    image_size: { width: 400, height: 300 },
    objects: [
      { id: "box_0", label: "box", bbox: { x1: 20, y1: 40, x2: 120, y2: 140 }, confidence: 0.9, mask_svg: square(20, 40, 100) },
      { id: "box_1", label: "box", bbox: { x1: 200, y1: 40, x2: 300, y2: 140 }, confidence: 0.85, mask_svg: square(200, 40, 100) },
      { id: "box_2", label: "box", bbox: { x1: 20, y1: 160, x2: 120, y2: 260 }, confidence: 0.8, mask_svg: square(20, 160, 100) },
      { id: "walkway_0", label: "walkway", bbox: { x1: 0, y1: 200, x2: 400, y2: 230 }, confidence: 0.9, mask_svg: "M0 200L400 200L400 230L0 230Z" },
    ],
    findings: [
      { id: "dmg_box_0", type: "box_damage", severity: "high", confidence: 0.92, bbox: { x1: 20, y1: 40, x2: 120, y2: 140 }, description: "Box box_0: crushed corner." },
      { id: "hz_box_2", type: "walkway_blocked", severity: "medium", confidence: 0.7, bbox: { x1: 20, y1: 160, x2: 120, y2: 260 }, description: "Box box_2 sits on a marked walkway (40% of its area)." },
    ],
    timings_ms: { segment: 3000, inspect: 4000, hazards: 2 },
    ...overrides,
  };
}

export function makeApprove(overrides: Partial<ApproveResult> = {}): ApproveResult {
  return {
    actions: [
      {
        tool: "create_incident_ticket",
        args: { finding_id: "dmg_box_0", title: "Crushed corner", severity: "high" },
        status: "executed",
        reason: null,
        result: { ticket_id: "T-abc123", pdf_url: "/tickets/T-abc123.pdf" },
      },
      {
        tool: "adjust_inventory",
        args: { finding_id: "dmg_box_0", sku: "SKU-1042", delta: 99 },
        status: "rejected",
        reason: "Quantity exceeds the 1 unit(s) detected for dmg_box_0.",
        result: null,
      },
    ],
    tickets: [
      { ticket_id: "T-abc123", finding_id: "dmg_box_0", title: "Crushed corner", severity: "high", assignee_team: "Maintenance", status: "open", pdf_url: "/tickets/T-abc123.pdf" },
    ],
    trace_id: "a".repeat(32),
    iterations: 2,
    within_iteration_cap: true,
    summary: "Created ticket T-abc123.",
    ...overrides,
  };
}

export function photo(name = "shelf.jpg", type = "image/jpeg", size = 1024): File {
  return new File([new Uint8Array(size)], name, { type });
}
