// Insights (decision 59): stuck alerts and follow-up nudges for the Reminders bell, when a stage
// counts as stuck, and the win-rate analytics behind the Insights page.

import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { notifyError } from "../components/errors";
import { useApp } from "../context";

type Ref = { module: string; id: number; title: string; subtitle: string };
export type StuckItem = Ref & { stage: string; stage_label: string; days: number; flag_after: number; usual: number | null; source: Source };
export type QuietItem = Ref & { days: number };
export type TrackerItem = Ref & { count: number; days: number; investors: string[] };
export type MeetingItem = Ref & { note: number; kind: "meeting" | "call"; date: string; days: number; related: string };
export type Alerts = {
  stuck: StuckItem[]; quiet: QuietItem[]; tracker: TrackerItem[]; meeting: MeetingItem[];
  counts: Record<"stuck" | "quiet" | "tracker" | "meeting", number>; count: number;
};
export type Source = "learned" | "default" | "set";
export type StageRule = {
  stage: string; label: string; days: number; source: Source; default: number; median: number | null; samples: number; learned: number | null;
};

export function useInsightAlerts() {
  const { me } = useApp();
  return useQuery({
    queryKey: ["insight-alerts"],
    queryFn: () => api<Alerts>("insights/alerts/", { background: true }),
    refetchInterval: 5 * 60_000,
    enabled: !!(me.perms.outreach?.view || me.perms.deals?.view),
  });
}

/** Snooze one alert for a week, or (meetings) set it aside for good. */
export function useSnooze() {
  const qc = useQueryClient();
  return async (kind: keyof Alerts["counts"], item: Ref, forever = false) => {
    try {
      const fresh = await api<Alerts>("insights/alerts/snooze/", { body: { kind, module: item.module, id: item.id, forever } });
      qc.setQueryData(["insight-alerts"], fresh);
      notifications.show({ color: "teal", message: forever ? `${item.title}: no follow-up needed.` : `${item.title}: snoozed for 7 days.` });
    } catch (e) {
      notifyError(e, "Couldn't snooze");
    }
  };
}

/** When a record counts as stuck, per stage (for record pages and board cards). */
export function useStageRules(module?: string) {
  const { me } = useApp();
  const ok = module === "outreach" || module === "deals";
  return useQuery({
    queryKey: ["stage-rules"],
    queryFn: () => api<Record<string, StageRule[]>>("insights/stages/"),
    enabled: ok && !!me.perms[module!]?.view,
    staleTime: 10 * 60_000,
    select: (d) => (ok ? d[module!] ?? [] : []),
  });
}

/** Days a record has been in its current stage, and whether that's longer than usual. */
export function stuckInfo(rules: StageRule[] | undefined, stage: unknown, status: unknown, entered: unknown, created: string | undefined, today = new Date()) {
  if (!rules || status !== "open" || typeof stage !== "string") return null;
  const rule = rules.find((r) => r.stage === stage);
  const since = typeof entered === "string" && entered ? entered : created?.slice(0, 10);
  if (!rule || !rule.days || !since) return null;
  const t = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  const [y, m, d] = since.split("-").map(Number);
  const days = Math.round((t - Date.UTC(y, m - 1, d)) / 86_400_000);
  return days > rule.days ? { days, rule } : null;
}

export type Breakdown = { label: string; total: number; won: number; lost: number; open: number; rate: number | null };
export type FunnelStep = { stage: string; label: string; reached: number; from_previous: number | null };
export type Analytics = {
  period: string; since: string | null; today: string;
  outreach: null | {
    total: number; won: number; lost: number; open: number; paused: number; win_rate: number | null;
    funnel: FunnelStep[]; median_days_to_mandate: number | null; mandates_timed: number;
    by: Record<"source" | "sector" | "transaction_type" | "size", Breakdown[]>;
    size_currency: string | null; size_other_currencies: number;
    lost_reasons: { label: string; count: number }[];
  };
  deals: null | {
    total: number; closed: number; open: number; on_hold: number; withdrawn: number; dropped: number; close_rate: number | null;
    funnel: FunnelStep[]; median_days_to_close: number | null; closes_timed: number;
    by: Record<"transaction_type" | "sector" | "source", Breakdown[]>;
  };
  investors: null | {
    entries: number; engaged: number; rate: number | null; investors: number;
    top: { id: number; name: string; deals: number; engaged: number; term_sheets: number; selected: number; rate: number }[];
  };
  stages: Record<string, StageRule[]>;
  can_see_team: boolean;
  bankers: null | {
    id: number | null; name: string; outreach: number; won: number; lost: number; win_rate: number | null;
    deals: number; closed: number; deals_lost: number; close_rate: number | null;
  }[];
};

export function useAnalytics(period: string) {
  return useQuery({
    queryKey: ["analytics", period],
    queryFn: () => api<Analytics>(`insights/analytics/?period=${period}`),
    placeholderData: (prev) => prev,
  });
}
