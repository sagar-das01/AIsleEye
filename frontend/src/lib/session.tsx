"use client";

import { createContext, useContext } from "react";

import type { Session } from "./api";

export interface SessionValue {
  session: Session;
  can: (permission: string) => boolean;
  switchRole: () => void;
  signOut: () => void;
}

export const SessionContext = createContext<SessionValue | null>(null);

export function useSession(): SessionValue | null {
  return useContext(SessionContext);
}

export function useCan(): (permission: string) => boolean {
  const value = useContext(SessionContext);
  // Outside a signed-in session (isolated component tests), components keep their full pre-RBAC behaviour;
  // the backend still enforces every permission.
  return value ? value.can : () => true;
}
