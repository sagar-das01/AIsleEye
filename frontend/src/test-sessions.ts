import type { Session, Ticket } from "@/lib/api";

export function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    user_id: "floor-manager",
    display_name: "Fatima (Floor Manager)",
    roles: ["floor_manager"],
    permissions: ["scan", "review", "approve", "tickets", "assign", "inventory", "reconcile", "compliance", "scores"],
    csrf_token: "csrf",
    expires_at: "2099-01-01T00:00:00+00:00",
    notice: "Demo sign-in: identities are not verified.",
    ...overrides,
  };
}

export const TECH = makeSession({
  user_id: "tech-a",
  display_name: "Tariq (Technician A)",
  roles: ["technician"],
  permissions: ["tickets", "work"],
});

export function makeTicket(overrides: Partial<Ticket> = {}): Ticket {
  return {
    ticket_id: "T-1",
    finding_id: "dmg_box_0",
    title: "Damaged box needs inspection",
    severity: "high",
    assignee_team: "Maintenance",
    status: "open",
    pdf_url: "/tickets/T-1.pdf",
    description: "Crushed corner",
    location: "Aisle 4",
    instructions: "Rewrap",
    created_at: new Date().toISOString(),
    version: 2,
    assigned_to_user_id: "tech-a",
    assigned_to_name: "Tariq (Technician A)",
    capabilities: { assign: false, acknowledge: true, resolve: false, note: true },
    ...overrides,
  };
}
