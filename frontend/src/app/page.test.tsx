import { cleanup, render, screen, within } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import Home from "./page";

vi.mock("next/font/google", () => ({
  Geist: () => ({ variable: "font-geist-sans" }),
  Geist_Mono: () => ({ variable: "font-geist-mono" }),
}));

beforeEach(() => {
  vi.spyOn(api, "health").mockReturnValue(new Promise(() => {}));
  vi.spyOn(api, "tickets").mockResolvedValue([]);
  vi.spyOn(api, "inventory").mockResolvedValue({});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Home", () => {
  it("renders the brand, the four KPIs and both panels", () => {
    render(<Home />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("AIsleEye");
    for (const kpi of ["Scans this session", "Open tickets", "Units quarantined", "Walkway hazards"]) {
      expect(screen.getByText(kpi)).toBeDefined();
    }
    expect(screen.getByRole("heading", { name: "Findings" })).toBeDefined();
    expect(screen.getByRole("heading", { name: "Agent actions" })).toBeDefined();
  });

  it("has one h1, two panel headings and the banner and main landmarks", () => {
    render(<Home />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(2);
    expect(within(screen.getByRole("banner")).getByText("Connecting…")).toBeDefined();
    expect(screen.getByRole("main")).toBeDefined();
  });

  it("offers camera and gallery uploads before anything is scanned", () => {
    render(<Home />);
    expect(screen.getByRole("button", { name: "Take photo" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Choose from gallery" })).toBeDefined();
    expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull();
  });

  it("stays usable when the backend is down", async () => {
    vi.mocked(api.health).mockRejectedValue(new Error("down"));
    render(<Home />);
    expect(await screen.findByText("Backend offline")).toBeDefined();
    expect(screen.getByRole("button", { name: "Take photo" })).toBeDefined();
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
