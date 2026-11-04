import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type Session } from "@/lib/api";
import { SessionContext } from "@/lib/session";
import { makeSession, TECH } from "@/test-sessions";

import { Workspace } from "./Workspace";

beforeEach(() => {
  vi.spyOn(api, "notifications").mockResolvedValue({ unread: 0, items: [] });
  vi.spyOn(api, "tickets").mockResolvedValue([]);
  vi.spyOn(api, "inventory").mockResolvedValue({});
  vi.spyOn(api, "scores").mockResolvedValue({
    pipeline_agreement: { value: null, n: 0, label: "n/a (0 so far)" },
    agent_score: { value: null, n: 0, label: "n/a (0 so far)" },
  });
  vi.spyOn(api, "demos").mockResolvedValue([]);
  vi.spyOn(api, "health").mockResolvedValue({ status: "ok", models: { vision_agent: "m", segmentation: "s" } });
  vi.spyOn(api, "stockBoard").mockResolvedValue([]);
  vi.spyOn(api, "accounts").mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderAs(session: Session, actions = { switchRole: vi.fn(), signOut: vi.fn() }) {
  render(
    <SessionContext.Provider value={{ session, can: (p) => session.permissions.includes(p), ...actions }}>
      <Workspace />
    </SessionContext.Provider>,
  );
  return actions;
}

const tabs = () => screen.queryAllByRole("tab").map((t) => t.textContent);

describe("Workspace", () => {
  it("gives technicians only their own ticket workspace", async () => {
    renderAs(TECH);
    expect(await screen.findByRole("heading", { name: "My tickets" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "Take photo" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Stock" })).toBeNull();
    expect(api.inventory).not.toHaveBeenCalled();
    expect(api.scores).not.toHaveBeenCalled();
  });

  it("gives floor managers scan, tickets and stock", async () => {
    renderAs(makeSession());
    expect(tabs()).toEqual(["Scan", "Tickets", "Stock"]);
    expect(screen.getByRole("button", { name: "Take photo" })).toBeDefined();
    fireEvent.click(screen.getByRole("tab", { name: "Tickets" }));
    expect(await screen.findByRole("tab", { name: /Unassigned/ })).toBeDefined();
    fireEvent.click(screen.getByRole("tab", { name: "Stock" }));
    expect(await screen.findByText("No inventory recorded yet.")).toBeDefined();
  });

  it("adds account administration for admins", async () => {
    renderAs(makeSession({ user_id: "admin", display_name: "Avery (Admin)", permissions: ["scan", "tickets", "assign", "inventory", "users", "work"] }));
    expect(tabs()).toEqual(["Scan", "Tickets", "Stock", "Accounts"]);
    fireEvent.click(screen.getByRole("tab", { name: "Accounts" }));
    expect(await screen.findByRole("heading", { name: "Demo accounts" })).toBeDefined();
  });

  it("explains when a role has no operational screens", () => {
    renderAs(makeSession({ user_id: "reporter", display_name: "Rui", permissions: [] }));
    expect(screen.getByText(/no operational screens/)).toBeDefined();
  });

  it("shows score cards to viewers", async () => {
    renderAs(makeSession({ user_id: "viewer", display_name: "Vik", permissions: ["scores"] }));
    expect(await screen.findByText("Pipeline agreement")).toBeDefined();
  });

  it("labels the demo identity and offers switch and sign out", () => {
    const actions = renderAs(TECH);
    expect(screen.getByText("DEMO · unverified")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Switch role" }));
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(actions.switchRole).toHaveBeenCalled();
    expect(actions.signOut).toHaveBeenCalled();
  });
});
