"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError, pdfUrl, type Technician, type Ticket, type TicketEvent } from "@/lib/api";
import { describeError, SEVERITY_ICON } from "@/lib/scan";

export const STATUS_LABEL: Record<string, string> = {
  open: "○ Awaiting acknowledgement",
  in_progress: "◐ In progress",
  resolved: "● Resolved",
};

const EVENT_LABEL: Record<string, string> = {
  created: "Ticket created",
  assigned: "Assigned",
  reassigned: "Reassigned",
  acknowledged: "Acknowledged, work started",
  note: "Work note",
  resolved: "Resolved",
  details_updated: "Details updated",
};

const field = "min-h-11 w-full rounded border border-panel-border bg-background px-3 py-2 text-sm";
const button = "min-h-11 rounded-md border border-panel-border px-4 text-sm disabled:opacity-50";

export function TicketDetail({ ticketId, onChanged }: { ticketId: string; onChanged?: () => void }) {
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [history, setHistory] = useState<TicketEvent[]>([]);
  const [error, setError] = useState<{ message: string; stale: boolean } | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [t, h] = await Promise.all([api.ticket(ticketId), api.ticketHistory(ticketId)]);
      setTicket(t);
      setHistory(h);
      setMissing(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setMissing(true);
      else setError({ message: describeError(e).message, stale: false });
    }
  }, [ticketId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch of the selected ticket
    void load();
  }, [load]);

  // Only server-confirmed results are shown; on failure the form keeps its text so nothing typed is lost.
  const act = async (fn: () => Promise<unknown>): Promise<boolean> => {
    if (busy) return false;
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      onChanged?.();
      return true;
    } catch (e) {
      setError({ message: describeError(e).message, stale: e instanceof ApiError && e.status === 409 });
      return false;
    } finally {
      setBusy(false);
    }
  };

  if (missing) return <p className="mt-3 text-sm text-zinc-400">This ticket is not available to you.</p>;
  if (!ticket) return <p role="status" className="mt-3 text-sm text-zinc-400">Loading ticket…</p>;

  const caps = ticket.capabilities ?? { assign: false, acknowledge: false, resolve: false, note: false };

  return (
    <div className="mt-3 flex flex-col gap-4 text-sm">
      <div className="flex flex-col gap-1">
        <h3 className="text-base font-semibold">{ticket.title}</h3>
        <p className="font-mono text-xs text-zinc-400">
          {ticket.ticket_id} · {SEVERITY_ICON[ticket.severity]} {ticket.severity} · {ticket.assignee_team}
        </p>
        <p>{STATUS_LABEL[ticket.status] ?? ticket.status}</p>
        <p className="text-zinc-300">
          Assigned to: {ticket.assigned_to_name ?? <span className="text-hazard">⚑ Unassigned</span>}
        </p>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-zinc-500">Location</dt>
        <dd>{ticket.location ?? "Not provided yet"}</dd>
        <dt className="text-zinc-500">Instructions</dt>
        <dd>{ticket.instructions ?? "Not provided yet"}</dd>
        {ticket.sku && (
          <>
            <dt className="text-zinc-500">SKU</dt>
            <dd>{ticket.sku}</dd>
          </>
        )}
        <dt className="text-zinc-500">Finding</dt>
        <dd>{ticket.description}</dd>
        {ticket.resolution_notes && (
          <>
            <dt className="text-zinc-500">Completion</dt>
            <dd>{ticket.resolution_notes}</dd>
          </>
        )}
      </dl>

      <a href={pdfUrl(ticket)} target="_blank" rel="noreferrer" className="text-hazard underline">
        Creation snapshot (PDF, may be unavailable after retention)
      </a>

      {error && (
        <div role="alert" className="flex flex-wrap items-center gap-3 rounded-md border border-danger p-3">
          <span>{error.message}</span>
          {error.stale && (
            <button type="button" className={button} onClick={() => void load().then(() => setError(null))}>
              Refresh
            </button>
          )}
        </div>
      )}

      {caps.acknowledge && (
        <div className="sticky bottom-0 bg-panel py-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => void act(() => api.acknowledge(ticket.ticket_id, ticket.version))}
            className="min-h-12 w-full rounded-lg bg-hazard px-5 font-semibold text-black disabled:opacity-50"
          >
            {busy ? "Acknowledging…" : "Acknowledge and start"}
          </button>
        </div>
      )}

      {caps.assign && <AssignForm ticket={ticket} busy={busy} act={act} />}
      {caps.note && <NoteForm busy={busy} onSubmit={(text) => act(() => api.addNote(ticket.ticket_id, text))} />}
      {caps.resolve && (
        <ResolveForm busy={busy} onSubmit={(notes) => act(() => api.resolve(ticket.ticket_id, notes, ticket.version))} />
      )}

      <section aria-label="Ticket history">
        <h4 className="text-xs uppercase tracking-widest text-zinc-500">History</h4>
        <ol className="mt-2 flex flex-col gap-2 border-l border-panel-border pl-3">
          {history.map((e) => (
            <li key={e.event_id}>
              <span className="font-semibold">{EVENT_LABEL[e.type] ?? e.type}</span>{" "}
              <span className="text-xs text-zinc-400">
                by {e.actor_name} · {new Date(e.at).toLocaleString()}
              </span>
              {e.note && <p className="text-zinc-300">{e.note}</p>}
              {e.reason && <p className="text-xs text-zinc-400">Reason: {e.reason}</p>}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

function AssignForm({
  ticket,
  busy,
  act,
}: {
  ticket: Ticket;
  busy: boolean;
  act: (fn: () => Promise<unknown>) => Promise<boolean>;
}) {
  const [technicians, setTechnicians] = useState<Technician[] | null>(null);
  const [assignee, setAssignee] = useState("");
  const [location, setLocation] = useState(ticket.location ?? "");
  const [instructions, setInstructions] = useState(ticket.instructions ?? "");
  const [reason, setReason] = useState("");
  const reassigning = Boolean(ticket.assigned_to_user_id);

  useEffect(() => {
    api.technicians(ticket.ticket_id).then(setTechnicians, () => setTechnicians([]));
  }, [ticket.ticket_id]);

  const ready = assignee && location.trim() && instructions.trim() && (!reassigning || reason.trim());

  return (
    <form
      aria-label={reassigning ? "Reassign ticket" : "Assign ticket"}
      className="flex flex-col gap-2 rounded-md border border-panel-border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!ready) return;
        void act(() =>
          api.assign(ticket.ticket_id, {
            assignee_id: assignee,
            expected_version: ticket.version,
            location,
            instructions,
            reason: reassigning ? reason : undefined,
          }),
        ).then((ok) => ok && setReason(""));
      }}
    >
      <h4 className="font-semibold">{reassigning ? "Reassign" : "Assign a technician"}</h4>
      <label className="flex flex-col gap-1">
        Technician
        <select className={field} value={assignee} onChange={(e) => setAssignee(e.target.value)} disabled={busy}>
          <option value="">{technicians === null ? "Loading…" : "Choose a technician"}</option>
          {technicians
            ?.filter((t) => t.user_id !== ticket.assigned_to_user_id)
            .map((t) => (
              <option key={t.user_id} value={t.user_id}>
                {t.display_name} ({t.active_tickets} active)
              </option>
            ))}
        </select>
      </label>
      {technicians?.length === 0 && <p className="text-xs text-hazard">No eligible technician for this team and area.</p>}
      <label className="flex flex-col gap-1">
        Location (aisle, zone, rack)
        <input className={field} value={location} maxLength={300} onChange={(e) => setLocation(e.target.value)} disabled={busy} />
      </label>
      <label className="flex flex-col gap-1">
        Instructions for the technician
        <textarea className={field} value={instructions} maxLength={1000} onChange={(e) => setInstructions(e.target.value)} disabled={busy} />
      </label>
      {reassigning && (
        <label className="flex flex-col gap-1">
          Reason for reassigning
          <input className={field} value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} disabled={busy} />
        </label>
      )}
      <button type="submit" disabled={busy || !ready} className={button}>
        {reassigning ? "Reassign" : "Assign"}
      </button>
    </form>
  );
}

function NoteForm({ busy, onSubmit }: { busy: boolean; onSubmit: (text: string) => Promise<boolean> }) {
  const [text, setText] = useState("");
  return (
    <form
      aria-label="Add work note"
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) void onSubmit(text).then((ok) => ok && setText(""));
      }}
    >
      <label className="flex flex-col gap-1">
        Work note
        <textarea className={field} value={text} maxLength={1000} onChange={(e) => setText(e.target.value)} disabled={busy} />
      </label>
      <button type="submit" disabled={busy || !text.trim()} className={button}>
        Add note
      </button>
    </form>
  );
}

function ResolveForm({ busy, onSubmit }: { busy: boolean; onSubmit: (notes: string) => Promise<boolean> }) {
  const [notes, setNotes] = useState("");
  return (
    <form
      aria-label="Resolve ticket"
      className="flex flex-col gap-2 rounded-md border border-ok p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (notes.trim()) void onSubmit(notes);
      }}
    >
      <label className="flex flex-col gap-1">
        Completion notes (required)
        <textarea className={field} value={notes} maxLength={1000} onChange={(e) => setNotes(e.target.value)} disabled={busy} />
      </label>
      <button
        type="submit"
        disabled={busy || !notes.trim()}
        className="min-h-12 rounded-lg bg-ok px-5 font-semibold text-black disabled:opacity-50"
      >
        {busy ? "Resolving…" : "Resolve ticket"}
      </button>
    </form>
  );
}
