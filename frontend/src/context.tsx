import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, type ReactNode } from "react";

import { api } from "./api";
import type { AppInfo, Me, ModuleSchema, Schema, UserLookup } from "./types";

type Ctx = {
  info: AppInfo;
  me: Me;
  refresh: () => Promise<void>;
};

const AppCtx = createContext<Ctx | null>(null);

/** After signing in or out: forget everything cached for the previous user and reload who's signed in.
 *  (queryClient.clear() alone leaves the screen showing the old state.) */
export async function resetSession(qc: QueryClient) {
  qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "app" });
  await qc.refetchQueries({ queryKey: ["app"] });
}

// Why the person was signed out (idle or the 12-hour limit), shown once on the sign-in page.
let signedOutReason = "";
export function takeSignedOutReason(): string {
  const r = signedOutReason;
  signedOutReason = "";
  return r;
}

export function useAppInfo() {
  return useQuery({ queryKey: ["app"], queryFn: () => api<AppInfo>("app/"), staleTime: 60_000 });
}

export function AppProvider({ info, children }: { info: AppInfo; children: ReactNode }) {
  const qc = useQueryClient();
  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: ["app"] });
  };
  useEffect(() => {
    const onPassword = () => qc.invalidateQueries({ queryKey: ["app"] });
    // Signed out (idle, 12 hours, or elsewhere): drop every cached record from memory too.
    const onOut = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail;
      if (typeof detail === "string" && detail) signedOutReason = detail;
      resetSession(qc);
    };
    window.addEventListener("dealflow:signed-out", onOut);
    window.addEventListener("dealflow:password-change", onPassword);
    return () => {
      window.removeEventListener("dealflow:signed-out", onOut);
      window.removeEventListener("dealflow:password-change", onPassword);
    };
  }, [qc]);
  if (!info.user) return null;
  return <AppCtx.Provider value={{ info, me: info.user, refresh }}>{children}</AppCtx.Provider>;
}

export function useApp(): Ctx {
  const c = useContext(AppCtx);
  if (!c) throw new Error("useApp outside AppProvider");
  return c;
}

export function useMe(): Me {
  return useApp().me;
}

export function useSchema() {
  return useQuery({ queryKey: ["schema"], queryFn: () => api<Schema>("schema/"), staleTime: 5 * 60_000 });
}

export function useModuleSchema(key: string | undefined): { schema?: Schema; module?: ModuleSchema; isLoading: boolean } {
  const q = useSchema();
  return { schema: q.data, module: q.data?.modules.find((m) => m.key === key), isLoading: q.isLoading };
}

export function useUsers() {
  return useQuery({ queryKey: ["users-lookup"], queryFn: () => api<UserLookup[]>("users/lookup/"), staleTime: 60_000 });
}
