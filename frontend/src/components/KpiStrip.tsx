interface Kpi {
  label: string;
  value: string | number;
}

export function KpiStrip({ items }: { items: Kpi[] }) {
  return (
    <section aria-label="Key figures" className="grid grid-cols-2 gap-px border-b border-panel-border bg-panel-border md:grid-cols-3 xl:grid-cols-6">
      {items.map((kpi) => (
        <div key={kpi.label} className="bg-background px-6 py-4">
          <div className="text-xs uppercase tracking-widest text-zinc-500">{kpi.label}</div>
          <div className="mt-1 font-mono text-xl">{kpi.value}</div>
        </div>
      ))}
    </section>
  );
}
