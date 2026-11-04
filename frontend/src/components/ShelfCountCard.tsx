"use client";

import { useEffect, useState } from "react";

import { api, type Inventory, type ReconcileResult, type ShelfCount } from "@/lib/api";
import { describeError } from "@/lib/scan";

interface Props {
  scanId: string;
  inventory: Inventory;
  defaultSku?: string;
  canReconcile: boolean;
  onReconciled?: () => void;
}

export function ShelfCountCard({ scanId, inventory, defaultSku, canReconcile, onReconciled }: Props) {
  const [sku, setSku] = useState(defaultSku && inventory[defaultSku] ? defaultSku : "");
  const [excludeDamaged, setExcludeDamaged] = useState(true);
  const [count, setCount] = useState<ShelfCount | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ReconcileResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.shelfCount(scanId, excludeDamaged).then(
      (c) => live && setCount(c),
      (e) => live && setError(describeError(e).message),
    );
    return () => {
      live = false;
    };
  }, [scanId, excludeDamaged]);

  const system = sku ? inventory[sku]?.available : undefined;
  const diff = count && system !== undefined ? count.counted - system : null;

  const reconcile = async () => {
    if (pending || !sku || !count) return;
    setPending(true);
    setError(null);
    try {
      setResult(await api.reconcile(scanId, sku, excludeDamaged, confirmed));
      onReconciled?.();
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setPending(false);
    }
  };

  return (
    <section aria-labelledby="count-title" className="rounded-lg border border-panel-border bg-panel p-4 text-sm">
      <h2 id="count-title" className="text-xs uppercase tracking-widest text-zinc-500">
        Shelf count
      </h2>
      <label className="mt-3 flex flex-col gap-1">
        SKU on this shelf
        <select
          className="min-h-11 rounded border border-panel-border bg-background px-3"
          value={sku}
          disabled={pending || result !== null}
          onChange={(e) => setSku(e.target.value)}
        >
          <option value="">Choose a SKU</option>
          {Object.entries(inventory).map(([s, item]) => (
            <option key={s} value={s}>
              {item.name ? `${s} · ${item.name}` : s}
            </option>
          ))}
        </select>
      </label>
      <label className="mt-2 flex min-h-11 items-center gap-2">
        <input
          type="checkbox"
          className="h-5 w-5"
          checked={excludeDamaged}
          disabled={pending || result !== null}
          onChange={(e) => setExcludeDamaged(e.target.checked)}
        />
        Leave damaged boxes out of the count
      </label>

      {count && (
        <p className="mt-2 font-mono" aria-label="Count versus system stock">
          Counted {count.counted}
          {system !== undefined && ` · System ${system} available · ${diff! > 0 ? "+" : diff! < 0 ? "−" : "±"}${Math.abs(diff!)}`}
          {count.damaged > 0 && excludeDamaged && ` · ${count.damaged} damaged not counted`}
        </p>
      )}
      {count?.needs_review && (
        <p className="mt-2 text-hazard">
          ⚑ Needs review: {count.low_confidence > 0 ? `${count.low_confidence} low-confidence box(es)` : "some boxes were not inspected"}.
          Count in person before relying on it.
        </p>
      )}

      {canReconcile && count && sku && diff !== 0 && !result && (
        <div className="mt-3 flex flex-col gap-2">
          {count.needs_review && (
            <label className="flex min-h-11 items-center gap-2">
              <input type="checkbox" className="h-5 w-5" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
              I checked the count in person
            </label>
          )}
          <button
            type="button"
            disabled={pending || (count.needs_review && !confirmed)}
            onClick={() => void reconcile()}
            className="min-h-12 rounded-lg border border-hazard px-4 font-semibold text-hazard disabled:opacity-50"
          >
            {pending ? "Adjusting…" : `Adjust ${sku} to counted (${count.counted})`}
          </button>
        </div>
      )}
      {result && (
        <p role="status" className="mt-3 text-ok">
          ✓ {result.sku} set to {result.available} available ({result.change > 0 ? "+" : ""}
          {result.change}). Recorded in the audit log.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 rounded-md border border-danger p-3">
          {error}
        </p>
      )}
    </section>
  );
}
