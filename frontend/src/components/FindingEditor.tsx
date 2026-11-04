"use client";

import { useState } from "react";

import type { FindingEdit, FindingType, Severity } from "@/lib/api";
import { describeError } from "@/lib/scan";

export const TYPE_LABEL: Record<FindingType, string> = { box_damage: "Damaged box", walkway_blocked: "Blocked walkway" };
const SEVERITIES: Severity[] = ["low", "medium", "high"];
const field = "min-h-11 w-full rounded border border-panel-border bg-background px-3 py-2 text-sm text-foreground";

interface Props {
  title: string;
  initial: FindingEdit;
  submitLabel: string;
  /** Rejects with the API error; the form stays open with the text kept so nothing typed is lost. */
  onSubmit: (edit: FindingEdit) => Promise<void>;
  onCancel: () => void;
  children?: React.ReactNode;
}

export function FindingEditor({ title, initial, submitLabel, onSubmit, onCancel, children }: Props) {
  const [type, setType] = useState<FindingType>(initial.type);
  const [severity, setSeverity] = useState<Severity>(initial.severity);
  const [description, setDescription] = useState(initial.description);
  const [reason, setReason] = useState(initial.reason ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (pending || !description.trim()) return;
    setPending(true);
    setError(null);
    try {
      await onSubmit({ type, severity, description: description.trim(), reason: reason.trim() });
    } catch (e) {
      setError(describeError(e).message);
      setPending(false);
    }
  };

  return (
    <form
      aria-label={title}
      className="mt-2 flex flex-col gap-2 rounded-md border border-hazard p-3 text-sm"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <strong>{title}</strong>
      {children}
      <label className="flex flex-col gap-1">
        Finding type
        <select className={field} value={type} disabled={pending} onChange={(e) => setType(e.target.value as FindingType)}>
          {Object.entries(TYPE_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1">
        Severity
        <select className={field} value={severity} disabled={pending} onChange={(e) => setSeverity(e.target.value as Severity)}>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1">
        What is actually wrong
        <textarea className={field} value={description} maxLength={500} disabled={pending} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1">
        Why (optional, kept for evaluation)
        <input className={field} value={reason} maxLength={300} disabled={pending} onChange={(e) => setReason(e.target.value)} />
      </label>
      {error && (
        <p role="alert" className="rounded-md border border-danger p-2">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="submit"
          disabled={pending || !description.trim()}
          className="min-h-11 rounded-md bg-hazard px-4 font-semibold text-black disabled:opacity-50"
        >
          {pending ? "Saving…" : submitLabel}
        </button>
        <button type="button" disabled={pending} onClick={onCancel} className="min-h-11 rounded-md border border-panel-border px-4">
          Cancel
        </button>
      </div>
    </form>
  );
}
