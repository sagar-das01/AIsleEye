"use client";

import { useCallback, useEffect, useState } from "react";

import { TicketDetail, STATUS_LABEL } from "@/components/TicketDetail";
import { api, type Ticket } from "@/lib/api";
import { describeError, SEVERITY_ICON } from "@/lib/scan";

interface Tab {
  id: string;
  label: string;
  empty: string;
  match: (t: Ticket) => boolean;
}

const MANAGER_TABS: Tab[] = [
  { id: "unassigned", label: "Unassigned", empty: "No unassigned tickets.", match: (t) => t.status !== "resolved" && !t.assigned_to_user_id },
  { id: "awaiting", label: "Awaiting ack", empty: "Nothing is waiting for a technician.", match: (t) => t.status === "open" && Boolean(t.assigned_to_user_id) },
  { id: "in_progress", label: "In progress", empty: "No work in progress.", match: (t) => t.status === "in_progress" },
  { id: "resolved", label: "Resolved", empty: "No resolved tickets yet.", match: (t) => t.status === "resolved" },
];

const TECHNICIAN_TABS: Tab[] = [
  { id: "new", label: "New", empty: "No new tickets assigned to you.", match: (t) => t.status === "open" },
  { id: "in_progress", label: "In progress", empty: "Nothing in progress.", match: (t) => t.status === "in_progress" },
  { id: "history", label: "History", empty: "No resolved tickets yet.", match: (t) => t.status === "resolved" },
];

function age(iso?: string): string {
  if (!iso) return "";
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

function TicketQueue({
  title,
  tabs,
  selectedId,
  refreshKey,
  onChanged,
}: {
  title: string;
  tabs: Tab[];
  selectedId?: string | null;
  refreshKey?: number;
  onChanged?: () => void;
}) {
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(tabs[0].id);
  const [open, setOpen] = useState<string | null>(selectedId ?? null);

  const load = useCallback(async () => {
    try {
      setTickets(await api.tickets());
      setError(null);
    } catch (e) {
      setError(describeError(e).message);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- (re)load when the parent signals a change
    void load();
  }, [load, refreshKey]);

  useEffect(() => {
    if (!selectedId) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- a notification picks the ticket to show
    setOpen(selectedId);
  }, [selectedId]);

  useEffect(() => {
    // After a reload, keep the open ticket in view even if its status moved it to another tab (e.g. New → In progress).
    // Manual tab clicks change neither input, so they are never overridden.
    const picked = open ? tickets?.find((t) => t.ticket_id === open) : undefined;
    const home = picked && tabs.find((t) => t.match(picked));
    // eslint-disable-next-line react-hooks/set-state-in-effect -- follows server-confirmed status changes only
    if (home) setTab(home.id);
  }, [tickets, open, tabs]);

  const current = tabs.find((t) => t.id === tab) ?? tabs[0];
  const rows = tickets?.filter(current.match) ?? [];

  return (
    <section aria-labelledby={`${title}-title`} className="rounded-lg border border-panel-border bg-panel p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 id={`${title}-title`} className="text-xs uppercase tracking-widest text-zinc-500">
          {title}
        </h2>
        <button type="button" onClick={() => void load()} className="min-h-11 px-3 text-xs text-zinc-400">
          ↻ Refresh
        </button>
      </div>
      <div role="tablist" aria-label={`${title} views`} className="mt-3 flex flex-wrap gap-2">
        {tabs.map((t) => {
          const count = tickets?.filter(t.match).length ?? 0;
          return (
            <button
              key={t.id}
              role="tab"
              type="button"
              aria-selected={t.id === tab}
              onClick={() => {
                setTab(t.id);
                setOpen(null);
              }}
              className={`min-h-11 rounded-md border px-3 text-sm ${t.id === tab ? "border-hazard text-hazard" : "border-panel-border text-zinc-300"}`}
            >
              {t.label} ({count})
            </button>
          );
        })}
      </div>

      {error && (
        <p role="alert" className="mt-3 rounded-md border border-danger p-3 text-sm">
          {error}
        </p>
      )}
      {tickets === null && !error && <p role="status" className="mt-3 text-sm text-zinc-400">Loading tickets…</p>}
      {tickets !== null && rows.length === 0 && <p className="mt-3 text-sm text-zinc-500">{current.empty}</p>}

      <ul className="mt-3 flex flex-col gap-2">
        {rows.map((t) => (
          <li key={t.ticket_id} className="rounded-md border border-panel-border">
            <button
              type="button"
              aria-expanded={open === t.ticket_id}
              onClick={() => setOpen(open === t.ticket_id ? null : t.ticket_id)}
              className="flex min-h-14 w-full flex-col items-start gap-1 p-3 text-left text-sm"
            >
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs">
                  {SEVERITY_ICON[t.severity]} {t.severity}
                </span>
                <strong>{t.title}</strong>
              </span>
              <span className="text-xs text-zinc-400">
                {t.location ?? "No location yet"} · {STATUS_LABEL[t.status] ?? t.status} · {age(t.created_at)}
                {t.assigned_to_name ? ` · ${t.assigned_to_name}` : ""}
              </span>
            </button>
            {open === t.ticket_id && (
              <div className="border-t border-panel-border px-3 pb-3">
                <TicketDetail
                  ticketId={t.ticket_id}
                  onChanged={() => {
                    void load();
                    onChanged?.();
                  }}
                />
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function TicketBoard(props: { selectedId?: string | null; refreshKey?: number; onChanged?: () => void }) {
  return <TicketQueue title="Tickets" tabs={MANAGER_TABS} {...props} />;
}

export function TechnicianWorkspace(props: { selectedId?: string | null; refreshKey?: number }) {
  return <TicketQueue title="My tickets" tabs={TECHNICIAN_TABS} {...props} />;
}
