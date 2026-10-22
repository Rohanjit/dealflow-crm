// Investor data rooms (backend rooms/, decision 64): what the deal page's Data room panel and the
// tracker read.

import { useQuery } from "@tanstack/react-query";

import { api } from "../api";

export type Suggestion = { stage: string; label: string; why: string; share?: number };

export type RoomFile = {
  id: number; document: number; title: string; file_name: string; ext: string; file_type: string; size: number;
  mode: "view" | "download"; pages: number; can_view_only: boolean; position: number; added_by: string | null;
  added_at: string; readers: number; seconds: number; downloads: number;
};

export type RoomShare = {
  id: number; entry: number; investor: { id: number; title: string }; name: string; email: string;
  status: "active" | "waiting" | "stopped" | "expired" | "locked" | "closed"; status_label: string;
  expires_at: string; created_at: string; created_by: string | null; invited_at: string | null; invite_error: string;
  nda: { state: "none" | "done" | "waiting"; label: string; at?: string };
  first_opened_at: string | null; last_opened_at: string | null; opens: number; seconds: number; files_read: number;
  downloads: number; open_questions: number; suggestion: Suggestion | null;
};

export type RoomVisit = {
  id: number; at: string; last_at: string; kind: string; label: string; who: string; share: number; entry: number;
  file: string; seconds: number; pages: number[]; trace: string; ip: string;
};

export type RoomQuestion = {
  id: number; share: number; who: string; entry: number; file: string; page: number | null; text: string;
  asked_at: string; answer: string; answered_by: string | null; answered_at: string | null;
};

export type EntrySummary = {
  shares: number; opens: number; seconds: number; files_read: number; last_opened_at: string | null;
  suggestion: (Suggestion & { share: number }) | null; share: number;
};

export type Candidate = {
  entry: number; investor: string; stage: string; status: string; contacts: { id: number; name: string; email: string }[];
};

export type RoomData = {
  deal: number;
  exists: boolean;
  can: { share: boolean; flag: boolean };
  rooms_domain: string | null;
  base_url: string;
  mailbox: boolean;
  nda_modes: { value: string; label: string }[];
  days: number[];
  room: null | {
    id: number; name: string; welcome: string; nda_mode: "none" | "click" | "signed"; nda_text: string; is_open: boolean;
    approved: boolean; approved_by: string | null; approved_at: string | null;
    pending: { id: number; requested_by: string; at: string } | null; approvers: string[];
  };
  files: RoomFile[];
  shares: RoomShare[];
  activity: RoomVisit[];
  questions: RoomQuestion[];
  by_entry: Record<string, EntrySummary>;
  candidates?: Candidate[];
};

export const roomKey = (dealId: number) => ["records", "data-room", dealId]; // under "records" so stage moves refresh it

export function useRoom(dealId: number, enabled = true) {
  return useQuery({
    queryKey: roomKey(dealId),
    queryFn: () => api<RoomData>(`deals/${dealId}/room/`),
    enabled,
    retry: false,
  });
}

/** "12 min", "1 h 5 min", "40 s" */
export function readingTime(seconds: number): string {
  if (seconds < 60) return `${seconds} s`;
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}
