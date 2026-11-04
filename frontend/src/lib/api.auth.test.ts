import { afterEach, describe, expect, it, vi } from "vitest";

import { API_URL, api, setCsrfToken, setUnauthorizedHandler } from "./api";

function stubFetch(response: () => Response) {
  const fetchMock = vi.fn().mockImplementation(async () => response());
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  setUnauthorizedHandler(null);
});

describe("session-aware requests", () => {
  it("sends the CSRF token on mutations only, with same-origin credentials", async () => {
    const fetchMock = stubFetch(() => new Response("{}"));
    setCsrfToken("csrf-1");
    await api.tickets();
    await api.acknowledge("T-1", 3);
    const [, getInit] = fetchMock.mock.calls[0];
    const [url, postInit] = fetchMock.mock.calls[1];
    expect((getInit.headers as Headers).has("X-CSRF-Token")).toBe(false);
    expect(url).toBe(`${API_URL}/tickets/T-1/acknowledge`);
    expect((postInit.headers as Headers).get("X-CSRF-Token")).toBe("csrf-1");
    expect(postInit.credentials).toBe("same-origin");
    expect(JSON.parse(postInit.body)).toEqual({ expected_version: 3 });
  });

  it("returns to sign-in on a 401 only while signed in", async () => {
    stubFetch(() => new Response("{}", { status: 401 }));
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    await expect(api.session()).rejects.toMatchObject({ status: 401 });
    expect(handler).not.toHaveBeenCalled();
    setCsrfToken("csrf");
    await expect(api.tickets()).rejects.toMatchObject({ status: 401 });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("treats 204 as an empty success", async () => {
    stubFetch(() => new Response(null, { status: 204 }));
    await expect(api.logout()).resolves.toBeUndefined();
  });

  it("builds workflow and stock requests", async () => {
    const fetchMock = stubFetch(() => new Response("{}"));
    await api.signIn("tech-a");
    await api.assign("T 1", { assignee_id: "tech-b", reason: "swap" });
    await api.resolve("T-1", "done", 4);
    await api.reconcile("0123456789abcdef", "SKU-1", false, true);
    await api.shelfCount("0123456789abcdef", false);
    await api.technicians("T-1");
    await api.tickets("active");
    const calls = fetchMock.mock.calls.map(([url, init]) => [url, init.method ?? "GET", init.body && JSON.parse(init.body)]);
    expect(calls).toEqual([
      [`${API_URL}/auth/demo-sign-in`, "POST", { persona_id: "tech-a" }],
      [`${API_URL}/tickets/T%201/assignment`, "POST", { assignee_id: "tech-b", reason: "swap" }],
      [`${API_URL}/tickets/T-1/resolve`, "POST", { notes: "done", expected_version: 4 }],
      [`${API_URL}/scan/0123456789abcdef/reconcile`, "POST", { sku: "SKU-1", exclude_damaged: false, confirm_needs_review: true }],
      [`${API_URL}/scan/0123456789abcdef/count?exclude_damaged=false`, "GET", undefined],
      [`${API_URL}/technicians?ticket_id=T-1`, "GET", undefined],
      [`${API_URL}/tickets?status=active`, "GET", undefined],
    ]);
  });
});
