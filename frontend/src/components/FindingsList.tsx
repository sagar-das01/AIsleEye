"use client";

import { useState } from "react";

import { FindingEditor, TYPE_LABEL } from "@/components/FindingEditor";
import type { DetectedObject, Finding, FindingDetails, FindingEdit, Inventory } from "@/lib/api";
import { boxNumbers, boxStatus, objectIdOf, SEVERITY_ICON } from "@/lib/scan";

interface Props {
  findings: Finding[];
  inventory: Inventory;
  pending: boolean;
  disabled: boolean;
  defaultSku?: string;
  onApprove: (findingIds: string[], skus: Record<string, string>, details: Record<string, FindingDetails>) => void;
  onReject?: (findingId: string) => void;
  /** Present only for roles that may review; each rejects with the API error so the form can show it. */
  review?: {
    boxes: DetectedObject[];
    onCorrect: (findingId: string, edit: FindingEdit) => Promise<void>;
    onAdd: (objectId: string, edit: FindingEdit) => Promise<void>;
    onUndo: (findingId: string) => Promise<void>;
  };
}

const SEVERITY_STYLE = { low: "text-zinc-300", medium: "text-hazard", high: "text-danger" } as const;
// Must match the guard: one detected damaged box moves exactly one unit to quarantine.
const UNITS_PER_DAMAGED_BOX = 1;

