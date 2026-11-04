"use client";

import { AuthGate } from "@/components/AuthGate";
import { BackendStatus } from "@/components/BackendStatus";
import { Workspace } from "@/components/Workspace";

export default function Home() {
  return (
    <div className="flex min-h-full flex-1 flex-col">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-panel-border px-4 py-4 sm:px-6">
        <div className="flex items-center gap-3">
          <div className="h-6 w-6 rotate-45 border-2 border-hazard" />
          <h1 className="text-lg font-semibold tracking-wide">
            <span className="text-hazard">AI</span>sleEye
          </h1>
          <span className="hidden text-xs uppercase tracking-widest text-zinc-500 sm:inline">Warehouse Floor Copilot</span>
        </div>
        <BackendStatus />
      </header>
      <AuthGate>
        <Workspace />
      </AuthGate>
    </div>
  );
}
