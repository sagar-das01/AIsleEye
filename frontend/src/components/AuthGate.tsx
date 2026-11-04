"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";

import { api, ApiError, setCsrfToken, setUnauthorizedHandler, type PersonaListing, type Session } from "@/lib/api";
import { SessionContext } from "@/lib/session";

type State =
  | { kind: "loading" }
  | { kind: "signed-out"; listing: PersonaListing | null; error?: string }
  | { kind: "signed-in"; session: Session; generation: number };

export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [pending, setPending] = useState<string | null>(null);

  const showSignIn = useCallback(async (error?: string) => {
    setCsrfToken(null);
    try {
      setState({ kind: "signed-out", listing: await api.personas(), error });
    } catch {
      setState({ kind: "signed-out", listing: null, error: error ?? "Can't reach the backend. Check that it is running." });
    }
  }, []);

  const enter = useCallback((session: Session) => {
    setCsrfToken(session.csrf_token);
    // A new generation remounts the workspace, so no ticket, inbox or scan state carries over between identities.
    setState((prev) => ({ kind: "signed-in", session, generation: prev.kind === "signed-in" ? prev.generation + 1 : 0 }));
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => void showSignIn("Your session has ended. Choose a role to continue."));
    api.session().then(enter, (error) => {
      void showSignIn(error instanceof ApiError && error.status === 401 ? undefined : "Couldn't check your session.");
    });
    return () => setUnauthorizedHandler(null);
  }, [enter, showSignIn]);

  const signIn = async (personaId: string) => {
    if (pending) return;
    setPending(personaId);
    try {
      enter(await api.signIn(personaId));
    } catch (error) {
      const detail = error instanceof ApiError ? error.detail : undefined;
      void showSignIn(detail ?? "Sign-in failed. Please try again.");
    } finally {
      setPending(null);
    }
  };

  const leave = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // Already expired or revoked server-side: the selection screen is still the right place to go.
    }
    await showSignIn();
  }, [showSignIn]);

  if (state.kind === "loading") {
    return (
      <p role="status" className="p-6 text-sm text-zinc-400">
        Checking your session…
      </p>
    );
  }

  if (state.kind === "signed-out") {
    return <SignIn listing={state.listing} error={state.error} pending={pending} onPick={(id) => void signIn(id)} />;
  }

  const { session } = state;
  return (
    <SessionContext.Provider
      value={{
        session,
        can: (permission) => session.permissions.includes(permission),
        switchRole: () => void leave(),
        signOut: () => void leave(),
      }}
    >
      <div key={`${session.user_id}:${state.generation}`} className="flex flex-1 flex-col">
        {children}
      </div>
    </SessionContext.Provider>
  );
}

function SignIn({
  listing,
  error,
  pending,
  onPick,
}: {
  listing: PersonaListing | null;
  error?: string;
  pending: string | null;
  onPick: (id: string) => void;
}) {
  const core = listing?.personas.filter((p) => p.core) ?? [];
  const more = listing?.personas.filter((p) => !p.core) ?? [];

  return (
    <main className="mx-auto flex w-full max-w-xl flex-col gap-5 p-4 sm:p-6">
      <h2 className="text-xl font-semibold">Choose a demo role</h2>
      {listing?.enabled && (
        <p role="note" className="rounded-md border border-hazard p-3 text-sm text-hazard">
          ⚠ {listing.notice}
        </p>
      )}
      {error && (
        <p role="alert" className="rounded-md border border-danger p-3 text-sm">
          {error}
        </p>
      )}
      {listing && !listing.enabled && (
        <p className="text-sm text-zinc-300">
          Demo sign-in is turned off on this server. Set <code>DEMO_SIGN_IN_ENABLED=true</code> for a local demo with
          synthetic data.
        </p>
      )}
      {core.length > 0 && <PersonaButtons personas={core} pending={pending} onPick={onPick} />}
      {more.length > 0 && (
        <details className="rounded-md border border-panel-border p-3">
          <summary className="min-h-11 cursor-pointer py-2 text-sm text-zinc-300">More roles</summary>
          <PersonaButtons personas={more} pending={pending} onPick={onPick} />
        </details>
      )}
    </main>
  );
}

function PersonaButtons({
  personas,
  pending,
  onPick,
}: {
  personas: PersonaListing["personas"];
  pending: string | null;
  onPick: (id: string) => void;
}) {
  return (
    <ul className="flex flex-col gap-2">
      {personas.map((p) => (
        <li key={p.id}>
          <button
            type="button"
            disabled={!p.active || pending !== null}
            onClick={() => onPick(p.id)}
            className="flex min-h-14 w-full flex-col items-start rounded-lg border border-panel-border bg-panel px-4 py-2 text-left disabled:opacity-50"
          >
            <span className="font-semibold">
              {pending === p.id ? "Signing in…" : `Sign in as ${p.label}`}
              {!p.active && " (disabled)"}
            </span>
            <span className="text-xs text-zinc-400">
              {p.display_name} · {p.description}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
