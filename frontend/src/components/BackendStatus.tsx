"use client";

import { useEffect, useState } from "react";

import { api, type Health } from "@/lib/api";

type State = { kind: "loading" } | { kind: "ok"; health: Health } | { kind: "down" };

export function BackendStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    api
      .health()
      .then((health) => setState({ kind: "ok", health }))
      .catch(() => setState({ kind: "down" }));
  }, []);

  const dot =
    state.kind === "ok" ? "bg-emerald-400" : state.kind === "down" ? "bg-red-500" : "bg-zinc-500 animate-pulse";
  const label =
    state.kind === "ok"
      ? `${state.health.models.segmentation} · ${state.health.models.vision_agent}`
      : state.kind === "down"
        ? "Backend offline"
        : "Connecting…";

  return (
    <div className="flex items-center gap-2 font-mono text-xs text-zinc-400">
      <span className={`h-2 w-2 rounded-full ${dot}`} />
      {label}
    </div>
  );
}
