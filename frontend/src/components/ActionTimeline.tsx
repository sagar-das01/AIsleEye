import { type ActionRecord, type ApproveResult, type ActionStatus, pdfUrl } from "@/lib/api";
import { summarizeArgs } from "@/lib/scan";

const STATUS: Record<ActionStatus, { icon: string; label: string; style: string }> = {
  executed: { icon: "✓", label: "Done", style: "text-ok border-ok" },
  rejected: { icon: "⛔", label: "Blocked by guard", style: "text-danger border-danger" },
  invalid: { icon: "⚠", label: "Invalid request", style: "text-hazard border-hazard" },
  failed: { icon: "✕", label: "Failed", style: "text-danger border-danger" },
};

const TOOL_LABEL: Record<string, string> = {
  create_incident_ticket: "Create incident ticket",
  adjust_inventory: "Quarantine inventory",
  log_compliance_event: "Log compliance event",
};

export function ActionTimeline({ result }: { result: ApproveResult }) {
  const ticketById = new Map(result.tickets.map((t) => [t.ticket_id, t]));
  return (
    <div className="mt-3 flex flex-col gap-3">
      {result.actions.length === 0 && <p className="text-sm text-zinc-400">The agent took no actions.</p>}
      <ol className="flex flex-col gap-2">
        {result.actions.map((action, i) => (
          <ActionRow key={i} action={action} ticket={ticketById.get(String(action.result?.ticket_id ?? ""))} />
        ))}
      </ol>
      {result.summary && <p className="text-sm text-zinc-300">{result.summary}</p>}
      {!result.within_iteration_cap && (
        <p role="status" className="text-xs text-hazard">
          The agent stopped at its step limit; some actions may be missing.
        </p>
      )}
    </div>
  );
}

function ActionRow({ action, ticket }: { action: ActionRecord; ticket?: ApproveResult["tickets"][number] }) {
  const status = STATUS[action.status];
  return (
    <li className="rounded-md border border-panel-border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded border px-1.5 py-0.5 text-xs ${status.style}`}>
          {status.icon} {status.label}
        </span>
        <strong>{TOOL_LABEL[action.tool] ?? action.tool}</strong>
      </div>
      <p className="mt-1 break-words font-mono text-xs text-zinc-400">{summarizeArgs(action.args)}</p>
      {action.reason && <p className="mt-1 text-xs text-zinc-300">{action.reason}</p>}
      {ticket && (
        <a href={pdfUrl(ticket)} target="_blank" rel="noreferrer" className="mt-2 inline-flex min-h-11 items-center text-hazard underline">
          Open ticket {ticket.ticket_id} (PDF)
        </a>
      )}
    </li>
  );
}
