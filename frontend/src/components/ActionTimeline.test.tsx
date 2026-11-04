import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { API_URL } from "@/lib/api";
import { makeApprove } from "@/test-fixtures";

import { ActionTimeline } from "./ActionTimeline";

afterEach(cleanup);

describe("ActionTimeline", () => {
  it("lists every action with a readable tool name and an argument summary", () => {
    render(<ActionTimeline result={makeApprove()} />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText("Create incident ticket")).toBeDefined();
    expect(within(items[0]).getByText("title: Crushed corner · severity: high")).toBeDefined();
    expect(within(items[1]).getByText("Quarantine inventory")).toBeDefined();
  });

  it("links the ticket PDF on the API host, opened in a new tab", () => {
    render(<ActionTimeline result={makeApprove()} />);
    const link = screen.getByRole("link", { name: /Open ticket T-abc123/ });
    expect(link.getAttribute("href")).toBe(`${API_URL}/tickets/T-abc123.pdf`);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noreferrer");
  });

  it("shows a guard rejection with its status label and reason", () => {
    render(<ActionTimeline result={makeApprove()} />);
    const rejected = screen.getAllByRole("listitem")[1];
    expect(within(rejected).getByText("⛔ Blocked by guard")).toBeDefined();
    expect(within(rejected).getByText(/exceeds the 1 unit/)).toBeDefined();
  });

  it("labels every status with an icon and words", () => {
    const base = makeApprove().actions[0];
    const actions = (["executed", "rejected", "invalid", "failed"] as const).map((status) => ({ ...base, status, reason: status, result: null }));
    render(<ActionTimeline result={makeApprove({ actions, tickets: [] })} />);
    for (const label of ["✓ Done", "⛔ Blocked by guard", "⚠ Invalid request", "✕ Failed"]) expect(screen.getByText(label)).toBeDefined();
  });

  it("shows the agent's summary", () => {
    render(<ActionTimeline result={makeApprove()} />);
    expect(screen.getByText("Created ticket T-abc123.")).toBeDefined();
  });

  it("says when the agent did nothing", () => {
    render(<ActionTimeline result={makeApprove({ actions: [], tickets: [], summary: null })} />);
    expect(screen.getByText("The agent took no actions.")).toBeDefined();
  });

  it("warns when the agent hit its step limit", () => {
    render(<ActionTimeline result={makeApprove({ within_iteration_cap: false })} />);
    expect(screen.getByRole("status").textContent).toMatch(/step limit/);
  });

  it("does not link anything for an action without a ticket", () => {
    render(<ActionTimeline result={makeApprove({ tickets: [] })} />);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("says when the built-in rules dispatched instead of the AI agent", () => {
    render(<ActionTimeline result={makeApprove({ fallback: "rules" })} />);
    expect(screen.getByRole("status").textContent).toMatch(/AI agent was unavailable/);
  });

  it("stays quiet about fallbacks otherwise", () => {
    render(<ActionTimeline result={makeApprove({ fallback: null })} />);
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("ActionTimeline: replayed approvals", () => {
  it("says plainly that nothing new was created and shows the tickets' current status", () => {
    render(
      <ActionTimeline
        result={makeApprove({
          already_dispatched: true,
          approved_at: "2026-11-05T00:50:00+05:30",
          tickets: [{ ...makeApprove().tickets[0], status: "in_progress" }],
        })}
      />,
    );
    const notice = screen.getByRole("status", { name: "Already dispatched" });
    expect(notice.textContent).toMatch(/already dispatched at [\s\S]*no new\s+tickets were created/);
    expect(notice.textContent).toMatch(/T-abc123 · ◐ In progress/);
  });

  it("shows no notice for a fresh dispatch", () => {
    render(<ActionTimeline result={makeApprove({ already_dispatched: false })} />);
    expect(screen.queryByRole("status", { name: "Already dispatched" })).toBeNull();
  });
});

