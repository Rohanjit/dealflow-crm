// The Vault's second lock: a per-person PIN that opens the Vault for a while in this browser session.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { api, ApiError } from "../api";

export type VaultStatus = {
  can_view: boolean;
  has_pin: boolean;
  open: boolean;
  open_until: string | null;
  locked_out_until: string | null;
  idle_minutes: number;
  accepted: string[];
  max_file_mb: number;
  sample_pin: string | null;
  // False while the virus scanner isn't answering: uploads are refused until it's back (decision 63, S5).
  uploads_allowed?: boolean;
};

export const VAULT_CODES = new Set(["vault_locked", "vault_pin_required", "vault_locked_out"]);

export function isVaultLock(e: unknown): boolean {
  return e instanceof ApiError && e.status === 403 && VAULT_CODES.has(e.code ?? "");
}

export function useVaultStatus(enabled = true) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["vault-status"], queryFn: () => api<VaultStatus>("vault/status/"), enabled, staleTime: 30_000 });
  useEffect(() => {
    // Any Vault request refused because it closed (idle, or locked elsewhere) brings the PIN screen back.
    const onLocked = () => qc.invalidateQueries({ queryKey: ["vault-status"] });
    window.addEventListener("dealflow:vault-locked", onLocked);
    return () => window.removeEventListener("dealflow:vault-locked", onLocked);
  }, [qc]);
  return q;
}

/** Refresh everything that depends on whether the Vault is open. */
export async function afterVaultChange(qc: ReturnType<typeof useQueryClient>, status?: VaultStatus) {
  if (status) qc.setQueryData(["vault-status"], status);
  await Promise.all([
    qc.invalidateQueries({ queryKey: ["vault-status"] }),
    qc.invalidateQueries({ queryKey: ["records", "vault"] }),
    qc.invalidateQueries({ queryKey: ["record", "vault"] }),
    qc.invalidateQueries({ queryKey: ["search"] }),
  ]);
}

export const downloadUrl = (id: number, inline = false) => `/api/vault/${id}/download/${inline ? "?inline=1" : ""}`;
export const opensInBrowser = (fileType: unknown) => fileType === "PDF" || fileType === "Image";
