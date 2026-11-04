"use client";

import { useState } from "react";

import type { Finding, Inventory } from "@/lib/api";
import { SEVERITY_ICON } from "@/lib/scan";

interface Props {
  findings: Finding[];
  inventory: Inventory;
  pending: boolean;
  disabled: boolean;
  defaultSku?: string;
  onApprove: (findingIds: string[], skus: Record<string, string>) => void;
  onReject?: (findingId: string) => void;
}

const SEVERITY_STYLE = { low: "text-zinc-300", medium: "text-hazard", high: "text-danger" } as const;
const TYPE_LABEL = { box_damage: "Damaged box", walkway_blocked: "Blocked walkway" } as const;

export function FindingsList({ findings, inventory, pending, disabled, defaultSku, onApprove, onReject }: Props) {
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [rejected, setRejected] = useState<Set<string>>(new Set());
  const [skus, setSkus] = useState<Record<string, string>>(() =>
    defaultSku ? Object.fromEntries(findings.filter((f) => f.type === "box_damage").map((f) => [f.id, defaultSku])) : {},
  );

  const selected = findings.filter((f) => !excluded.has(f.id));
  const locked = pending || disabled;

  function toggle(id: string) {
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function reject(id: string) {
    setRejected((prev) => new Set(prev).add(id));
    setExcluded((prev) => new Set(prev).add(id));
    onReject?.(id);
  }

  function submit() {
    if (locked || selected.length === 0) return;
    const chosen: Record<string, string> = {};
    for (const f of selected) {
      const sku = skus[f.id]?.trim();
      if (f.type === "box_damage" && sku) chosen[f.id] = sku;
    }
    onApprove(selected.map((f) => f.id), chosen);
  }

  if (findings.length === 0) {
    return <p className="mt-3 text-sm text-zinc-400">No damage or blocked walkways found.</p>;
  }

  return (
    <div className="mt-3 flex flex-col gap-3">
      <ul className="flex flex-col gap-3">
        {findings.map((f) => {
          const off = excluded.has(f.id);
          return (
            <li key={f.id} className={`rounded-md border border-panel-border p-3 ${off ? "opacity-50" : ""}`}>
              <label className="flex min-h-11 cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  className="mt-1 h-5 w-5 accent-yellow-400"
                  checked={!off}
                  disabled={locked || rejected.has(f.id)}
                  onChange={() => toggle(f.id)}
                  aria-label={`Include ${TYPE_LABEL[f.type]} ${f.id}`}
                />
                <span className="flex flex-col gap-1 text-sm">
                  <span className="flex flex-wrap items-center gap-2">
                    <strong>{TYPE_LABEL[f.type]}</strong>
                    <span className={`font-mono text-xs ${SEVERITY_STYLE[f.severity]}`}>
                      {SEVERITY_ICON[f.severity]} {f.severity}
                    </span>
                    <span className="font-mono text-xs text-zinc-400">{Math.round(f.confidence * 100)}% confident</span>
                    {f.needs_review && (
                      <span className="rounded border border-hazard px-1.5 text-xs text-hazard">⚑ Needs review</span>
                    )}
                  </span>
                  <span className="text-zinc-300">{f.description}</span>
                  {f.needs_review && f.review_reason && <span className="text-xs text-hazard">{f.review_reason}</span>}
                </span>
              </label>
              {onReject && (
                <div className="mt-2 pl-8 text-xs">
                  {rejected.has(f.id) ? (
                    <span className="text-zinc-400">Rejected: not a real issue. Thanks for the feedback.</span>
                  ) : (
                    <button
                      type="button"
                      disabled={locked}
                      onClick={() => reject(f.id)}
                      className="min-h-11 rounded border border-panel-border px-3 text-zinc-300 disabled:opacity-50"
                    >
                      Reject: not a real issue
                    </button>
                  )}
                </div>
              )}
              {f.type === "box_damage" && !off && (
                <label className="mt-2 flex flex-col gap-1 pl-8 text-xs text-zinc-400">
                  SKU of the damaged box (optional, needed to quarantine stock)
                  <input
                    list="sku-options"
                    className="min-h-11 rounded border border-panel-border bg-background px-3 text-sm text-foreground"
                    value={skus[f.id] ?? ""}
                    disabled={locked}
                    placeholder="e.g. SKU-1042"
                    onChange={(e) => setSkus((prev) => ({ ...prev, [f.id]: e.target.value }))}
                  />
                </label>
              )}
            </li>
          );
        })}
      </ul>
      <datalist id="sku-options">
        {Object.keys(inventory).map((sku) => (
          <option key={sku} value={sku} />
        ))}
      </datalist>
      <button
        type="button"
        onClick={submit}
        disabled={locked || selected.length === 0}
        className="min-h-12 rounded-lg bg-hazard px-5 text-sm font-semibold text-black disabled:cursor-not-allowed disabled:opacity-50"
      >
        {pending ? "Dispatching…" : `Approve & Dispatch (${selected.length})`}
      </button>
    </div>
  );
}
