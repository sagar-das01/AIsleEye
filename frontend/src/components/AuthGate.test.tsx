import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, setCsrfToken, setUnauthorizedHandler, type PersonaListing } from "@/lib/api";
import { useSession } from "@/lib/session";
import { makeSession, TECH } from "@/test-sessions";

import { AuthGate } from "./AuthGate";

const LISTING: PersonaListing = {
  enabled: true,
  notice: "Demo sign-in: identities are not verified. Use only with synthetic, disposable data.",
  personas: [
    { id: "admin", label: "Admin", display_name: "Avery (Admin)", description: "Everything", core: true, active: true },
    { id: "tech-a", label: "Technician A", display_name: "Tariq", description: "Own tickets", core: true, active: true },
    { id: "tech-b", label: "Technician B", display_name: "Bea", description: "Own tickets", core: true, active: false },
    { id: "viewer", label: "Viewer", display_name: "Vik", description: "Scores", core: false, active: true },
  ],
};

function Probe() {
  const value = useSession()!;
  return (
    <div>
      <p>Signed in as {value.session.display_name}</p>
      <button type="button" onClick={value.signOut}>
        Sign out
      </button>
    </div>
  );
}

beforeEach(() => {
  vi.spyOn(api, "personas").mockResolvedValue(LISTING);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setCsrfToken(null);
  setUnauthorizedHandler(null);
});

const gate = () =>
  render(
    <AuthGate>
      <Probe />
    </AuthGate>,
  );

describe("AuthGate", () => {
  it("shows labelled persona buttons without any credential fields when signed out", async () => {
    vi.spyOn(api, "session").mockRejectedValue(new ApiError("401", 401));
    gate();
    expect(await screen.findByRole("button", { name: /Sign in as Admin/ })).toBeDefined();
    expect(screen.getByRole("note").textContent).toMatch(/not verified/);
    expect(screen.getByRole("button", { name: /Sign in as Technician B/ })).toHaveProperty("disabled", true);
    expect(screen.getByText("More roles")).toBeDefined();
    expect(document.querySelector("input[type=password]")).toBeNull();
    expect(document.querySelector("input")).toBeNull();
  });

  it("signs in with the persona id and renders the workspace", async () => {
    vi.spyOn(api, "session").mockRejectedValue(new ApiError("401", 401));
    const signIn = vi.spyOn(api, "signIn").mockResolvedValue(TECH);
    gate();
    fireEvent.click(await screen.findByRole("button", { name: /Sign in as Technician A/ }));
    expect(await screen.findByText("Signed in as Tariq (Technician A)")).toBeDefined();
    expect(signIn).toHaveBeenCalledWith("tech-a");
  });

  it("resumes an existing session", async () => {
    vi.spyOn(api, "session").mockResolvedValue(makeSession());
    gate();
    expect(await screen.findByText("Signed in as Fatima (Floor Manager)")).toBeDefined();
  });

  it("signs out through the backend and returns to the selection", async () => {
    vi.spyOn(api, "session").mockResolvedValue(makeSession());
    const logout = vi.spyOn(api, "logout").mockResolvedValue(undefined);
    gate();
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("button", { name: /Sign in as Admin/ })).toBeDefined();
    expect(logout).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/Signed in as/)).toBeNull();
  });

  it("still leaves the workspace when logout fails", async () => {
    vi.spyOn(api, "session").mockResolvedValue(makeSession());
    vi.spyOn(api, "logout").mockRejectedValue(new ApiError("401", 401));
    gate();
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("button", { name: /Sign in as Admin/ })).toBeDefined();
  });

  it("explains when demo sign-in is disabled on the server", async () => {
    vi.spyOn(api, "session").mockRejectedValue(new ApiError("401", 401));
    vi.mocked(api.personas).mockResolvedValue({ enabled: false, notice: "off", personas: [] });
    gate();
    expect(await screen.findByText(/DEMO_SIGN_IN_ENABLED=true/)).toBeDefined();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows a rejected sign-in", async () => {
    vi.spyOn(api, "session").mockRejectedValue(new ApiError("401", 401));
    vi.spyOn(api, "signIn").mockRejectedValue(new ApiError("403", 403, "This demo account is disabled."));
    gate();
    fireEvent.click(await screen.findByRole("button", { name: /Sign in as Admin/ }));
    expect((await screen.findByRole("alert")).textContent).toBe("This demo account is disabled.");
  });

  it("returns to the selection when a signed-in request gets a 401", async () => {
    vi.spyOn(api, "session").mockResolvedValue(makeSession());
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401 })));
    vi.mocked(api.personas).mockResolvedValue(LISTING);
    gate();
    await screen.findByText("Signed in as Fatima (Floor Manager)");
    await expect(api.tickets()).rejects.toMatchObject({ status: 401 });
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/session has ended/));
    vi.unstubAllGlobals();
  });

  it("reports an unreachable backend", async () => {
    vi.spyOn(api, "session").mockRejectedValue(new TypeError("Failed to fetch"));
    vi.mocked(api.personas).mockRejectedValue(new TypeError("Failed to fetch"));
    gate();
    expect((await screen.findByRole("alert")).textContent).toMatch(/Couldn't check your session/);
  });
});
