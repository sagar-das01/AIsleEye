import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

async function loadApi(url?: string) {
  vi.resetModules();
  if (url === undefined) vi.stubEnv("NEXT_PUBLIC_API_URL", undefined as unknown as string);
  else vi.stubEnv("NEXT_PUBLIC_API_URL", url);
  return import("./api");
}

const HEALTH = { status: "ok", models: { vision_agent: "muse-spark-1.3", segmentation: "sam-3.1" } };

describe("API_URL configuration", () => {
  it("uses NEXT_PUBLIC_API_URL when set", async () => {
    const { API_URL } = await loadApi("https://api.example.test");
    expect(API_URL).toBe("https://api.example.test");
  });

  it("falls back to the local backend when unset", async () => {
    const { API_URL } = await loadApi();
    expect(API_URL).toBe("http://localhost:8000");
  });

  it("builds request URLs from the override", async () => {
    const { api } = await loadApi("https://api.example.test");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(HEALTH)));
    vi.stubGlobal("fetch", fetchMock);
    await api.health();
    expect(fetchMock.mock.calls[0][0]).toBe("https://api.example.test/health");
  });
});

describe("api.health errors", () => {
  it.each([400, 401, 404, 500, 502, 504])("rejects with the status for HTTP %i", async (status) => {
    const { api } = await loadApi("http://x.test");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status })));
    await expect(api.health()).rejects.toThrow(`GET /health failed: ${status}`);
  });

  it("does not leak the response body into the error", async () => {
    const { api } = await loadApi("http://x.test");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("secret-stack-trace", { status: 500 })));
    await expect(api.health()).rejects.not.toThrow(/secret-stack-trace/);
  });

  it("rejects when a 200 response is not JSON", async () => {
    const { api } = await loadApi("http://x.test");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>proxy error</html>", { status: 200 })));
    await expect(api.health()).rejects.toThrow();
  });

  it("rejects on an aborted request", async () => {
    const { api } = await loadApi("http://x.test");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("aborted", "AbortError")));
    await expect(api.health()).rejects.toThrow("aborted");
  });

  it("makes exactly one request per call and never retries silently", async () => {
    const { api } = await loadApi("http://x.test");
    const fetchMock = vi.fn().mockResolvedValue(new Response("x", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    await api.health().catch(() => {});
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("always disables the fetch cache so status is live", async () => {
    const { api } = await loadApi("http://x.test");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(HEALTH)));
    vi.stubGlobal("fetch", fetchMock);
    await api.health();
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ cache: "no-store" });
  });
});
