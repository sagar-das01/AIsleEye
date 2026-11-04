import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, pdfUrl, API_URL } from "./api";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("api.scan", () => {
  it("posts the file as multipart field 'image' and leaves the content type to the browser", async () => {
    const fetchMock = stubFetch(new Response(JSON.stringify({ scan_id: "x" })));
    const file = new File(["bytes"], "p.png", { type: "image/png" });
    await api.scan(file);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_URL}/scan`);
    expect(init.method).toBe("POST");
    expect((init.body as FormData).get("image")).toBeInstanceOf(File);
    expect(init.headers).toBeUndefined();
  });
});

describe("api.approve", () => {
  it("sends finding ids and SKUs as JSON to the scan's approve route", async () => {
    const fetchMock = stubFetch(new Response(JSON.stringify({ actions: [] })));
    await api.approve("0123456789abcdef", ["dmg_box_0"], { dmg_box_0: "SKU-1" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_URL}/scan/0123456789abcdef/approve`);
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(init.body)).toEqual({ finding_ids: ["dmg_box_0"], skus: { dmg_box_0: "SKU-1" } });
  });

  it("defaults to no SKUs", async () => {
    const fetchMock = stubFetch(new Response("{}"));
    await api.approve("0123456789abcdef", ["a"]);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).skus).toEqual({});
  });
});

describe("ApiError", () => {
  it("carries the status and the server's detail but keeps them out of the message", async () => {
    stubFetch(new Response(JSON.stringify({ detail: "Image is too large." }), { status: 422 }));
    const error = await api.scan(new File(["x"], "p.png")).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, detail: "Image is too large.", message: "POST /scan failed: 422" });
  });

  it("ignores validation-error arrays and non-JSON bodies", async () => {
    stubFetch(new Response(JSON.stringify({ detail: [{ msg: "bad" }] }), { status: 422 }));
    expect((await api.scan(new File(["x"], "p.png")).catch((e) => e)).detail).toBeUndefined();
    stubFetch(new Response("<html>", { status: 502 }));
    expect((await api.scan(new File(["x"], "p.png")).catch((e) => e)).detail).toBeUndefined();
  });
});

describe("read calls and links", () => {
  it("fetches tickets and inventory", async () => {
    const fetchMock = stubFetch(new Response("[]"));
    await api.tickets();
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_URL}/tickets`);
    stubFetch(new Response("{}"));
    await expect(api.inventory()).resolves.toEqual({});
  });

  it("builds absolute ticket PDF links on the API host", () => {
    expect(pdfUrl({ pdf_url: "/tickets/T-1.pdf" })).toBe(`${API_URL}/tickets/T-1.pdf`);
  });
});

describe("api.rejectFinding", () => {
  it("posts a reject verdict for the finding as JSON", async () => {
    const fetchMock = stubFetch(new Response("{}"));
    await api.rejectFinding("abc", "dmg_box_1", "shadow");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_URL}/scan/abc/feedback`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ finding_id: "dmg_box_1", action: "reject", reason: "shadow" });
  });
});

describe("demo photos", () => {
  it("lists demos and downloads one as a JPEG file named after it", async () => {
    stubFetch(new Response(JSON.stringify([{ id: "a" }])));
    await expect(api.demos()).resolves.toEqual([{ id: "a" }]);
    const fetchMock = stubFetch(new Response("abc"));
    const file = await api.demoPhoto({ id: "damaged_crushed", image_url: "/demo/damaged_crushed.jpg" });
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_URL}/demo/damaged_crushed.jpg`);
    expect(file).toBeInstanceOf(File);
    expect([file.name, file.type, file.size]).toEqual(["damaged_crushed.jpg", "image/jpeg", 3]);
  });

  it("raises an ApiError when the demo photo is missing", async () => {
    stubFetch(new Response("", { status: 404 }));
    await expect(api.demoPhoto({ id: "x", image_url: "/demo/x.jpg" })).rejects.toMatchObject({ status: 404 });
  });
});
