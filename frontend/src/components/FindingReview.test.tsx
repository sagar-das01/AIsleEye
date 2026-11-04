import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type Finding } from "@/lib/api";
import { boxStatus } from "@/lib/scan";
import { makeScan } from "@/test-fixtures";

import { FindingsList } from "./FindingsList";
import { ScanOverlay } from "./ScanOverlay";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const scan = makeScan();

function setup(findings: Finding[] = scan.findings, overrides: Partial<NonNullable<Parameters<typeof FindingsList>[0]["review"]>> = {}) {
  const review = {
    boxes: scan.objects,
    onCorrect: vi.fn().mockResolvedValue(undefined),
    onAdd: vi.fn().mockResolvedValue(undefined),
    onUndo: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  const onApprove = vi.fn();
  render(<FindingsList findings={findings} inventory={{}} pending={false} disabled={false} onApprove={onApprove} review={review} />);
  return { review, onApprove };
}

describe("Correcting findings", () => {
  it("edits type, severity and description and sends them with a reason", async () => {
    const { review } = setup();
    fireEvent.click(screen.getAllByRole("button", { name: /Correct this finding/ })[0]);
    const form = screen.getByRole("form", { name: /Correct damaged box dmg_box_0/ });
    fireEvent.change(within(form).getByLabelText("Finding type"), { target: { value: "walkway_blocked" } });
    fireEvent.change(within(form).getByLabelText("Severity"), { target: { value: "low" } });
    fireEvent.change(within(form).getByLabelText("What is actually wrong"), { target: { value: "  Sits on the lane, not damaged. " } });
    fireEvent.change(within(form).getByLabelText(/Why/), { target: { value: "Glare looked like a tear" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save correction" }));
    await waitFor(() =>
      expect(review.onCorrect).toHaveBeenCalledWith("dmg_box_0", {
        type: "walkway_blocked", severity: "low", description: "Sits on the lane, not damaged.", reason: "Glare looked like a tear",
      }),
    );
    await waitFor(() => expect(screen.queryByRole("form", { name: /Correct/ })).toBeNull());
  });

  it("keeps the form and the text when saving fails", async () => {
    setup(undefined, { onCorrect: vi.fn().mockRejectedValue(new ApiError("409", 409, "This finding was already dispatched.")) });
    fireEvent.click(screen.getAllByRole("button", { name: /Correct this finding/ })[0]);
    const text = screen.getByLabelText("What is actually wrong");
    fireEvent.change(text, { target: { value: "Torn side" } });
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/already dispatched/);
    expect((text as HTMLTextAreaElement).value).toBe("Torn side");
  });

  it("shows corrected findings with what the model originally proposed, and can undo", async () => {
    const corrected: Finding = {
      ...scan.findings[0], severity: "low", description: "Only scuffed.", review_status: "corrected",
      original: { type: "box_damage", severity: "high", description: "Box box_0: crushed corner." },
    };
    const { review } = setup([corrected, scan.findings[1]]);
    expect(screen.getByText("✎ Corrected by supervisor")).toBeDefined();
    expect(screen.getByText(/Model proposed: Damaged box · high · “Box box_0: crushed corner.”/)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Undo correction" }));
    await waitFor(() => expect(review.onUndo).toHaveBeenCalledWith("dmg_box_0"));
  });

  it("adds a missed finding on a numbered box", async () => {
    const { review } = setup();
    fireEvent.click(screen.getByRole("button", { name: /Add a finding the model missed/ }));
    const form = screen.getByRole("form", { name: "Add a missed finding" });
    const box = within(form).getByLabelText(/Box \(numbers match the photo\)/);
    expect(within(box).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Box #1 (now: damaged)", "Box #2 (now: OK)", "Box #3 (now: blocks walkway)",
    ]);
    fireEvent.change(box, { target: { value: "box_1" } });
    fireEvent.change(within(form).getByLabelText("What is actually wrong"), { target: { value: "Water stain" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add finding" }));
    await waitFor(() =>
      expect(review.onAdd).toHaveBeenCalledWith("box_1", { type: "box_damage", severity: "medium", description: "Water stain", reason: "" }),
    );
  });

  it("offers adding even when the model found nothing", () => {
    setup([]);
    expect(screen.getByText("No damage or blocked walkways found.")).toBeDefined();
    expect(screen.getByRole("button", { name: /Add a finding the model missed/ })).toBeDefined();
  });

  it("marks supervisor-added findings and leaves rejected ones out of the dispatch", () => {
    const added: Finding = { ...scan.findings[0], id: "added_dmg_box_1", object_id: "box_1", review_status: "added" };
    const rejected: Finding = { ...scan.findings[1], review_status: "rejected" };
    const { onApprove } = setup([added, rejected]);
    expect(screen.getByText("＋ Added by supervisor")).toBeDefined();
    expect(screen.getByRole("button", { name: "Remove" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Undo rejection" })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: /Approve & Dispatch \(1\)/ }));
    expect(onApprove).toHaveBeenCalledWith(["added_dmg_box_1"], {}, {});
  });

  it("hides editing without the review permission or once locked", () => {
    render(<FindingsList findings={scan.findings} inventory={{}} pending={false} disabled={false} onApprove={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /Correct this finding/ })).toBeNull();
    cleanup();
    render(
      <FindingsList findings={scan.findings} inventory={{}} pending={false} disabled onApprove={vi.fn()}
        review={{ boxes: scan.objects, onCorrect: vi.fn(), onAdd: vi.fn(), onUndo: vi.fn() }} />,
    );
    expect(screen.queryByRole("button", { name: /Correct this finding|Add a finding/ })).toBeNull();
  });
});

describe("Overlay follows the review", () => {
  it("uses the corrected type, ignores rejections, places added findings and numbers boxes", () => {
    const findings: Finding[] = [
      { ...scan.findings[0], type: "walkway_blocked", review_status: "corrected" },
      { ...scan.findings[1], review_status: "rejected" },
      { ...scan.findings[0], id: "added_dmg_box_1", object_id: "box_1", review_status: "added" },
    ];
    render(<ScanOverlay imageUrl="blob:x" scan={{ ...scan, findings }} />);
    expect(screen.getByTestId("object-box_0").getAttribute("data-state")).toBe("blocked");
    expect(screen.getByTestId("object-box_1").getAttribute("data-state")).toBe("damaged");
    expect(screen.getByTestId("object-box_2").getAttribute("data-state")).toBe("ok");
    expect(within(screen.getByTestId("object-box_2")).getByText("#3")).toBeDefined();
    expect(boxStatus(scan.objects[2], findings)).toEqual({ damage: null, blocked: null });
  });
});
