import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { makeScan } from "@/test-fixtures";

import { FindingsList } from "./FindingsList";

afterEach(cleanup);

const inventory = { "SKU-1042": { available: 5, quarantine: 0 } };

function setup(props: Partial<Parameters<typeof FindingsList>[0]> = {}) {
  const onApprove = vi.fn();
  render(<FindingsList findings={makeScan().findings} inventory={inventory} pending={false} disabled={false} onApprove={onApprove} {...props} />);
  return onApprove;
}

describe("FindingsList", () => {
  it("shows type, severity with an icon, and confidence for each finding", () => {
    setup();
    expect(screen.getByText("Damaged box")).toBeDefined();
    expect(screen.getByText("▲▲ high")).toBeDefined();
    expect(screen.getByText("92% confident")).toBeDefined();
    expect(screen.getByText("Blocked walkway")).toBeDefined();
    expect(screen.getByText("▲ medium")).toBeDefined();
  });

  it("selects every finding by default and sends all of them on approve", () => {
    const onApprove = setup();
    fireEvent.click(screen.getByRole("button", { name: "Approve & Dispatch (2)" }));
    expect(onApprove).toHaveBeenCalledWith(["dmg_box_0", "hz_box_2"], {}, {});
  });

  it("excludes an unticked finding and updates the count", () => {
    const onApprove = setup();
    fireEvent.click(screen.getByRole("checkbox", { name: /Blocked walkway hz_box_2/ }));
    fireEvent.click(screen.getByRole("button", { name: "Approve & Dispatch (1)" }));
    expect(onApprove).toHaveBeenCalledWith(["dmg_box_0"], {}, {});
  });

  it("disables approve when nothing is selected", () => {
    const onApprove = setup();
    fireEvent.click(screen.getByRole("checkbox", { name: /Damaged box dmg_box_0/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Blocked walkway hz_box_2/ }));
    const button = screen.getByRole("button", { name: "Approve & Dispatch (0)" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(onApprove).not.toHaveBeenCalled();
  });

  it("sends the SKU the supervisor typed for a damaged box, trimmed, and none for hazards", () => {
    const onApprove = setup();
    fireEvent.change(screen.getByPlaceholderText("e.g. SKU-1042"), { target: { value: "  SKU-1042 " } });
    fireEvent.click(screen.getByRole("button", { name: /Approve & Dispatch/ }));
    expect(onApprove).toHaveBeenCalledWith(["dmg_box_0", "hz_box_2"], { dmg_box_0: "SKU-1042" }, {});
  });

  it("only asks for a SKU on damaged boxes and suggests known SKUs", () => {
    setup();
    expect(screen.getAllByPlaceholderText("e.g. SKU-1042")).toHaveLength(1);
    expect(document.querySelectorAll("#sku-options option")).toHaveLength(1);
  });

  it("drops the SKU of a finding that was unticked", () => {
    const onApprove = setup();
    fireEvent.change(screen.getByPlaceholderText("e.g. SKU-1042"), { target: { value: "SKU-1042" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Damaged box dmg_box_0/ }));
    fireEvent.click(screen.getByRole("button", { name: /Approve & Dispatch/ }));
    expect(onApprove).toHaveBeenCalledWith(["hz_box_2"], {}, {});
  });

  it("locks everything while a request is pending and says so", () => {
    setup({ pending: true });
    const button = screen.getByRole("button", { name: "Dispatching…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    for (const box of screen.getAllByRole("checkbox")) expect((box as HTMLInputElement).disabled).toBe(true);
  });

  it("cannot be submitted again after dispatch (disabled)", () => {
    const onApprove = setup({ disabled: true });
    fireEvent.click(screen.getByRole("button", { name: /Approve & Dispatch/ }));
    expect(onApprove).not.toHaveBeenCalled();
  });

  it("flags findings that need review, with the reason", () => {
    const findings = makeScan().findings.map((f, i) => (i === 0 ? { ...f, needs_review: true, review_reason: "Confidence is below 60%." } : f));
    setup({ findings });
    expect(screen.getByText("⚑ Needs review")).toBeDefined();
    expect(screen.getByText("Confidence is below 60%.")).toBeDefined();
  });

  it("rejects a finding: reports it, excludes it from the approval and locks its checkbox", () => {
    const onReject = vi.fn();
    const onApprove = setup({ onReject });
    const first = makeScan().findings[0];
    fireEvent.click(screen.getAllByRole("button", { name: /Reject: not a real issue/ })[0]);
    expect(onReject).toHaveBeenCalledExactlyOnceWith(first.id);
    expect(screen.getByText(/Rejected: not a real issue/)).toBeDefined();
    expect((screen.getByLabelText(new RegExp(`Include .* ${first.id}`)) as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Approve & Dispatch/ }));
    expect(onApprove.mock.calls[0][0]).not.toContain(first.id);
  });

  it("offers no reject button when rejection is not wired up, and disables it while locked", () => {
    setup();
    expect(screen.queryByRole("button", { name: /Reject/ })).toBeNull();
    cleanup();
    setup({ onReject: vi.fn(), disabled: true });
    for (const b of screen.getAllByRole("button", { name: /Reject/ })) expect((b as HTMLButtonElement).disabled).toBe(true);
  });

  it("says so when there is nothing to approve", () => {
    setup({ findings: [] });
    expect(screen.getByText("No damage or blocked walkways found.")).toBeDefined();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("prefills the suggested SKU on damaged boxes only and sends it on approve", () => {
    const onApprove = setup({ defaultSku: "SKU-1042" });
    expect((screen.getByPlaceholderText("e.g. SKU-1042") as HTMLInputElement).value).toBe("SKU-1042");
    fireEvent.click(screen.getByRole("button", { name: /Approve & Dispatch/ }));
    expect(onApprove).toHaveBeenCalledWith(["dmg_box_0", "hz_box_2"], { dmg_box_0: "SKU-1042" }, {});
  });

  it("lets the supervisor clear a suggested SKU", () => {
    const onApprove = setup({ defaultSku: "SKU-1042" });
    fireEvent.change(screen.getByPlaceholderText("e.g. SKU-1042"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /Approve & Dispatch/ }));
    expect(onApprove).toHaveBeenCalledWith(["dmg_box_0", "hz_box_2"], {}, {});
  });

  it("labels findings as AI proposals that need approval", () => {
    setup();
    expect(screen.getByText(/AI-proposed findings\. Nothing is dispatched until you approve/)).toBeDefined();
  });

  it("previews the stock move for a damaged box with a known SKU, using the guard's one-unit rule", () => {
    setup({ defaultSku: "SKU-1042" });
    expect(screen.getByLabelText("Stock preview for dmg_box_0").textContent).toBe(
      "On approval: SKU-1042 −1 available (5 → 4), +1 quarantine",
    );
    fireEvent.change(screen.getByPlaceholderText("e.g. SKU-1042"), { target: { value: "SKU-UNKNOWN" } });
    expect(screen.queryByLabelText("Stock preview for dmg_box_0")).toBeNull();
  });

  it("sends trimmed supervisor location and instructions for selected findings only", () => {
    const onApprove = setup();
    const [location] = screen.getAllByLabelText(/Location \(aisle/);
    const [instructions] = screen.getAllByLabelText("Instructions");
    fireEvent.change(location, { target: { value: "  Aisle 4  " } });
    fireEvent.change(instructions, { target: { value: "Rewrap the pallet" } });
    fireEvent.click(screen.getByRole("button", { name: /Approve & Dispatch/ }));
    expect(onApprove).toHaveBeenCalledWith(["dmg_box_0", "hz_box_2"], {}, {
      dmg_box_0: { location: "Aisle 4", instructions: "Rewrap the pallet" },
    });
  });
});
