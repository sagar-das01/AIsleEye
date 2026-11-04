import { API_URL, type DemoPhoto } from "@/lib/api";

interface Props {
  demos: DemoPhoto[];
  busy: boolean;
  replay: boolean;
  onPick: (demo: DemoPhoto) => void;
}

export function DemoGallery({ demos, busy, replay, onPick }: Props) {
  if (demos.length === 0) return null;
  return (
    <section aria-labelledby="demo-title" className="flex flex-col gap-3 border-t border-panel-border pt-4">
      <h3 id="demo-title" className="text-xs uppercase tracking-widest text-zinc-500">
        Or try a demo photo
      </h3>
      {replay && <p className="text-xs text-zinc-400">Replay mode: only the demo photos have saved results.</p>}
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {demos.map((demo) => (
          <li key={demo.id}>
            <button
              type="button"
              disabled={busy}
              onClick={() => onPick(demo)}
              className="flex min-h-11 w-full flex-col gap-1 rounded-md border border-panel-border p-2 text-left text-xs transition-colors enabled:hover:border-hazard disabled:opacity-50"
            >
              {/* eslint-disable-next-line @next/next/no-img-element -- small thumbnail served by the API */}
              <img src={`${API_URL}${demo.image_url}`} alt="" className="aspect-video w-full rounded object-cover" loading="lazy" />
              <span className="font-medium text-zinc-200">{demo.title}</span>
              <span className="text-zinc-400">{demo.description}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
