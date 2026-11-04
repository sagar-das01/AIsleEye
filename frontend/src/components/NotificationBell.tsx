"use client";

import { useCallback, useEffect, useState } from "react";

import { api, type Inbox } from "@/lib/api";

export const POLL_MS = 15_000;

const EVENT_TEXT: Record<string, string> = { assigned: "New assignment", reassigned: "Reassigned to you" };

/** Foreground in-app inbox: polls while open and refreshes on refocus. Nothing is delivered while the app is closed. */
export function NotificationBell({ onOpenTicket }: { onOpenTicket?: (ticketId: string) => void }) {
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      setInbox(await api.notifications());
    } catch {
      // Keep the last known inbox; the next poll or refocus tries again.
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial inbox fetch, then polling
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    const refresh = () => document.visibilityState === "visible" && void load();
    window.addEventListener("focus", refresh);
    window.addEventListener("online", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("online", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [load]);

  const unread = inbox?.unread ?? 0;

  const pick = async (id: string, ticketId: string, current: boolean) => {
    try {
      await api.readNotification(id); // reading never acknowledges the ticket
    } finally {
      void load();
    }
    setOpen(false);
    if (current) onOpenTicket?.(ticketId);
  };

  return (
    <div className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-label={`Notifications, ${unread} unread`}
        onClick={() => setOpen((v) => !v)}
        className="flex min-h-11 min-w-11 items-center justify-center gap-1 rounded-md border border-panel-border px-3 text-sm"
      >
        <span aria-hidden>🔔</span>
        {unread > 0 && <span className="rounded bg-hazard px-1.5 font-mono text-xs text-black">{unread}</span>}
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-2 w-[min(20rem,90vw)] rounded-lg border border-panel-border bg-panel p-3 shadow-lg">
          <p className="text-xs text-zinc-500">In-app only: shown while AIsleEye is open.</p>
          {(inbox?.items.length ?? 0) === 0 ? (
            <p className="mt-2 text-sm text-zinc-400">No notifications.</p>
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {inbox!.items.map((n) => (
                <li key={n.notification_id}>
                  <button
                    type="button"
                    onClick={() => void pick(n.notification_id, n.ticket_id, n.current)}
                    className="flex min-h-11 w-full flex-col items-start rounded-md border border-panel-border p-2 text-left text-sm"
                  >
                    <span className="flex items-center gap-2">
                      {n.read_at === null && <span className="text-hazard">● Unread</span>}
                      <strong>{EVENT_TEXT[n.event_type] ?? n.event_type}</strong>
                    </span>
                    <span className="text-zinc-300">{n.title}</span>
                    <span className="font-mono text-xs text-zinc-500">{n.ticket_id}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
