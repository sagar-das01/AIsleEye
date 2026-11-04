import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { photo } from "@/test-fixtures";

import { UploadPanel } from "./UploadPanel";

afterEach(cleanup);

function choose(testId: string, file: File) {
  fireEvent.change(screen.getByTestId(testId), { target: { files: [file] } });
}

describe("UploadPanel", () => {
  it("offers the camera (rear camera on phones) and a gallery pick", () => {
    render(<UploadPanel busy={false} onFile={() => {}} />);
    const camera = screen.getByTestId("camera-input");
    expect(camera.getAttribute("accept")).toBe("image/*");
    expect(camera.getAttribute("capture")).toBe("environment");
    expect(screen.getByTestId("gallery-input").getAttribute("accept")).toBe("image/jpeg,image/png,image/webp");
    expect(screen.getByRole("button", { name: "Take photo" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Choose from gallery" })).toBeDefined();
  });

  it("passes a valid photo from either input to the callback", () => {
    const onFile = vi.fn();
    render(<UploadPanel busy={false} onFile={onFile} />);
    const a = photo("a.jpg");
    const b = photo("b.png", "image/png");
    choose("camera-input", a);
    choose("gallery-input", b);
    expect(onFile.mock.calls.map((c) => c[0])).toEqual([a, b]);
  });

  it.each([
    [photo("x.gif", "image/gif"), /JPEG, PNG or WebP/],
    [photo("big.png", "image/png", 10 * 1024 * 1024 + 1), /10 MB/],
    [photo("empty.png", "image/png", 0), /empty/],
  ])("rejects an unusable file with a readable message and never calls back", (file, message) => {
    const onFile = vi.fn();
    render(<UploadPanel busy={false} onFile={onFile} />);
    choose("gallery-input", file);
    expect(screen.getByRole("alert").textContent).toMatch(message);
    expect(onFile).not.toHaveBeenCalled();
  });

  it("clears an earlier problem once a good photo is chosen", () => {
    render(<UploadPanel busy={false} onFile={() => {}} />);
    choose("gallery-input", photo("x.gif", "image/gif"));
    expect(screen.queryByRole("alert")).not.toBeNull();
    choose("gallery-input", photo());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("disables both buttons while busy", () => {
    render(<UploadPanel busy onFile={() => {}} />);
    expect((screen.getByRole("button", { name: "Take photo" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Choose from gallery" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("ignores an empty selection", () => {
    const onFile = vi.fn();
    render(<UploadPanel busy={false} onFile={onFile} />);
    fireEvent.change(screen.getByTestId("camera-input"), { target: { files: [] } });
    expect(onFile).not.toHaveBeenCalled();
  });
});