export function FindingsList({ findings, inventory, pending, disabled, defaultSku, onApprove, onReject, review }: Props) {
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [rejectedLocally, setRejected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [undoError, setUndoError] = useState<string | null>(null);
  const rejected = new Set([...rejectedLocally, ...findings.filter((f) => f.review_status === "rejected").map((f) => f.id)]);
  const [details, setDetails] = useState<Record<string, FindingDetails>>({});
  const [skus, setSkus] = useState<Record<string, string>>(() =>
    defaultSku ? Object.fromEntries(findings.filter((f) => f.type === "box_damage").map((f) => [f.id, defaultSku])) : {},
  );

  const selected = findings.filter((f) => !excluded.has(f.id) && !rejected.has(f.id));
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
    const context: Record<string, FindingDetails> = {};
    for (const f of selected) {
      const location = details[f.id]?.location?.trim();
      const instructions = details[f.id]?.instructions?.trim();
      if (location || instructions) context[f.id] = { location: location || undefined, instructions: instructions || undefined };
    }
    onApprove(selected.map((f) => f.id), chosen, context);
  }

  const numbers = review ? boxNumbers(review.boxes) : new Map<string, number>();
  const boxName = (f: Finding) => {
    const n = numbers.get(objectIdOf(f));
    return n ? ` · box #${n}` : "";
  };

  const saveCorrection = async (id: string, edit: FindingEdit) => {
    await review!.onCorrect(id, edit);
    // A corrected finding is a real one again: it goes back into the selection.
    setRejected((prev) => new Set([...prev].filter((x) => x !== id)));
    setExcluded((prev) => new Set([...prev].filter((x) => x !== id)));
    setEditing(null);
  };

  const undo = async (id: string) => {
    setUndoError(null);
    try {
      await review!.onUndo(id);
      setRejected((prev) => new Set([...prev].filter((x) => x !== id)));
    } catch {
      setUndoError("Couldn't undo that change. Please try again.");
    }
  };

  const addForm = review && !locked && (
    <AddMissed
      boxes={review.boxes}
      findings={findings}
      open={adding}
      setOpen={setAdding}
      onAdd={async (objectId, edit) => {
        await review.onAdd(objectId, edit);
        setAdding(false);
      }}
    />
  );

  if (findings.length === 0) {
    return (
      <>
        <p className="mt-3 text-sm text-zinc-400">No damage or blocked walkways found.</p>
        {addForm}
      </>
    );
  }

  const setDetail = (id: string, key: keyof FindingDetails, value: string) =>
    setDetails((prev) => ({ ...prev, [id]: { ...prev[id], [key]: value } }));

  return (
    <div className="mt-3 flex flex-col gap-3">
      <p className="text-xs text-zinc-400">
        🤖 AI-proposed findings. Nothing is dispatched until you approve; rejections are saved as review feedback.
        {review && " If the model got something wrong, edit it before approving: the corrected version is what gets dispatched."}
      </p>
      {undoError && (
        <p role="alert" className="rounded-md border border-danger p-2 text-sm">
          {undoError}
        </p>
      )}
      <ul className="flex flex-col gap-3">
        {findings.map((f) => {
          const off = excluded.has(f.id) || rejected.has(f.id);
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
                    {f.review_status === "added" ? (
                      <span className="rounded border border-ok px-1.5 text-xs text-ok">＋ Added by supervisor</span>
                    ) : (
                      <span className="font-mono text-xs text-zinc-400">{Math.round(f.confidence * 100)}% confident</span>
                    )}
                    {f.review_status === "corrected" && (
                      <span className="rounded border border-ok px-1.5 text-xs text-ok">✎ Corrected by supervisor</span>
                    )}
                    <span className="text-xs text-zinc-500">{boxName(f).replace(" · ", "")}</span>
                    {f.needs_review && (
                      <span className="rounded border border-hazard px-1.5 text-xs text-hazard">⚑ Needs review</span>
                    )}
                  </span>
                  <span className="text-zinc-300">{f.description}</span>
                  {f.needs_review && f.review_reason && <span className="text-xs text-hazard">{f.review_reason}</span>}
                  {f.original && (
                    <span className="text-xs text-zinc-500">
                      Model proposed: {TYPE_LABEL[f.original.type]} · {f.original.severity} · “{f.original.description}”
                    </span>
                  )}
                </span>
              </label>
              {review && !locked && editing !== f.id && (
                <div className="mt-2 flex flex-wrap gap-2 pl-8 text-xs">
                  <button
                    type="button"
                    onClick={() => setEditing(f.id)}
                    className="min-h-11 rounded border border-panel-border px-3 text-zinc-300"
                  >
                    ✎ {f.review_status === "added" ? "Edit" : "Correct this finding"}
                  </button>
                  {(f.review_status === "corrected" || f.review_status === "added" || f.review_status === "rejected") && (
                    <button
                      type="button"
                      onClick={() => void undo(f.id)}
                      className="min-h-11 rounded border border-panel-border px-3 text-zinc-300"
                    >
                      {f.review_status === "added" ? "Remove" : f.review_status === "rejected" ? "Undo rejection" : "Undo correction"}
                    </button>
                  )}
                </div>
              )}
              {review && editing === f.id && (
                <div className="pl-8">
                  <FindingEditor
                    title={`Correct ${TYPE_LABEL[f.type].toLowerCase()} ${f.id}`}
                    initial={{ type: f.type, severity: f.severity, description: f.description, reason: f.review_note ?? "" }}
                    submitLabel="Save correction"
                    onSubmit={(edit) => saveCorrection(f.id, edit)}
                    onCancel={() => setEditing(null)}
                  />
                </div>
              )}
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
              {f.type === "box_damage" && !off && inventory[skus[f.id]?.trim() ?? ""] && (
                <p className="mt-2 pl-8 text-xs text-zinc-300" aria-label={`Stock preview for ${f.id}`}>
                  On approval: {skus[f.id].trim()} −{UNITS_PER_DAMAGED_BOX} available ({inventory[skus[f.id].trim()].available} →{" "}
                  {Math.max(0, inventory[skus[f.id].trim()].available - UNITS_PER_DAMAGED_BOX)}), +{UNITS_PER_DAMAGED_BOX} quarantine
                </p>
              )}
              {!off && (
                <details className="mt-2 pl-8 text-xs text-zinc-400">
                  <summary className="min-h-11 cursor-pointer py-3">Location and instructions for the technician (optional)</summary>
                  <label className="flex flex-col gap-1">
                    Location (aisle, zone, rack)
                    <input
                      className="min-h-11 rounded border border-panel-border bg-background px-3 text-sm text-foreground"
                      value={details[f.id]?.location ?? ""}
                      maxLength={300}
                      disabled={locked}
                      onChange={(e) => setDetail(f.id, "location", e.target.value)}
                    />
                  </label>
                  <label className="mt-2 flex flex-col gap-1">
                    Instructions
                    <textarea
                      className="min-h-11 rounded border border-panel-border bg-background px-3 py-2 text-sm text-foreground"
                      value={details[f.id]?.instructions ?? ""}
                      maxLength={1000}
                      disabled={locked}
                      onChange={(e) => setDetail(f.id, "instructions", e.target.value)}
                    />
                  </label>
                </details>
              )}
            </li>
          );
        })}
      </ul>
      {addForm}
      <datalist id="sku-options">
        {Object.entries(inventory).map(([sku, item]) => (
          <option key={sku} value={sku} label={item.name ? `${sku} · ${item.name}` : sku} />
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

function AddMissed({
  boxes,
  findings,
  open,
  setOpen,
  onAdd,
}: {
  boxes: DetectedObject[];
  findings: Finding[];
  open: boolean;
  setOpen: (open: boolean) => void;
  onAdd: (objectId: string, edit: FindingEdit) => Promise<void>;
}) {
  const numbers = boxNumbers(boxes);
  const candidates = boxes.filter((b) => b.label === "box");
  const [objectId, setObjectId] = useState(candidates[0]?.id ?? "");
  if (candidates.length === 0) return null;
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className="min-h-11 rounded-md border border-dashed border-panel-border px-3 text-sm text-zinc-300">
        ＋ Add a finding the model missed
      </button>
    );
  }
  const describe = (box: DetectedObject) => {
    const { damage, blocked } = boxStatus(box, findings);
    const state = [damage && "damaged", blocked && "blocks walkway"].filter(Boolean).join(", ") || "OK";
    return `Box #${numbers.get(box.id)} (now: ${state})`;
  };
  return (
    <FindingEditor
      title="Add a missed finding"
      initial={{ type: "box_damage", severity: "medium", description: "" }}
      submitLabel="Add finding"
      onSubmit={(edit) => onAdd(objectId, edit)}
      onCancel={() => setOpen(false)}
    >
      <label className="flex flex-col gap-1">
        Box (numbers match the photo)
        <select
          className="min-h-11 w-full rounded border border-panel-border bg-background px-3 py-2 text-sm text-foreground"
          value={objectId}
          onChange={(e) => setObjectId(e.target.value)}
        >
          {candidates.map((b) => (
            <option key={b.id} value={b.id}>
              {describe(b)}
            </option>
          ))}
        </select>
      </label>
    </FindingEditor>
  );
}
