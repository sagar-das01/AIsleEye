"use client";

import { useRef, useState } from "react";

import { validateUpload } from "@/lib/scan";

interface Props {
  busy: boolean;
  onFile: (file: File) => void;
}

export function UploadPanel({ busy, onFile }: Props) {
  const [problem, setProblem] = useState<string | null>(null);
  const camera = useRef<HTMLInputElement>(null);
  const gallery = useRef<HTMLInputElement>(null);

  function handle(file: File | undefined) {
    if (!file) return;
    const error = validateUpload(file);
    setProblem(error);
    if (!error) onFile(file);
  }

  const button =
    "min-h-12 rounded-lg border border-panel-border px-5 text-sm font-medium transition-colors enabled:hover:border-hazard disabled:opacity-50";

  return (
    <div className="flex flex-col items-center gap-4 p-6 text-center">
      <p className="text-sm text-zinc-400">Upload a warehouse photo to scan</p>
      <div className="flex flex-wrap justify-center gap-3">
        <button type="button" className={`${button} bg-hazard text-black`} disabled={busy} onClick={() => camera.current?.click()}>
          Take photo
        </button>
        <button type="button" className={`${button} bg-panel`} disabled={busy} onClick={() => gallery.current?.click()}>
          Choose from gallery
        </button>
      </div>
      <input
        ref={camera}
        data-testid="camera-input"
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        onChange={(e) => {
          handle(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      <input
        ref={gallery}
        data-testid="gallery-input"
        type="file"
        accept="image/jpeg,image/png,image/webp"
        hidden
        onChange={(e) => {
          handle(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      {problem && (
        <p role="alert" className="text-sm text-danger">
          {problem}
        </p>
      )}
    </div>
  );
}
