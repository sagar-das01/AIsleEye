import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { makeScan } from "@/test-fixtures";

import { ScanOverlay } from "./ScanOverlay";

afterEach(cleanup);

describe("ScanOverlay", () => {
  it("overlays the photo with an svg in image coordinates", () => {
    const { container } = render(<ScanOverlay imageUrl="blob:x" scan={makeScan()} />);
    expect(screen.getByAltText("Uploaded warehouse photo").getAttribute("src")).toBe("blob:x");
    expect(container.querySelector("svg")?.getAttribute("viewBox")).toBe("0 0 400 300");
  });

  it("describes what was found for assistive technology", () => {
    render(<ScanOverlay imageUrl="blob:x" scan={makeScan()} />);
    expect(screen.getByRole("img", { name: "3 boxes and 1 walkway marking detected" })).toBeDefined();
  });

  it("uses singular wording for one box", () => {
    const scan = makeScan({ objects: makeScan().objects.slice(0, 1), findings: [] });
    render(<ScanOverlay imageUrl="blob:x" scan={scan} />);
    expect(screen.getByRole("img", { name: "1 box and 0 walkway markings detected" })).toBeDefined();
  });

  it("marks each box damaged, blocking or OK", () => {
    render(<ScanOverlay imageUrl="blob:x" scan={makeScan()} />);
    expect(screen.getByTestId("object-box_0").getAttribute("data-state")).toBe("damaged");
    expect(screen.getByTestId("object-box_1").getAttribute("data-state")).toBe("ok");
    expect(screen.getByTestId("object-box_2").getAttribute("data-state")).toBe("blocked");
  });

  it("pairs every state with a text label and an icon, never colour alone", () => {
    render(<ScanOverlay imageUrl="blob:x" scan={makeScan()} />);
    expect(within(screen.getByTestId("object-box_0")).getByText("✕ Damaged")).toBeDefined();
    expect(within(screen.getByTestId("object-box_1")).getByText("✓ OK")).toBeDefined();
    expect(within(screen.getByTestId("object-box_2")).getByText("⚠ Blocks walkway")).toBeDefined();
    expect(within(screen.getByTestId("object-walkway_0")).getByText("═ Walkway")).toBeDefined();
  });

  it("uses a different pattern or line style per state", () => {
    const { container } = render(<ScanOverlay imageUrl="blob:x" scan={makeScan()} />);
    const fill = (id: string) => screen.getByTestId(id).querySelector("path")?.getAttribute("fill");
    expect(fill("object-box_2")).toBe("url(#hatch-red)");
    expect(fill("object-box_1")).toBe("url(#dots-green)");
    expect(screen.getByTestId("object-walkway_0").querySelector("path")?.getAttribute("stroke-dasharray")).toBe("14 8");
    expect(container.querySelector("pattern#hatch-red")).not.toBeNull();
    expect(container.querySelector("pattern#dots-green")).not.toBeNull();
  });

  it("pulses only the blocked box, once", () => {
    render(<ScanOverlay imageUrl="blob:x" scan={makeScan()} />);
    expect(screen.getByTestId("object-box_2").getAttribute("class")).toContain("pulse-once");
    expect(screen.getByTestId("object-box_0").getAttribute("class")).toBeNull();
    expect(screen.getByTestId("object-box_1").getAttribute("class")).toBeNull();
  });

  it("labels a box that is both damaged and blocking with both facts", () => {
    const base = makeScan();
    const scan = makeScan({ findings: [...base.findings, { ...base.findings[0], id: "dmg_box_2" }] });
    render(<ScanOverlay imageUrl="blob:x" scan={scan} />);
    expect(within(screen.getByTestId("object-box_2")).getByText("⚠ Damaged · blocks walkway")).toBeDefined();
  });

  it("draws the masks from the backend's svg paths", () => {
    render(<ScanOverlay imageUrl="blob:x" scan={makeScan()} />);
    expect(screen.getByTestId("object-box_0").querySelector("path")?.getAttribute("d")).toBe(makeScan().objects[0].mask_svg);
  });

  it("copes with a scan that found nothing", () => {
    const { container } = render(<ScanOverlay imageUrl="blob:x" scan={makeScan({ objects: [], findings: [] })} />);
    expect(container.querySelectorAll("path")).toHaveLength(0);
  });
});
