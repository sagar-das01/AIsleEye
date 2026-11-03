import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import { BackendStatus } from "./BackendStatus";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("BackendStatus", () => {
  it("shows a connecting state first", () => {
    vi.spyOn(api, "health").mockReturnValue(new Promise(() => {}));
    render(<BackendStatus />);
    expect(screen.getByText("Connecting…")).toBeDefined();
  });

  it("shows the configured models when the backend is up", async () => {
    vi.spyOn(api, "health").mockResolvedValue({
      status: "ok",
      models: { vision_agent: "muse-spark-1.3", segmentation: "sam-3.1" },
    });
    render(<BackendStatus />);
    expect(await screen.findByText("sam-3.1 · muse-spark-1.3")).toBeDefined();
  });

  it("shows offline when the health check fails", async () => {
    vi.spyOn(api, "health").mockRejectedValue(new Error("GET /health failed: 503"));
    render(<BackendStatus />);
    expect(await screen.findByText("Backend offline")).toBeDefined();
  });
});
