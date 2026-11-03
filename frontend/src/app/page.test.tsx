import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import Home from "./page";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Home dashboard", () => {
  it("renders the brand, KPI strip and panels", () => {
    vi.spyOn(api, "health").mockReturnValue(new Promise(() => {}));
    render(<Home />);

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("AIsleEye");
    for (const kpi of ["Scans today", "Open tickets", "Units quarantined", "Walkway hazards"]) {
      expect(screen.getByText(kpi)).toBeDefined();
    }
    expect(screen.getByRole("heading", { name: "Findings" })).toBeDefined();
    expect(screen.getByRole("heading", { name: "Agent actions" })).toBeDefined();
  });
});
