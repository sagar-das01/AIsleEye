import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, type ShelfCount } from "@/lib/api";

import { NotificationBell, POLL_MS } from "./NotificationBell";
import { ShelfCountCard } from "./ShelfCountCard";
import { StockBoard } from "./StockBoard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("StockBoard", () => {
  it("lists stock with a text reorder badge, not colour alone", async () => {
    vi.spyOn(api, "stockBoard").mockResolvedValue([
      { sku: "SKU-1042", available: 22, quarantine: 1, total: 23, reorder_point: 24, low_stock: true },
      { sku: "SKU-3310", available: 60, quarantine: 0, total: 60, reorder_point: 20, low_stock: false },
    ]);
    render(<StockBoard />);
    expect(await screen.findByText("⚠ Reorder (below 24)")).toBeDefined();
    expect(screen.getByText("✓ OK")).toBeDefined();
    expect(screen.getByText("Quarantine 1")).toBeDefined();
  });

  it("has empty and error states and reloads on change", async () => {
    const board = vi.spyOn(api, "stockBoard").mockResolvedValue([]);
    const { rerender } = render(<StockBoard refreshKey={0} />);
    expect(await screen.findByText("No inventory recorded yet.")).toBeDefined();
    board.mockRejectedValue(new TypeError("Failed to fetch"));
    rerender(<StockBoard refreshKey={1} />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/Couldn't load stock/);
  });
});

const COUNT: ShelfCount = {
  scan_id: "0123456789abcdef", boxes: 23, damaged: 1, counted: 22, exclude_damaged: true, low_confidence: 0, needs_review: false,
};
const INVENTORY = { "SKU-1042": { available: 25, quarantine: 0 } };

describe("ShelfCountCard", () => {
  it("shows the discrepancy and reconciles once on request", async () => {
    vi.spyOn(api, "shelfCount").mockResolvedValue(COUNT);
    const reconcile = vi.spyOn(api, "reconcile").mockResolvedValue({
      sku: "SKU-1042", previous_available: 25, change: -3, available: 22, quarantine: 0, counted: 22,
    });
    const onReconciled = vi.fn();
    render(<ShelfCountCard scanId={COUNT.scan_id} inventory={INVENTORY} defaultSku="SKU-1042" canReconcile onReconciled={onReconciled} />);
    expect((await screen.findByLabelText("Count versus system stock")).textContent).toBe(
      "Counted 22 · System 25 available · −3 · 1 damaged not counted",
    );
    const button = screen.getByRole("button", { name: "Adjust SKU-1042 to counted (22)" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect((await screen.findByRole("status")).textContent).toMatch(/set to 22 available \(-3\)/);
    expect(reconcile).toHaveBeenCalledTimes(1);
    expect(reconcile).toHaveBeenCalledWith(COUNT.scan_id, "SKU-1042", true, false);
    expect(onReconciled).toHaveBeenCalled();
  });

  it("flags a low-confidence count and requires an in-person check before adjusting", async () => {
    vi.spyOn(api, "shelfCount").mockResolvedValue({ ...COUNT, needs_review: true, low_confidence: 2 });
    const reconcile = vi.spyOn(api, "reconcile").mockRejectedValue(new ApiError("422", 422, "exceeds the 20-unit reconcile limit"));
    render(<ShelfCountCard scanId={COUNT.scan_id} inventory={INVENTORY} defaultSku="SKU-1042" canReconcile />);
    expect(await screen.findByText(/Needs review: 2 low-confidence/)).toBeDefined();
    const button = screen.getByRole("button", { name: /Adjust SKU-1042/ });
    expect(button).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByLabelText("I checked the count in person"));
    fireEvent.click(button);
    expect((await screen.findByRole("alert")).textContent).toMatch(/reconcile limit/);
    expect(reconcile).toHaveBeenCalledWith(COUNT.scan_id, "SKU-1042", true, true);
  });

  it("is read-only without the reconcile permission", async () => {
    vi.spyOn(api, "shelfCount").mockResolvedValue(COUNT);
    render(<ShelfCountCard scanId={COUNT.scan_id} inventory={INVENTORY} defaultSku="SKU-1042" canReconcile={false} />);
    await screen.findByLabelText("Count versus system stock");
    expect(screen.queryByRole("button", { name: /Adjust/ })).toBeNull();
  });

  it("recounts including damaged boxes when asked", async () => {
    const count = vi.spyOn(api, "shelfCount").mockResolvedValue(COUNT);
    render(<ShelfCountCard scanId={COUNT.scan_id} inventory={INVENTORY} canReconcile />);
    await screen.findByLabelText("Count versus system stock");
    fireEvent.click(screen.getByLabelText("Leave damaged boxes out of the count"));
    await waitFor(() => expect(count).toHaveBeenLastCalledWith(COUNT.scan_id, false));
  });
});

describe("NotificationBell", () => {
  const ITEM = {
    notification_id: "N-1", ticket_id: "T-1", event_type: "assigned", created_at: "2026-01-01T00:00:00Z",
    read_at: null, current: true, title: "Damaged box", severity: "high" as const,
  };

  it("shows the unread count, marks read on open and opens the ticket", async () => {
    vi.spyOn(api, "notifications").mockResolvedValue({ unread: 1, items: [ITEM] });
    const read = vi.spyOn(api, "readNotification").mockResolvedValue({ read_at: "now" });
    const onOpen = vi.fn();
    render(<NotificationBell onOpenTicket={onOpen} />);
    const bell = await screen.findByRole("button", { name: "Notifications, 1 unread" });
    fireEvent.click(bell);
    fireEvent.click(screen.getByRole("button", { name: /New assignment/ }));
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith("T-1"));
    expect(read).toHaveBeenCalledWith("N-1");
  });

  it("does not open tickets from obsolete notifications", async () => {
    vi.spyOn(api, "notifications").mockResolvedValue({
      unread: 1, items: [{ ...ITEM, current: false, title: "This ticket is no longer assigned to you.", severity: null }],
    });
    vi.spyOn(api, "readNotification").mockResolvedValue({ read_at: "now" });
    const onOpen = vi.fn();
    render(<NotificationBell onOpenTicket={onOpen} />);
    fireEvent.click(await screen.findByRole("button", { name: "Notifications, 1 unread" }));
    fireEvent.click(screen.getByRole("button", { name: /no longer assigned/ }));
    await waitFor(() => expect(api.readNotification).toHaveBeenCalled());
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("polls in the foreground and refreshes on refocus", async () => {
    vi.useFakeTimers();
    const inbox = vi.spyOn(api, "notifications").mockResolvedValue({ unread: 0, items: [] });
    render(<NotificationBell />);
    expect(inbox).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTime(POLL_MS));
    expect(inbox).toHaveBeenCalledTimes(2);
    await act(async () => window.dispatchEvent(new Event("focus")));
    expect(inbox).toHaveBeenCalledTimes(3);
  });
});
