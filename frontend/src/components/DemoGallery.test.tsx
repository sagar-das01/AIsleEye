import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { API_URL, type DemoPhoto } from "@/lib/api";

import { DemoGallery } from "./DemoGallery";

afterEach(cleanup);

const demos: DemoPhoto[] = [
  { id: "a", title: "Photo A", description: "First.", sku: null, scan_id: "0123456789abcdef", image_url: "/demo/a.jpg", pinned: true },
  { id: "b", title: "Photo B", description: "Second.", sku: "SKU-1", scan_id: "fedcba9876543210", image_url: "/demo/b.jpg", pinned: true },
];

describe("DemoGallery", () => {
  it("shows a titled, described thumbnail button per demo photo", () => {
    const { container } = render(<DemoGallery demos={demos} busy={false} replay={false} onPick={() => {}} />);
    expect(screen.getByRole("heading", { name: "Or try a demo photo" })).toBeDefined();
    expect(screen.getAllByRole("button")).toHaveLength(2);
    expect(screen.getByText("First.")).toBeDefined();
    expect(container.querySelector("img")?.getAttribute("src")).toBe(`${API_URL}/demo/a.jpg`);
  });

  it("reports the chosen demo", () => {
    const onPick = vi.fn();
    render(<DemoGallery demos={demos} busy={false} replay={false} onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: /Photo B/ }));
    expect(onPick).toHaveBeenCalledWith(demos[1]);
  });

  it("disables the buttons while busy", () => {
    render(<DemoGallery demos={demos} busy replay={false} onPick={() => {}} />);
    for (const b of screen.getAllByRole("button")) expect((b as HTMLButtonElement).disabled).toBe(true);
  });

  it("explains replay mode", () => {
    render(<DemoGallery demos={demos} busy={false} replay onPick={() => {}} />);
    expect(screen.getByText(/only the demo photos have saved results/)).toBeDefined();
  });

  it("renders nothing without demo photos", () => {
    const { container } = render(<DemoGallery demos={[]} busy={false} replay={false} onPick={() => {}} />);
    expect(container.innerHTML).toBe("");
  });
});
