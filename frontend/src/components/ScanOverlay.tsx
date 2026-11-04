import type { ScanResult } from "@/lib/api";
import { boxStatus } from "@/lib/scan";

interface Props {
  imageUrl: string;
  scan: ScanResult;
}

const FONT = 14;

// Colour is never the only signal: every state also has a pattern or dash style, an icon and a text label.
export function ScanOverlay({ imageUrl, scan }: Props) {
  const { width, height } = scan.image_size;
  const boxes = scan.objects.filter((o) => o.label === "box");
  const walkways = scan.objects.filter((o) => o.label === "walkway");
  const labelSize = Math.max(FONT, Math.round(width / 45));
  const description = `${boxes.length} box${boxes.length === 1 ? "" : "es"} and ${walkways.length} walkway marking${walkways.length === 1 ? "" : "s"} detected`;

  return (
    <div className="relative w-full overflow-hidden rounded-lg border border-panel-border bg-black">
      {/* eslint-disable-next-line @next/next/no-img-element -- local object URL, not optimisable */}
      <img src={imageUrl} alt="Uploaded warehouse photo" className="block h-auto w-full" />
      <svg
        role="img"
        aria-label={description}
        viewBox={`0 0 ${width} ${height}`}
        className="absolute inset-0 h-full w-full"
        preserveAspectRatio="none"
      >
        <defs>
          <pattern id="hatch-red" width="14" height="14" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="14" height="14" fill="rgba(239,68,68,0.18)" />
            <line x1="0" y1="0" x2="0" y2="14" stroke="#ef4444" strokeWidth="5" />
          </pattern>
          <pattern id="dots-green" width="12" height="12" patternUnits="userSpaceOnUse">
            <rect width="12" height="12" fill="rgba(52,211,153,0.12)" />
            <circle cx="6" cy="6" r="1.6" fill="#34d399" />
          </pattern>
        </defs>

        {walkways.map((w) => (
          <g key={w.id} data-testid={`object-${w.id}`}>
            <path d={w.mask_svg} fill="rgba(250,204,21,0.25)" stroke="#facc15" strokeWidth={4} strokeDasharray="14 8" />
            <Label x={w.bbox.x1} y={w.bbox.y1} size={labelSize} text="Walkway" icon="═" tone="#facc15" />
          </g>
        ))}

        {boxes.map((box) => {
          const { damage, blocked } = boxStatus(box, scan.findings);
          const state = blocked ? "blocked" : damage ? "damaged" : "ok";
          const text = blocked ? "Blocks walkway" : damage ? "Damaged" : "OK";
          return (
            <g key={box.id} data-testid={`object-${box.id}`} data-state={state} className={blocked ? "pulse-once" : undefined}>
              <path
                d={box.mask_svg}
                fill={blocked ? "url(#hatch-red)" : damage ? "rgba(239,68,68,0.25)" : "url(#dots-green)"}
                stroke={blocked || damage ? "#ef4444" : "#34d399"}
                strokeWidth={blocked ? 5 : 3}
              />
              <Label
                x={box.bbox.x1}
                y={box.bbox.y1}
                size={labelSize}
                text={damage && blocked ? "Damaged · blocks walkway" : text}
                icon={blocked ? "⚠" : damage ? "✕" : "✓"}
                tone={blocked || damage ? "#ef4444" : "#34d399"}
              />
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function Label({ x, y, size, text, icon, tone }: { x: number; y: number; size: number; text: string; icon: string; tone: string }) {
  const content = `${icon} ${text}`;
  const w = content.length * size * 0.62 + size;
  const top = Math.max(0, y - size * 1.6);
  return (
    <g>
      <rect x={x} y={top} width={w} height={size * 1.5} fill="#0b0d10" stroke={tone} strokeWidth={1.5} rx={3} />
      <text x={x + size * 0.5} y={top + size * 1.1} fontSize={size} fill="#fff" fontFamily="sans-serif">
        {content}
      </text>
    </g>
  );
}
