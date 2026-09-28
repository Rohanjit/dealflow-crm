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

export function useAppInfo() {
  return useQuery({ queryKey: ["app"], queryFn: () => api<AppInfo>("app/"), staleTime: 60_000 });
}

export function AppProvider({ info, children }: { info: AppInfo; children: ReactNode }) {
  const qc = useQueryClient();
  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: ["app"] });
  };
  useEffect(() => {
    const onOut = () => qc.invalidateQueries({ queryKey: ["app"] });
    window.addEventListener("dealflow:signed-out", onOut);
    window.addEventListener("dealflow:password-change", onOut);
    return () => {
      window.removeEventListener("dealflow:signed-out", onOut);
      window.removeEventListener("dealflow:password-change", onOut);
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
