import { cleanup, render, screen, within } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import Home from "./page";

vi.mock("next/font/google", () => ({
  Geist: () => ({ variable: "font-geist-sans" }),
  Geist_Mono: () => ({ variable: "font-geist-mono" }),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Home dashboard edges", () => {
  it("shows a placeholder for every KPI until real data exists", () => {
    vi.spyOn(api, "health").mockReturnValue(new Promise(() => {}));
    render(<Home />);
    const placeholders = screen.getAllByText("—");
    expect(placeholders).toHaveLength(4);
  });

  it("has exactly one h1 and two panel headings", () => {
    vi.spyOn(api, "health").mockReturnValue(new Promise(() => {}));
    render(<Home />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(2);
  });

  it("shows empty-state guidance before any scan", () => {
    vi.spyOn(api, "health").mockReturnValue(new Promise(() => {}));
    render(<Home />);
    expect(screen.getByText("Upload a warehouse photo to scan")).toBeDefined();
    expect(screen.getByText("No scan yet.")).toBeDefined();
    expect(screen.getByText("Approve findings to dispatch tickets.")).toBeDefined();
  });

  it("keeps the backend status inside the header landmark", () => {
    vi.spyOn(api, "health").mockReturnValue(new Promise(() => {}));
    render(<Home />);
    expect(within(screen.getByRole("banner")).getByText("Connecting…")).toBeDefined();
    expect(screen.getByRole("main")).toBeDefined();
  });

  it("stays usable when the backend is down", async () => {
    vi.spyOn(api, "health").mockRejectedValue(new Error("down"));
    render(<Home />);
    expect(await screen.findByText("Backend offline")).toBeDefined();
    expect(screen.getByRole("heading", { name: "Findings" })).toBeDefined();
  });

  it("does not render any button or link before an upload is possible", () => {
    vi.spyOn(api, "health").mockReturnValue(new Promise(() => {}));
    render(<Home />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});

describe("Root layout", () => {
  it("declares the AIsleEye title and a description", async () => {
    const { metadata } = await import("./layout");
    expect(metadata.title).toBe("AIsleEye — Warehouse Floor Copilot");
    expect(String(metadata.description)).toMatch(/damage/i);
  });

  it("renders an English dark-themed document and its children", async () => {
    const { default: RootLayout } = await import("./layout");
    const html = renderToStaticMarkup(RootLayout({ children: <p>child</p> } as never));
    expect(html).toContain('<html lang="en"');
    expect(html).toMatch(/class="[^"]*\bdark\b/);
    expect(html).toContain("<p>child</p>");
  });
});
