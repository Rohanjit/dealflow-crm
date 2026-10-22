// Email and calendar logging (decision 64): the person's connected mailboxes and calendars,
// logged emails and meetings, upcoming meetings and scheduling.

import { useQuery } from "@tanstack/react-query";

import { api } from "../api";

export type SyncKind = "google" | "microsoft" | "icloud" | "other";

export type Connection = {
  id: number;
  kind: SyncKind;
  label: string;
  email: string;
  mail: boolean;
  calendar: boolean;
  connected_at: string;
  synced_at: string | null;
  needs_reconnect: boolean;
  problem: string;
  imap_host?: string;
  imap_port?: number;
  caldav_url?: string;
};

export type SyncInfo = {
  connections: Connection[];
  reminder_minutes: number | null;
  ready: Record<SyncKind, boolean>;
  in_sample: boolean;
};

export type Participant = { name: string; email: string; contact: number | null; role: string; colleague: boolean };

export type Choice = { ref: string; module: string; id: number; title: string; kind: string };

export type Item = {
  id: number;
  kind: "email" | "meeting";
  subject: string;
  at: string;
  end: string | null;
  all_day: boolean;
  participants: Participant[];
  cancelled: boolean;
  attachments: string[];
  can_open: boolean;
  restricted: boolean;
  choices: Choice[];
  links: { module: string; id: number; title: string }[];
  // When opened by the owner
  text?: string;
  description?: string;
  location?: string;
};

export type Agenda = {
  now: string;
  meetings: Item[];
  soon: number[];
  reminder_minutes: number | null;
  to_file: Item[];
};

export function useSync(enabled = true) {
  return useQuery({ queryKey: ["sync"], queryFn: () => api<SyncInfo>("sync/"), enabled });
}

export function useAgenda(enabled = true) {
  return useQuery({
    queryKey: ["sync-agenda"], enabled, refetchInterval: 60_000,
    queryFn: () => api<Agenda>("sync/agenda/", { background: true }),
  });
}

export function useMeetingsAbout(about: string, enabled = true) {
  return useQuery({ queryKey: ["sync-meetings", about], enabled,
    queryFn: () => api<Item[]>(`sync/meetings/?about=${encodeURIComponent(about)}`) });
}

export const KIND_LABEL: Record<SyncKind, string> = {
  google: "Google", microsoft: "Microsoft 365", icloud: "Apple iCloud", other: "Another provider",
};

/** Who an email or meeting was with, in a short line: "Ravi Menon, Priya Shah and 2 more". */
export function withLine(p: Participant[], max = 3): string {
  const outside = p.filter((x) => !x.colleague);
  const names = (outside.length ? outside : p).map((x) => x.name || x.email);
  return names.length <= max ? names.join(", ") : `${names.slice(0, max).join(", ")} and ${names.length - max} more`;
}

export const SYNC_PAGES = new Set(["contacts", "deals", "outreach"]);
