import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { boxStatus, describeError, summarizeArgs, validateUpload } from "./scan";
import { makeScan, photo } from "@/test-fixtures";

describe("validateUpload", () => {
  it.each(["image/jpeg", "image/png", "image/webp"])("accepts %s", (type) => {
    expect(validateUpload(photo("a", type))).toBeNull();
  });

  it.each(["image/gif", "application/pdf", "text/plain", ""])("rejects type %j", (type) => {
    expect(validateUpload(photo("a", type))).toMatch(/JPEG, PNG or WebP/);
  });

  it("accepts exactly 10 MB and rejects one byte more", () => {
    expect(validateUpload(photo("a", "image/png", 10 * 1024 * 1024))).toBeNull();
    expect(validateUpload(photo("a", "image/png", 10 * 1024 * 1024 + 1))).toMatch(/10 MB/);
  });

  it("rejects an empty file", () => {
    expect(validateUpload(photo("a", "image/png", 0))).toMatch(/empty/);
  });
});

describe("describeError", () => {
  it.each([502, 503])("treats HTTP %i as retryable and friendly", (status) => {
    const out = describeError(new ApiError("POST /scan failed", status));
    expect(out.retryable).toBe(true);
    expect(out.message).toMatch(/busy or unavailable/);
    expect(out.message).not.toMatch(/\d{3}/);
  });

  it("explains a timeout (HTTP 504) as taking too long, with a retry", () => {
    const out = describeError(new ApiError("POST /scan failed", 504));
    expect(out).toEqual({ message: "The analysis took too long. Please try again, or use a smaller photo.", retryable: true });
  });

  it.each([413, 415, 422])("shows the server's reason for HTTP %i and does not offer a retry", (status) => {
    expect(describeError(new ApiError("x", status, "Image is too large."))).toEqual({ message: "Image is too large.", retryable: false });
  });

  it("falls back to a generic message when a 4xx carries no detail", () => {
    expect(describeError(new ApiError("x", 422)).message).toMatch(/could not be used/);
  });

  it("treats network failures as a backend-unreachable problem", () => {
    expect(describeError(new TypeError("Failed to fetch"))).toMatchObject({ retryable: true, message: expect.stringMatching(/Can't reach the backend/) });
  });

  it("never leaks a stack trace or raw error text", () => {
    expect(describeError(new Error("secret internals")).message).not.toMatch(/secret/);
    expect(describeError(new ApiError("GET /x failed: 500", 500)).message).not.toMatch(/500/);
  });
});

describe("boxStatus", () => {
  const scan = makeScan();

  it("links a box to its damage and hazard findings by id", () => {
    const [b0, , b2] = scan.objects;
    expect(boxStatus(b0, scan.findings).damage?.id).toBe("dmg_box_0");
    expect(boxStatus(b0, scan.findings).blocked).toBeNull();
    expect(boxStatus(b2, scan.findings).blocked?.id).toBe("hz_box_2");
  });

  it("returns nothing for an intact, clear box", () => {
    expect(boxStatus(scan.objects[1], scan.findings)).toEqual({ damage: null, blocked: null });
  });
});

describe("summarizeArgs", () => {
  it("omits the finding id and joins the rest", () => {
    expect(summarizeArgs({ finding_id: "f", sku: "SKU-1", delta: 2 })).toBe("sku: SKU-1 · delta: 2");
  });

  it("is empty for no arguments", () => {
    expect(summarizeArgs({})).toBe("");
  });
});

describe("describeError for missing results", () => {
  it("uses the server's reason for a 404 and does not offer a retry", () => {
    expect(describeError(new ApiError("x", 404, "No saved replay result for this photo."))).toEqual({
      message: "No saved replay result for this photo.",
      retryable: false,
    });
  });

  it("has a fallback message for a 404 without a reason", () => {
    expect(describeError(new ApiError("x", 404)).retryable).toBe(false);
  });
});
