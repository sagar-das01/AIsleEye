"use client";

import { useCallback, useEffect, useState } from "react";

import { api, type Account } from "@/lib/api";
import { describeError } from "@/lib/scan";

export function AdminPanel({ selfId }: { selfId: string }) {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setAccounts(await api.accounts());
    } catch (e) {
      setError(describeError(e).message);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch of the demo accounts
    void load();
  }, [load]);

  const toggle = async (account: Account) => {
    setBusy(account.user_id);
    setError(null);
    try {
      await api.setAccountActive(account.user_id, !account.active);
      await load();
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section aria-labelledby="admin-title" className="rounded-lg border border-panel-border bg-panel p-4 text-sm">
      <h2 id="admin-title" className="text-xs uppercase tracking-widest text-zinc-500">
        Demo accounts
      </h2>
      <p className="mt-2 text-zinc-400">Disabling an account signs it out everywhere and removes it from assignment.</p>
      {error && (
        <p role="alert" className="mt-3 rounded-md border border-danger p-3">
          {error}
        </p>
      )}
      <ul className="mt-3 flex flex-col gap-2">
        {accounts?.map((a) => (
          <li key={a.user_id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-panel-border p-3">
            <span className="flex flex-col">
              <strong>{a.display_name}</strong>
              <span className="text-xs text-zinc-400">
                {a.roles.join(", ")} · {a.active ? "✓ Active" : "✕ Disabled"}
              </span>
            </span>
            {a.user_id !== selfId && (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void toggle(a)}
                className="min-h-11 rounded-md border border-panel-border px-3 disabled:opacity-50"
              >
                {a.active ? "Disable" : "Enable"}
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
