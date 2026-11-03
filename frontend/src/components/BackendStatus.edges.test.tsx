import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import { BackendStatus } from "./BackendStatus";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const HEALTH = { status: "ok", models: { vision_agent: "muse-spark-1.3", segmentation: "sam-3.1" } };

function dotClass(container: HTMLElement) {
  return container.querySelector("span")!.className;
}

describe("BackendStatus edges", () => {
  it("checks health exactly once on mount", async () => {
    const spy = vi.spyOn(api, "health").mockResolvedValue(HEALTH);
    render(<BackendStatus />);
    await screen.findByText("sam-3.1 · muse-spark-1.3");
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("uses a pulsing grey dot while connecting, green when up, red when down", async () => {
    vi.spyOn(api, "health").mockReturnValueOnce(new Promise(() => {}));
    const connecting = render(<BackendStatus />);
    expect(dotClass(connecting.container)).toContain("bg-zinc-500");
    expect(dotClass(connecting.container)).toContain("animate-pulse");
    connecting.unmount();

    vi.spyOn(api, "health").mockResolvedValueOnce(HEALTH);
    const up = render(<BackendStatus />);
    await screen.findByText("sam-3.1 · muse-spark-1.3");
    expect(dotClass(up.container)).toContain("bg-emerald-400");
    expect(dotClass(up.container)).not.toContain("animate-pulse");
    up.unmount();

    vi.spyOn(api, "health").mockRejectedValueOnce(new Error("down"));
    const down = render(<BackendStatus />);
    await screen.findByText("Backend offline");
    expect(dotClass(down.container)).toContain("bg-red-500");
  });

  it("does not crash when unmounted before the response arrives", async () => {
    let resolve!: (h: typeof HEALTH) => void;
    vi.spyOn(api, "health").mockReturnValue(new Promise((r) => (resolve = r)));
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const view = render(<BackendStatus />);
    view.unmount();
    resolve(HEALTH);
    await Promise.resolve();
    expect(errors).not.toHaveBeenCalled();
  });

  it("shows the model names from the backend, not hard-coded ones", async () => {
    vi.spyOn(api, "health").mockResolvedValue({
      status: "ok",
      models: { vision_agent: "muse-custom", segmentation: "sam-custom" },
    });
    render(<BackendStatus />);
    expect(await screen.findByText("sam-custom · muse-custom")).toBeDefined();
  });

  it("treats any rejection (network, 5xx, timeout) as offline", async () => {
    for (const err of [new TypeError("Failed to fetch"), new Error("GET /health failed: 502"), "string error"]) {
      vi.spyOn(api, "health").mockRejectedValueOnce(err);
      const view = render(<BackendStatus />);
      await waitFor(() => expect(screen.getByText("Backend offline")).toBeDefined());
      view.unmount();
    }
  });

  it("never shows 'Connecting…' and a result at the same time", async () => {
    vi.spyOn(api, "health").mockResolvedValue(HEALTH);
    render(<BackendStatus />);
    await screen.findByText("sam-3.1 · muse-spark-1.3");
    expect(screen.queryByText("Connecting…")).toBeNull();
  });

  // Gap found in review: a 200 response with an unexpected shape throws during render
  // (state.health.models is undefined) instead of showing "Backend offline".
  it.todo("shows offline when /health returns an unexpected JSON shape");
});
