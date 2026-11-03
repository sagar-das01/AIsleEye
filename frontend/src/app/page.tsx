import { BackendStatus } from "@/components/BackendStatus";

const KPIS = [
  { label: "Scans today", value: "—" },
  { label: "Open tickets", value: "—" },
  { label: "Units quarantined", value: "—" },
  { label: "Walkway hazards", value: "—" },
];

export default function Home() {
  return (
    <div className="flex min-h-full flex-1 flex-col">
      <header className="flex items-center justify-between border-b border-panel-border px-6 py-4">
        <div className="flex items-center gap-3">
          <div className="h-6 w-6 rotate-45 border-2 border-hazard" />
          <h1 className="text-lg font-semibold tracking-wide">
            <span className="text-hazard">AI</span>sleEye
          </h1>
          <span className="text-xs uppercase tracking-widest text-zinc-500">Warehouse Floor Copilot</span>
        </div>
        <BackendStatus />
      </header>

      <section className="grid grid-cols-2 gap-px border-b border-panel-border bg-panel-border md:grid-cols-4">
        {KPIS.map((kpi) => (
          <div key={kpi.label} className="bg-background px-6 py-4">
            <div className="text-xs uppercase tracking-widest text-zinc-500">{kpi.label}</div>
            <div className="mt-1 font-mono text-2xl">{kpi.value}</div>
          </div>
        ))}
      </section>

      <main className="grid flex-1 gap-6 p-6 lg:grid-cols-[1fr_420px]">
        <section className="flex min-h-[480px] items-center justify-center rounded-lg border border-dashed border-panel-border bg-panel">
          <p className="text-sm text-zinc-500">Upload a warehouse photo to scan</p>
        </section>

        <aside className="flex flex-col gap-6">
          <div className="rounded-lg border border-panel-border bg-panel p-4">
            <h2 className="text-xs uppercase tracking-widest text-zinc-500">Findings</h2>
            <p className="mt-3 text-sm text-zinc-500">No scan yet.</p>
          </div>
          <div className="rounded-lg border border-panel-border bg-panel p-4">
            <h2 className="text-xs uppercase tracking-widest text-zinc-500">Agent actions</h2>
            <p className="mt-3 text-sm text-zinc-500">Approve findings to dispatch tickets.</p>
          </div>
        </aside>
      </main>
    </div>
  );
}
