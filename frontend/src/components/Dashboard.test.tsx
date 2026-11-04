import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { ApiError, api, type ApproveResult, type ScanResult } from "@/lib/api";
import { makeApprove, makeScan, photo } from "@/test-fixtures";

import { Dashboard } from "./Dashboard";

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => ((resolve = res), (reject = rej)));
  return { promise, resolve, reject };
}

const ticket = (status: string) => ({ ticket_id: `T-${status}`, finding_id: "f", title: "t", severity: "low" as const, assignee_team: "Safety", status, pdf_url: "/tickets/x.pdf" });

let scan: MockInstance<typeof api.scan>;
let approve: MockInstance<typeof api.approve>;
let tickets: MockInstance<typeof api.tickets>;

beforeEach(() => {
  scan = vi.spyOn(api, "scan").mockResolvedValue(makeScan());
  approve = vi.spyOn(api, "approve").mockResolvedValue(makeApprove());
  tickets = vi.spyOn(api, "tickets").mockResolvedValue([]);
  vi.spyOn(api, "inventory").mockResolvedValue({ "SKU-1042": { available: 5, quarantine: 3 }, "SKU-2": { available: 1, quarantine: 2 } });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

async function upload(file = photo()) {
  render(<Dashboard />);
  fireEvent.change(screen.getByTestId("gallery-input"), { target: { files: [file] } });
  return file;
}

const kpi = (label: string) => screen.getByText(label).nextElementSibling?.textContent;

describe("Dashboard: first load", () => {
  it("starts empty with an upload prompt and guidance in both panels", async () => {
    render(<Dashboard />);
    expect(screen.getByText("Upload a warehouse photo to scan")).toBeDefined();
    expect(screen.getByText("No scan yet.")).toBeDefined();
    expect(screen.getByText("Approve findings to dispatch tickets.")).toBeDefined();
    await waitFor(() => expect(kpi("Units quarantined")).toBe("5"));
  });

  it("fills the KPI strip from the backend's tickets and inventory", async () => {
    tickets.mockResolvedValue([ticket("open"), ticket("open"), ticket("closed")]);
    render(<Dashboard />);
    await waitFor(() => expect(kpi("Open tickets")).toBe("2"));
    expect(kpi("Units quarantined")).toBe("5");
    expect(kpi("Scans this session")).toBe("0");
    expect(kpi("Walkway hazards")).toBe("0");
  });

  it("still works when the totals cannot be loaded", async () => {
    tickets.mockRejectedValue(new Error("down"));
    render(<Dashboard />);
    expect(await screen.findByText("Upload a warehouse photo to scan")).toBeDefined();
    expect(kpi("Open tickets")).toBe("0");
  });
});

describe("Dashboard: scanning", () => {
  it("sends the chosen photo and shows overlay, findings and updated KPIs", async () => {
    const file = await upload();
    expect(await screen.findByRole("img", { name: /3 boxes and 1 walkway marking/ })).toBeDefined();
    expect(scan).toHaveBeenCalledWith(file);
    expect(screen.getByText("Damaged box")).toBeDefined();
    expect(kpi("Scans this session")).toBe("1");
    expect(kpi("Walkway hazards")).toBe("1");
    expect(screen.queryByText("No scan yet.")).toBeNull();
  });

  it("shows each stage while waiting and hides the upload buttons", async () => {
    vi.useFakeTimers();
    const pending = deferred<ScanResult>();
    scan.mockReturnValue(pending.promise);
    await upload();
    expect(screen.getByRole("status").textContent).toBe("Uploading photo…");
    expect(screen.queryByRole("button", { name: "Take photo" })).toBeNull();
    await act(async () => void vi.advanceTimersByTime(800));
    expect(screen.getByRole("status").textContent).toBe("Segmenting boxes and walkways…");
    await act(async () => void vi.advanceTimersByTime(4000));
    expect(screen.getByRole("status").textContent).toBe("Inspecting boxes for damage…");
    await act(async () => pending.resolve(makeScan()));
    expect(screen.queryByText("Inspecting boxes for damage…")).toBeNull();
  });

  it("ignores a second photo while a scan is running", async () => {
    const pending = deferred<ScanResult>();
    scan.mockReturnValue(pending.promise);
    render(<Dashboard />);
    const input = screen.getByTestId("gallery-input");
    fireEvent.change(input, { target: { files: [photo("a.jpg")] } });
    fireEvent.change(input, { target: { files: [photo("b.jpg")] } });
    expect(scan).toHaveBeenCalledTimes(1);
    pending.resolve(makeScan());
    await screen.findByText("Damaged box");
  });

  it("says when no freight was found", async () => {
    scan.mockResolvedValue(makeScan({ objects: [], findings: [] }));
    await upload();
    expect(await screen.findByText(/No freight found in this photo/)).toBeDefined();
    expect(screen.getByText("No damage or blocked walkways found.")).toBeDefined();
  });

  it("allows scanning another photo afterwards and replaces the old results", async () => {
    await upload();
    await screen.findByText("Damaged box");
    scan.mockResolvedValue(makeScan({ scan_id: "fedcba9876543210", findings: [] }));
    fireEvent.change(screen.getByTestId("gallery-input"), { target: { files: [photo("next.jpg")] } });
    await screen.findByText("No damage or blocked walkways found.");
    expect(kpi("Scans this session")).toBe("2");
    expect(kpi("Walkway hazards")).toBe("0");
  });
});

describe("Dashboard: scan errors", () => {
  it("shows a friendly message and retries the same photo after a 502", async () => {
    scan.mockRejectedValueOnce(new ApiError("POST /scan failed: 502", 502));
    const file = await upload();
    expect((await screen.findByRole("alert")).textContent).toMatch(/busy or unavailable/);
    expect(screen.getByText("Upload a warehouse photo to scan")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Damaged box")).toBeDefined();
    expect(scan).toHaveBeenCalledTimes(2);
    expect(scan).toHaveBeenLastCalledWith(file);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the server's reason for a rejected photo without a retry button", async () => {
    scan.mockRejectedValue(new ApiError("POST /scan failed: 422", 422, "Image content does not match its declared type."));
    await upload();
    expect((await screen.findByRole("alert")).textContent).toBe("Image content does not match its declared type.");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("explains an unreachable backend", async () => {
    scan.mockRejectedValue(new TypeError("Failed to fetch"));
    await upload();
    expect((await screen.findByRole("alert")).textContent).toMatch(/Can't reach the backend/);
  });
});

describe("Dashboard: approving", () => {
  async function scanned() {
    await upload();
    await screen.findByText("Damaged box");
  }

  it("sends the selected findings and SKU, shows progress, then the timeline", async () => {
    const pending = deferred<ApproveResult>();
    approve.mockReturnValue(pending.promise);
    await scanned();
    fireEvent.change(screen.getByPlaceholderText("e.g. SKU-1042"), { target: { value: "SKU-1042" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve & Dispatch (2)" }));

    expect(approve).toHaveBeenCalledWith("0123456789abcdef", ["dmg_box_0", "hz_box_2"], { dmg_box_0: "SKU-1042" });
    expect(screen.getByText("Agent dispatching…")).toBeDefined();
    expect((screen.getByRole("button", { name: "Dispatching…" }) as HTMLButtonElement).disabled).toBe(true);

    tickets.mockResolvedValue([ticket("open")]);
    await act(async () => pending.resolve(makeApprove()));
    expect(screen.queryByText("Agent dispatching…")).toBeNull();
    const panel = screen.getByRole("heading", { name: "Agent actions" }).closest("section")!;
    expect(within(panel).getByText("Create incident ticket")).toBeDefined();
    expect(within(panel).getByRole("link", { name: /Open ticket T-abc123/ })).toBeDefined();
    await waitFor(() => expect(kpi("Open tickets")).toBe("1"));
  });

  it("cannot be approved twice: a fast double click sends one request and the button locks afterwards", async () => {
    await scanned();
    const button = screen.getByRole("button", { name: /Approve & Dispatch/ });
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findByText("Create incident ticket");
    expect(approve).toHaveBeenCalledTimes(1);
    expect((screen.getByRole("button", { name: /Approve & Dispatch/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows a guard rejection in the timeline", async () => {
    await scanned();
    fireEvent.click(screen.getByRole("button", { name: /Approve & Dispatch/ }));
    expect(await screen.findByText("⛔ Blocked by guard")).toBeDefined();
  });

  it("recovers from a failed approval and retries it with the same selection", async () => {
    approve.mockRejectedValueOnce(new ApiError("POST failed: 502", 502));
    await scanned();
    fireEvent.click(screen.getByRole("checkbox", { name: /Blocked walkway/ }));
    fireEvent.click(screen.getByRole("button", { name: "Approve & Dispatch (1)" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/busy or unavailable/);
    expect((screen.getByRole("button", { name: /Approve & Dispatch/ }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Create incident ticket");
    expect(approve).toHaveBeenLastCalledWith("0123456789abcdef", ["dmg_box_0"], {});
    expect(approve).toHaveBeenCalledTimes(2);
  });
});
