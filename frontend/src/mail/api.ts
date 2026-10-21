// Email from Dealflow: the person's own Gmail or Outlook, single emails, templates, campaigns.

import { useQuery } from "@tanstack/react-query";

import { api } from "../api";

export type MailAccount = {
  provider: "gmail" | "outlook";
  provider_label: string;
  email: string;
  connected_at: string;
  needs_reconnect: boolean;
  last_error: string;
  sent_today: number;
  daily_limit: number;
};

export type MailInfo = {
  account: MailAccount | null;
  providers: { key: "gmail" | "outlook"; label: string; ready: boolean }[];
  can_campaign: boolean;
  merge_fields: { key: string; label: string }[];
  in_sample: boolean;
};

export type Attachment = {
  kind: "vault" | "upload";
  id?: number;
  token?: string;
  name: string;
  size: number;
  content_type?: string;
  type?: string;
};

export type Person = {
  name: string; email: string; contact_id: number; kind: string; firm: string; designation: string;
  primary: boolean; opted_out: boolean;
};

export type Template = { id: number; name: string; subject: string; body: string; shared: boolean; mine: boolean; owner: string; updated_at: string };

export type Counts = { queued: number; sending: number; sent: number; failed: number; skipped: number; total: number };

export type Campaign = {
  id: number; name: string; status: "draft" | "scheduled" | "sending" | "paused" | "done" | "cancelled";
  status_label: string; owner: { id: number; name: string }; mine: boolean; subject: string;
  send_at: string | null; paused_reason: string; resume_at: string | null; created_at: string;
  started_at: string | null; finished_at: string | null; counts: Counts;
  // Only on one campaign
  body?: string; attachments?: Attachment[]; problems?: string[]; opt_out_text?: string; editable?: boolean;
  confirmed?: number; result?: { added: number; skipped: number };
};

export type Recipient = {
  id: number; name: string; email: string; contact_id: number | null; status: string; status_label: string;
  reason: string; sent_at: string | null; confirmed: boolean; confirm_status: string | null; web_link: string;
};

export function useMail(enabled = true) {
  return useQuery({ queryKey: ["mail", "account"], queryFn: () => api<MailInfo>("mail/account/"), enabled, staleTime: 30_000 });
}

export function useTemplates(enabled = true) {
  return useQuery({ queryKey: ["mail", "templates"], queryFn: () => api<Template[]>("mail/templates/"), enabled });
}

export async function uploadAttachments(files: File[]): Promise<Attachment[]> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  return api<Attachment[]>("mail/attachments/", { form });
}

export function sizeLabel(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

/** Where an email can be sent from: these record pages get an "Email" button. */
export const EMAIL_MODULES = new Set(["investors", "contacts", "companies", "outreach", "deals", "deal_investors"]);

export const STATUS_COLOR: Record<string, string> = {
  confirmed: "teal", pending: "gray", unconfirmed: "orange",
};
export const STATUS_LABEL: Record<string, string> = {
  confirmed: "Confirmed sent", pending: "Waiting for confirmation", unconfirmed: "Not found in Sent",
};
