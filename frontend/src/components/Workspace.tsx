"use client";

import { useEffect, useState } from "react";

import { AdminPanel } from "@/components/AdminPanel";
import { Dashboard } from "@/components/Dashboard";
import { KpiStrip } from "@/components/KpiStrip";
import { NotificationBell } from "@/components/NotificationBell";
import { StockBoard } from "@/components/StockBoard";
import { TechnicianWorkspace, TicketBoard } from "@/components/TicketQueues";
import { api, type Scores } from "@/lib/api";
import { useSession } from "@/lib/session";

type TabId = "scan" | "tickets" | "stock" | "accounts" | "scores";

export function Workspace() {
  const value = useSession()!;
  const { session, can } = value;
  const technician = can("work") && !can("assign");

  const tabs: { id: TabId; label: string }[] = [];
  if (can("scan")) tabs.push({ id: "scan", label: "Scan" });
  if (can("tickets")) tabs.push({ id: "tickets", label: technician ? "My tickets" : "Tickets" });
  if (can("inventory")) tabs.push({ id: "stock", label: "Stock" });
  if (can("scores") && !can("scan")) tabs.push({ id: "scores", label: "Scores" });
  if (can("users")) tabs.push({ id: "accounts", label: "Accounts" });

  const [tab, setTab] = useState<TabId | undefined>(technician ? "tickets" : tabs[0]?.id);
  const [selected, setSelected] = useState<string | null>(null);
  const [changes, setChanges] = useState(0);
  const bump = () => setChanges((n) => n + 1);

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-panel-border px-4 py-2 sm:px-6">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="rounded border border-hazard px-1.5 text-xs text-hazard" title={session.notice}>
            DEMO · unverified
          </span>
          <strong>{session.display_name}</strong>
        </div>
        <div className="flex items-center gap-2">
          <NotificationBell
            onOpenTicket={(id) => {
              setSelected(id);
              setTab("tickets");
            }}
          />
          <button type="button" onClick={value.switchRole} className="min-h-11 rounded-md border border-panel-border px-3 text-sm">
            Switch role
          </button>
          <button type="button" onClick={value.signOut} className="min-h-11 rounded-md border border-panel-border px-3 text-sm">
            Sign out
          </button>
        </div>
      </div>

      {tabs.length > 1 && (
        <nav role="tablist" aria-label="Workspace" className="flex gap-1 overflow-x-auto border-b border-panel-border px-4 sm:px-6">
          {tabs.map((t) => (
            <button
              key={t.id}
              role="tab"
              type="button"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={`min-h-11 shrink-0 border-b-2 px-4 text-sm ${tab === t.id ? "border-hazard text-hazard" : "border-transparent text-zinc-400"}`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      )}

      {tabs.length === 0 && (
        <main className="p-4 sm:p-6">
          <p className="rounded-md border border-panel-border p-4 text-sm text-zinc-300">
            Your role has no operational screens in this MVP. Notifications addressed to you still appear in the bell above.
          </p>
        </main>
      )}

      {/* The scan view stays mounted so switching tabs never loses a scan under review. */}
      {can("scan") && (
        <div hidden={tab !== "scan"} className="flex flex-1 flex-col">
          <Dashboard onChanged={bump} />
        </div>
      )}
      {tab === "tickets" && (
        <main className="mx-auto w-full max-w-3xl p-4 sm:p-6">
          {technician ? (
            <TechnicianWorkspace selectedId={selected} refreshKey={changes} />
          ) : (
            <TicketBoard selectedId={selected} refreshKey={changes} onChanged={bump} />
          )}
        </main>
      )}
      {tab === "stock" && (
        <main className="mx-auto w-full max-w-3xl p-4 sm:p-6">
          <StockBoard refreshKey={changes} />
        </main>
      )}
      {tab === "scores" && <ScoresView />}
      {tab === "accounts" && (
        <main className="mx-auto w-full max-w-3xl p-4 sm:p-6">
          <AdminPanel selfId={session.user_id} />
        </main>
      )}
    </>
  );
}

function ScoresView() {
  const [scores, setScores] = useState<Scores | null>(null);
  useEffect(() => {
    api.scores().then(setScores, () => setScores(null));
  }, []);
  return (
    <KpiStrip
      items={[
        { label: "Pipeline agreement", value: scores?.pipeline_agreement.label ?? "n/a" },
        { label: "Agent score", value: scores?.agent_score.label ?? "n/a" },
      ]}
    />
  );
}
