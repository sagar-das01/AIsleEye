import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, type Ticket } from "@/lib/api";
import { makeTicket } from "@/test-sessions";

import { TechnicianWorkspace, TicketBoard } from "./TicketQueues";

const NEW = makeTicket();
const WORKING = makeTicket({
  ticket_id: "T-2",
  title: "Walkway blocked",
  status: "in_progress",
  version: 3,
  capabilities: { assign: false, acknowledge: false, resolve: true, note: true },
});
const DONE = makeTicket({ ticket_id: "T-3", title: "Old job", status: "resolved", resolution_notes: "Fixed", capabilities: { assign: false, acknowledge: false, resolve: false, note: false } });

let tickets: Ticket[];

beforeEach(() => {
  tickets = [NEW, WORKING, DONE];
  vi.spyOn(api, "tickets").mockImplementation(async () => tickets);
  vi.spyOn(api, "ticket").mockImplementation(async (id) => tickets.find((t) => t.ticket_id === id)!);
  vi.spyOn(api, "ticketHistory").mockResolvedValue([
    { event_id: "e1", ticket_id: "T-1", type: "assigned", actor_id: "fm", actor_name: "Fatima", at: new Date().toISOString() },
  ]);
  vi.spyOn(api, "technicians").mockResolvedValue([
    { user_id: "tech-a", display_name: "Tariq (Technician A)", active_tickets: 1 },
    { user_id: "tech-b", display_name: "Bea (Technician B)", active_tickets: 0 },
  ]);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const tab = (name: RegExp) => screen.getByRole("tab", { name });

describe("TechnicianWorkspace", () => {
  it("splits assigned work into New, In progress and History", async () => {
    render(<TechnicianWorkspace />);
    expect(await screen.findByText("Damaged box needs inspection")).toBeDefined();
    expect(tab(/New \(1\)/)).toBeDefined();
    fireEvent.click(tab(/In progress \(1\)/));
    expect(screen.getByText("Walkway blocked")).toBeDefined();
    fireEvent.click(tab(/History \(1\)/));
    expect(screen.getByText("Old job")).toBeDefined();
  });

  it("acknowledges with the loaded version and only shows the server-confirmed state", async () => {
    const acknowledge = vi.spyOn(api, "acknowledge").mockImplementation(async () => {
      tickets = [{ ...NEW, status: "in_progress", capabilities: { ...WORKING.capabilities! } }, WORKING, DONE];
      return tickets[0];
    });
    render(<TechnicianWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: /Damaged box needs inspection/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Acknowledge and start" }));
    await waitFor(() => expect(acknowledge).toHaveBeenCalledWith("T-1", 2));
    expect(await screen.findByRole("button", { name: "Resolve ticket" })).toBeDefined();
  });

  it("requires completion notes and keeps them after a failed resolve", async () => {
    vi.spyOn(api, "resolve").mockRejectedValue(new ApiError("409", 409, "This ticket changed since you loaded it."));
    render(<TechnicianWorkspace />);
    await screen.findByText("Damaged box needs inspection");
    fireEvent.click(tab(/In progress/));
    fireEvent.click(screen.getByRole("button", { name: /Walkway blocked/ }));
    const resolve = await screen.findByRole("button", { name: "Resolve ticket" });
    expect(resolve).toHaveProperty("disabled", true);
    const notes = screen.getByLabelText("Completion notes (required)");
    fireEvent.change(notes, { target: { value: "Moved the pallet." } });
    fireEvent.click(resolve);
    expect((await screen.findByRole("alert")).textContent).toMatch(/changed since you loaded it/);
    expect(within(screen.getByRole("alert")).getByRole("button", { name: "Refresh" })).toBeDefined();
    expect((notes as HTMLTextAreaElement).value).toBe("Moved the pallet.");
  });

  it("shows empty and error states", async () => {
    tickets = [];
    render(<TechnicianWorkspace />);
    expect(await screen.findByText("No new tickets assigned to you.")).toBeDefined();
    cleanup();
    vi.mocked(api.tickets).mockRejectedValue(new ApiError("503", 503));
    render(<TechnicianWorkspace />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/unavailable/);
  });

  it("opens the ticket picked from a notification", async () => {
    render(<TechnicianWorkspace selectedId="T-2" />);
    expect(await screen.findByRole("button", { name: "Resolve ticket" })).toBeDefined();
    expect(tab(/In progress/).getAttribute("aria-selected")).toBe("true");
  });
});

describe("TicketBoard", () => {
  const UNASSIGNED = makeTicket({
    ticket_id: "T-9", title: "Needs an owner", assigned_to_user_id: null, assigned_to_name: null, location: null, instructions: null,
    capabilities: { assign: true, acknowledge: false, resolve: false, note: true },
  });

  it("assigns an unassigned ticket only once location and instructions are filled", async () => {
    tickets = [UNASSIGNED];
    const assign = vi.spyOn(api, "assign").mockResolvedValue(UNASSIGNED);
    render(<TicketBoard />);
    expect(tab(/Unassigned/)).toBeDefined();
    fireEvent.click(await screen.findByRole("button", { name: /Needs an owner/ }));
    const form = await screen.findByRole("form", { name: "Assign ticket" });
    await waitFor(() => expect(within(form).getAllByRole("option")).toHaveLength(3));
    fireEvent.change(within(form).getByLabelText("Technician"), { target: { value: "tech-b" } });
    const submit = within(form).getByRole("button", { name: "Assign" });
    expect(submit).toHaveProperty("disabled", true);
    fireEvent.change(within(form).getByLabelText(/Location/), { target: { value: "Aisle 2" } });
    fireEvent.change(within(form).getByLabelText(/Instructions/), { target: { value: "Clear the walkway" } });
    fireEvent.click(submit);
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith("T-9", {
        assignee_id: "tech-b", expected_version: 2, location: "Aisle 2", instructions: "Clear the walkway", reason: undefined,
      }),
    );
  });

  it("requires a reason to reassign and hides the current assignee", async () => {
    tickets = [makeTicket({ capabilities: { assign: true, acknowledge: false, resolve: false, note: true } })];
    render(<TicketBoard />);
    fireEvent.click(tab(/Awaiting ack/));
    fireEvent.click(await screen.findByRole("button", { name: /Damaged box/ }));
    const form = await screen.findByRole("form", { name: "Reassign ticket" });
    await waitFor(() => expect(within(form).getAllByRole("option")).toHaveLength(2));
    expect(within(form).queryByRole("option", { name: /Tariq/ })).toBeNull();
    expect(within(form).getByLabelText("Reason for reassigning")).toBeDefined();
  });
});
