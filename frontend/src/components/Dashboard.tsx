"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ActionTimeline } from "@/components/ActionTimeline";
import { DemoGallery } from "@/components/DemoGallery";
import { FindingsList } from "@/components/FindingsList";
import { KpiStrip } from "@/components/KpiStrip";
import { ScanOverlay } from "@/components/ScanOverlay";
import { UploadPanel } from "@/components/UploadPanel";
import { api, type ApproveResult, type DemoPhoto, type Inventory, type ScanResult, type Ticket } from "@/lib/api";
import { describeError, type FriendlyError } from "@/lib/scan";

type Phase = "idle" | "scanning" | "scanned" | "approving" | "approved";

interface Failure extends FriendlyError {
  action?: { kind: "scan"; file: File; sku?: string } | { kind: "approve"; ids: string[]; skus: Record<string, string> };
}

const STAGES: [number, string][] = [
  [0, "Uploading photo…"],
  [700, "Segmenting boxes and walkways…"],
  [4000, "Inspecting boxes for damage…"],
];

export function Dashboard() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [stage, setStage] = useState("");
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [result, setResult] = useState<ApproveResult | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [scans, setScans] = useState(0);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [inventory, setInventory] = useState<Inventory>({});
  const [demos, setDemos] = useState<DemoPhoto[]>([]);
  const [demoMode, setDemoMode] = useState("");
  const [suggestedSku, setSuggestedSku] = useState<string | undefined>();
  const busy = useRef(false);

  const loadTotals = useCallback(async () => {
    const [t, i] = await Promise.allSettled([api.tickets(), api.inventory()]);
    if (t.status === "fulfilled") setTickets(t.value);
    if (i.status === "fulfilled") setInventory(i.value);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch of the backend's totals
    void loadTotals();
    void Promise.allSettled([api.demos(), api.health()]).then(([d, h]) => {
      if (d.status === "fulfilled") setDemos(d.value);
      if (h.status === "fulfilled") setDemoMode(h.value.demo_mode ?? "");
    });
  }, [loadTotals]);

  useEffect(() => () => void (imageUrl && URL.revokeObjectURL(imageUrl)), [imageUrl]);

  const runScan = useCallback(
    async (file: File, sku?: string) => {
      if (busy.current) return;
      busy.current = true;
      setFailure(null);
      setResult(null);
      setScan(null);
      setSuggestedSku(sku);
      setImageUrl(URL.createObjectURL(file));
      setPhase("scanning");
      setStage(STAGES[0][1]);
      const timers = STAGES.slice(1).map(([delay, text]) => setTimeout(() => setStage(text), delay));
      try {
        setScan(await api.scan(file));
        setScans((n) => n + 1);
        setPhase("scanned");
      } catch (error) {
        setPhase("idle");
        setImageUrl(null);
        setFailure({ ...describeError(error), action: { kind: "scan", file, sku } });
      } finally {
        timers.forEach(clearTimeout);
        busy.current = false;
      }
    },
    [],
  );

  const runDemo = useCallback(
    async (demo: DemoPhoto) => {
      if (busy.current) return;
      try {
        await runScan(await api.demoPhoto(demo), demo.sku ?? undefined);
      } catch {
        setFailure({ message: "Couldn't load that demo photo. Check that the backend is running.", retryable: false });
      }
    },
    [runScan],
  );

  const runApprove = useCallback(
    async (findingIds: string[], skus: Record<string, string>) => {
      if (busy.current || !scan) return;
      busy.current = true; // a ref, so a fast double click cannot send twice before the state updates
      setFailure(null);
      setPhase("approving");
      try {
        setResult(await api.approve(scan.scan_id, findingIds, skus));
        setPhase("approved");
        void loadTotals();
      } catch (error) {
        setPhase("scanned");
        setFailure({ ...describeError(error), action: { kind: "approve", ids: findingIds, skus } });
      } finally {
        busy.current = false;
      }
    },
    [scan, loadTotals],
  );

  const retry = (action: NonNullable<Failure["action"]>) =>
    void (action.kind === "scan" ? runScan(action.file, action.sku) : runApprove(action.ids, action.skus));

  const replaying = demoMode === "replay" || scan?.replay === true;
  const boxes = scan?.objects.filter((o) => o.label === "box").length ?? 0;
  const hazards = scan?.findings.filter((f) => f.type === "walkway_blocked").length ?? 0;
  const quarantined = Object.values(inventory).reduce((sum, item) => sum + item.quarantine, 0);

  return (
    <>
      <KpiStrip
        items={[
          { label: "Scans this session", value: scans },
          { label: "Open tickets", value: tickets.filter((t) => t.status === "open").length },
          { label: "Units quarantined", value: quarantined },
          { label: "Walkway hazards", value: hazards },
        ]}
      />

      <main className="grid flex-1 gap-6 p-4 sm:p-6 lg:grid-cols-[1fr_420px]">
        <section aria-label="Photo" className="flex min-h-[320px] flex-col gap-4 rounded-lg border border-dashed border-panel-border bg-panel p-3 lg:min-h-[480px]">
          {replaying && (
            <p role="status" aria-label="Replay notice" className="rounded-md border border-hazard bg-background p-3 text-sm text-hazard">
              ⟲ Showing replay results: saved from an earlier analysis, not a live scan.
            </p>
          )}

          {failure && (
            <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-danger p-3 text-sm">
              <span>{failure.message}</span>
              {failure.retryable && failure.action && (
                <button type="button" onClick={() => retry(failure.action!)} className="min-h-11 rounded-md border border-danger px-4">
                  Retry
                </button>
              )}
            </div>
          )}

          {phase === "scanning" && (
            <p role="status" className="animate-pulse text-center text-sm text-zinc-300 motion-reduce:animate-none">
              {stage}
            </p>
          )}

          {scan && imageUrl && <ScanOverlay imageUrl={imageUrl} scan={scan} />}

          {scan && boxes === 0 && (
            <p role="status" className="rounded-md border border-hazard p-3 text-sm text-hazard">
              No freight found in this photo. Try a closer or brighter shot.
            </p>
          )}

          {(phase === "idle" || phase === "scanned" || phase === "approved") && (
            <>
              <UploadPanel busy={false} onFile={(file) => void runScan(file)} />
              <DemoGallery demos={demos} busy={false} replay={demoMode === "replay"} onPick={(demo) => void runDemo(demo)} />
            </>
          )}
        </section>

        <aside className="flex flex-col gap-6">
          <section aria-labelledby="findings-title" className="rounded-lg border border-panel-border bg-panel p-4">
            <h2 id="findings-title" className="text-xs uppercase tracking-widest text-zinc-500">
              Findings
            </h2>
            {scan ? (
              <FindingsList
                key={scan.scan_id}
                findings={scan.findings}
                inventory={inventory}
                defaultSku={suggestedSku}
                pending={phase === "approving"}
                disabled={phase === "approved" || phase === "scanning"}
                onApprove={(ids, skus) => void runApprove(ids, skus)}
              />
            ) : (
              <p className="mt-3 text-sm text-zinc-500">No scan yet.</p>
            )}
          </section>

          <section aria-labelledby="actions-title" className="rounded-lg border border-panel-border bg-panel p-4">
            <h2 id="actions-title" className="text-xs uppercase tracking-widest text-zinc-500">
              Agent actions
            </h2>
            {phase === "approving" && (
              <p role="status" className="mt-3 animate-pulse text-sm text-zinc-300 motion-reduce:animate-none">
                Agent dispatching…
              </p>
            )}
            {result ? (
              <ActionTimeline result={result} />
            ) : (
              phase !== "approving" && <p className="mt-3 text-sm text-zinc-500">Approve findings to dispatch tickets.</p>
            )}
          </section>
        </aside>
      </main>
    </>
  );
}
