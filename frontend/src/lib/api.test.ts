import { afterEach, describe, expect, it, vi } from "vitest";

import { API_URL, api } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api.health", () => {
  it("calls /health without caching and returns the JSON body", async () => {
    const body = { status: "ok", models: { vision_agent: "muse-spark-1.3", segmentation: "sam-3.1" } };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.health()).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/health`, expect.objectContaining({ cache: "no-store", credentials: "same-origin" }));
  });

  it("throws with method, path and status on non-2xx", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("down", { status: 503 })));

    await expect(api.health()).rejects.toThrow("GET /health failed: 503");
  });

  it("propagates network errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(api.health()).rejects.toThrow("Failed to fetch");
  });
});

describe("API_URL", () => {
  it("defaults to the local backend", () => {
    expect(API_URL).toBe(process.env.NEXT_PUBLIC_API_URL ?? "/api");
  });
});
