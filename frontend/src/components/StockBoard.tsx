"use client";

import { useCallback, useEffect, useState } from "react";

import { api, type StockRow } from "@/lib/api";
import { describeError } from "@/lib/scan";

export function StockBoard({ refreshKey }: { refreshKey?: number }) {
  const [rows, setRows] = useState<StockRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api.stockBoard());
      setError(null);
    } catch (e) {
      setError(describeError(e).message);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- (re)load after an approve or reconcile
    void load();
  }, [load, refreshKey]);

  return (
    <section aria-labelledby="stock-title" className="rounded-lg border border-panel-border bg-panel p-4">
      <div className="flex items-center justify-between">
        <h2 id="stock-title" className="text-xs uppercase tracking-widest text-zinc-500">
          Stock
        </h2>
        <button type="button" onClick={() => void load()} className="min-h-11 px-3 text-xs text-zinc-400">
          ↻ Refresh
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-3 rounded-md border border-danger p-3 text-sm">
          Couldn&apos;t load stock. {error}
        </p>
      )}
      {rows === null && !error && <p role="status" className="mt-3 text-sm text-zinc-400">Loading stock…</p>}
      {rows?.length === 0 && <p className="mt-3 text-sm text-zinc-500">No inventory recorded yet.</p>}
      {rows && rows.length > 0 && (
        <ul className="mt-3 flex flex-col gap-2">
          {rows.map((r) => (
            <li key={r.sku} className="grid grid-cols-2 gap-1 rounded-md border border-panel-border p-3 text-sm sm:grid-cols-5">
              <strong className="col-span-2 font-mono sm:col-span-1">{r.sku}</strong>
              <span>Available {r.available}</span>
              <span>Quarantine {r.quarantine}</span>
              <span>Total {r.total}</span>
              {r.low_stock ? (
                <span className="text-hazard">⚠ Reorder (below {r.reorder_point})</span>
              ) : (
                <span className="text-ok">✓ OK</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
