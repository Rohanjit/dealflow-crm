// Notifications in the header: just a bell (decision 60). Outlined when there's nothing, filled with
// a count when something is waiting (tasks due today or overdue, access requests, stuck deals,
// follow-ups, relationships going cold), and it shakes when something new arrives. "Clear all" hides
// what's showing until something new comes in (kept per person on the server). Notices also pop up
// when a task falls due or someone assigns you one while Dealflow is open.

import { ActionIcon, Anchor, Badge, Button, Divider, Group, Popover, ScrollArea, Stack, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconBell, IconBellFilled } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { notifyError } from "../components/errors";
import { useMe } from "../context";
import { useCold, useColdAction, useReminders, type ColdItem, type Reminder } from "./api";
import { useAccessRequests, useDecide, type AccessRequest } from "../deals/Restricted";
import { useInsightAlerts, useSnooze, type Alerts } from "../insights/api";
import { dueText } from "./TaskRow";
import { formatDateTime } from "../format";

export default function TaskBell() {
  const me = useMe();
  const nav = useNavigate();
  const { data } = useReminders();
  const cold = useCold().data;
  const coldAction = useColdAction();
  const [acting, setActing] = useState<string | null>(null);
  const asks = useAccessRequests(!!me.perms.deals?.view).data;
  const decide = useDecide();
  const alerts = useInsightAlerts().data;
  const snooze = useSnooze();
  // Sign-ins from a browser not used in the last 90 days (accounts: new_device).
  const signins = useQuery({
    queryKey: ["security-alerts"], refetchInterval: 5 * 60_000,
    queryFn: () => api<{ id: number; device: string; ip: string; created_at: string; current: boolean }[]>("auth/security-alerts/", { background: true }),
  }).data;
  // Files the virus scanner refused (admins only; the server sends nobody else anything).
  const refusals = useQuery({
    queryKey: ["scan-refusals"], refetchInterval: 5 * 60_000, enabled: !!me.role.is_admin,
    queryFn: () => api<{ id: number; at: string; place: string; file_name: string; signature: string; user: string | null }[]>("security/refusals/", { background: true }),
  }).data;
  const secAlerts = useQuery({
    queryKey: ["admin-security-alerts"], refetchInterval: 5 * 60_000, enabled: !!me.role.is_admin,
    queryFn: () => api<{ key: string; title: string; detail: string }[]>("security/admin-alerts/", { background: true }),
  }).data;
  const qc = useQueryClient();
  const clearedQ = useQuery({ queryKey: ["cleared-alerts"], queryFn: () => api<{ keys: string[] }>("notifications/cleared/") });
  const cleared = useMemo(() => new Set(clearedQ.data?.keys ?? []), [clearedQ.data]);

  // Every item the bell could show, with a key that changes when the item means something new
  // (a task going from today to overdue, a deal stuck at a later stage, another investor idling).
  const keys = useMemo(() => {
    const k: string[] = [];
    for (const s of signins ?? []) k.push(`signin:${s.id}`);
    for (const r of refusals ?? []) k.push(`virus:${r.id}`);
    for (const a of secAlerts ?? []) k.push(a.key);
    for (const r of asks?.to_decide ?? []) k.push(`ask:${r.id}`);
    for (const t of data?.overdue ?? []) k.push(`task:${t.id}:overdue`);
    for (const t of data?.today ?? []) k.push(`task:${t.id}:today`);
    for (const st of alerts?.stuck ?? []) k.push(`stuck:${st.module}:${st.id}:${st.stage}`);
    if (alerts && alerts.counts.stuck > alerts.stuck.length) k.push(`more:stuck:${alerts.counts.stuck - alerts.stuck.length}`);
    for (const m of alerts?.meeting ?? []) k.push(`meeting:${m.module}:${m.id}:${m.note}`);
    for (const t of alerts?.tracker ?? []) k.push(`tracker:${t.id}:${t.count}`);
    for (const q of alerts?.quiet ?? []) k.push(`quiet:${q.module}:${q.id}`);
    for (const c of cold?.items ?? []) k.push(`cold:${c.module}:${c.id}`);
    for (const [m, n] of Object.entries(cold?.more ?? {})) if (n) k.push(`more:cold:${m}:${n}`);
    return k;
  }, [asks, data, alerts, cold, signins, refusals, secAlerts]);
  const show = (key: string) => !cleared.has(key);
  const moreCount = (key: string) => Number(key.split(":").pop()) || 0;
  const total = keys.filter(show).reduce((sum, k) => sum + (k.startsWith("more:") ? moreCount(k) : 1), 0);
  const hiddenCount = keys.filter((k) => cleared.has(k)).length;

  async function saveCleared(next: string[], message: string) {
    const prev = clearedQ.data;
    qc.setQueryData(["cleared-alerts"], { keys: next });
    try {
      await api("notifications/cleared/", { method: "PUT", body: { keys: next } });
      notifications.show({ color: "teal", message });
    } catch (e) {
      qc.setQueryData(["cleared-alerts"], prev);
      notifyError(e, "Couldn't update notifications");
    }
  }
  const clearAll = () => saveCleared(keys, "Notifications cleared. New ones will show here as they arrive.");
  const bringBack = () => saveCleared([], "Cleared notifications are back.");
  const [opened, setOpened] = useState(false);
  const seen = useRef<Map<number, "overdue" | "today"> | null>(null);
  // The bell rings (a short wiggle) whenever the count goes up.
  const [ring, setRing] = useState(0);
  const lastCount = useRef<number | null>(null);
  const mountedAt = useRef(Date.now());
  useEffect(() => {
    if (!data && !cold && !asks && !alerts && !signins) return;
    // The first few seconds are the lists arriving one by one, not new notifications.
    const settling = Date.now() - mountedAt.current < 4000;
    if (!settling && lastCount.current !== null && total > lastCount.current) setRing((r) => r + 1);
    lastCount.current = total;
  }, [data, cold, asks, alerts, signins, total]);

  useEffect(() => {
    // Wait for the cleared list too, so the "overdue" notice on opening leaves out what was cleared.
    if (!data || !clearedQ.data) return;
    const current = new Map<number, "overdue" | "today">([
      ...data.overdue.map((t) => [t.id, "overdue"] as const),
      ...data.today.map((t) => [t.id, "today"] as const),
    ]);
    const open = (t: Reminder) => nav(`/m/tasks/${t.id}`);
    if (seen.current === null) {
      const late = data.overdue.filter((t) => !cleared.has(`task:${t.id}:overdue`)).length;
      if (late) {
        notifications.show({
          color: "red",
          title: late === 1 ? "1 overdue task" : `${late} overdue tasks`,
          message: <Anchor component={Link} to="/m/tasks" size="sm">Open my tasks</Anchor>,
        });
      }
    } else {
      for (const t of data.overdue) {
        // A task with a time whose time has just come.
        if (t.due_time && seen.current.get(t.id) === "today") {
          notifications.show({ color: "orange", title: "Due now", autoClose: false,
            message: <Anchor size="sm" onClick={() => open(t)}>{t.title}</Anchor> });
        }
      }
      for (const t of [...data.overdue, ...data.today]) {
        if (!seen.current.has(t.id) && t.assigned_by) {
          notifications.show({ color: "navy", title: `${t.assigned_by} assigned you a task`, autoClose: 15_000,
            message: <Anchor size="sm" onClick={() => open(t)}>{t.title}</Anchor> });
        }
      }
    }
    seen.current = current;
  }, [data, nav, clearedQ.data]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!data && !cold && !asks && !alerts && !signins) return null;
  const toDecide = (asks?.to_decide ?? []).filter((r) => show(`ask:${r.id}`));
  const newSignins = (signins ?? []).filter((x) => show(`signin:${x.id}`));
  const newRefusals = (refusals ?? []).filter((x) => show(`virus:${x.id}`));
  const newSec = (secAlerts ?? []).filter((x) => show(x.key));
  const askRow = (r: AccessRequest) => (
    <div key={`ask-${r.id}`}>
      <Anchor component={Link} to={`/m/deals/${r.deal}`} size="sm" fw={500} onClick={() => setOpened(false)}>{r.deal_title}</Anchor>
      <Text size="xs" c="dimmed">{r.what} · asked by {r.requested_by.name}</Text>
      <Group gap={6} mt={4}>
        <Button size="compact-xs" color="teal" loading={acting === `ask${r.id}a`}
          onClick={async () => { setActing(`ask${r.id}a`); await decide(r, "approve"); setActing(null); }}>Approve</Button>
        <Button size="compact-xs" variant="default" loading={acting === `ask${r.id}d`}
          onClick={async () => { setActing(`ask${r.id}d`); await decide(r, "decline"); setActing(null); }}>Decline</Button>
      </Group>
    </div>
  );
  const overdue = (data?.overdue ?? []).filter((t) => show(`task:${t.id}:overdue`));
  const today = (data?.today ?? []).filter((t) => show(`task:${t.id}:today`));
  const coldItems = (cold?.items ?? []).filter((c) => show(`cold:${c.module}:${c.id}`));
  const act = async (c: ColdItem, a: "contacted" | "snooze") => {
    setActing(`${c.module}:${c.id}:${a}`);
    await coldAction(c, a);
    setActing(null);
  };
  const coldRow = (c: ColdItem) => (
    <div key={`${c.module}-${c.id}`}>
      <Anchor component={Link} to={`/m/${c.module}/${c.id}`} size="sm" fw={500} onClick={() => setOpened(false)}>{c.title}</Anchor>
      <Text size="xs" c="dimmed">
        {c.last_contacted ? `Last contacted ${ago(c.days)} ago` : `Never contacted · added ${ago(c.days)} ago`}
        {c.unowned ? " · no owner" : ""}
      </Text>
      <Group gap={6} mt={4}>
        <Button size="compact-xs" variant="light" loading={acting === `${c.module}:${c.id}:contacted`} onClick={() => act(c, "contacted")}>Contacted today</Button>
        <Button size="compact-xs" variant="subtle" color="gray" loading={acting === `${c.module}:${c.id}:snooze`} onClick={() => act(c, "snooze")}>Snooze 30 days</Button>
      </Group>
    </div>
  );
  const close = () => setOpened(false);
  const later = async (kind: keyof Alerts["counts"], it: { module: string; id: number; title: string; subtitle: string }, forever = false) => {
    setActing(`${kind}:${it.module}:${it.id}`);
    await snooze(kind, it, forever);
    setActing(null);
  };
  const snoozeBtn = (kind: keyof Alerts["counts"], it: { module: string; id: number; title: string; subtitle: string }) => (
    <Button size="compact-xs" variant="subtle" color="gray" loading={acting === `${kind}:${it.module}:${it.id}`}
      onClick={() => later(kind, it)}>Snooze 7 days</Button>
  );
  const recLink = (module: string, id: number, title: string) => (
    <Anchor component={Link} to={`/m/${module}/${id}`} size="sm" fw={500} onClick={close}>{title}</Anchor>
  );
  const stuckItems = (alerts?.stuck ?? []).filter((st) => show(`stuck:${st.module}:${st.id}:${st.stage}`));
  const stuckMore = alerts && alerts.counts.stuck > alerts.stuck.length && show(`more:stuck:${alerts.counts.stuck - alerts.stuck.length}`);
  const coldMore = (["investors", "contacts"] as const).filter((m) => cold?.more[m] && show(`more:cold:${m}:${cold.more[m]}`));
  const followUps = [
    ...(alerts?.meeting ?? []).filter((m) => show(`meeting:${m.module}:${m.id}:${m.note}`)).map((m) => (
      <div key={`meet-${m.module}-${m.id}`}>
        {recLink(m.module, m.id, m.title)}
        <Text size="xs" c="dimmed">You logged a {m.kind} {m.days === 0 ? "today" : `${ago(m.days)} ago`}. No follow-up task yet.</Text>
        <Group gap={6} mt={4}>
          <Button size="compact-xs" variant="light" component={Link} onClick={close}
            to={`/m/tasks/new?related=${encodeURIComponent(m.related)}&return=${encodeURIComponent(`/m/${m.related.replace(":", "/")}`)}`}>Add a task</Button>
          <Button size="compact-xs" variant="subtle" color="gray" loading={acting === `meeting:${m.module}:${m.id}`}
            onClick={() => later("meeting", m, true)}>No follow-up needed</Button>
        </Group>
      </div>
    )),
    ...(alerts?.tracker ?? []).filter((t) => show(`tracker:${t.id}:${t.count}`)).map((t) => (
      <div key={`trk-${t.id}`}>
        {recLink(t.module, t.id, t.title)}
        <Text size="xs" c="dimmed">
          {t.count === 1 ? "1 investor hasn't" : `${t.count} investors haven't`} moved on the tracker for {ago(t.days)}
          {" "}({t.investors.join(", ")}{t.count > t.investors.length ? "…" : ""})
        </Text>
        <Group gap={6} mt={4}>{snoozeBtn("tracker", t)}</Group>
      </div>
    )),
    ...(alerts?.quiet ?? []).filter((q) => show(`quiet:${q.module}:${q.id}`)).map((q) => (
      <div key={`quiet-${q.module}-${q.id}`}>
        {recLink(q.module, q.id, q.title)}
        <Text size="xs" c="dimmed">{q.module === "deals" ? "Deal" : "Outreach"} · no activity for {ago(q.days)}</Text>
        <Group gap={6} mt={4}>{snoozeBtn("quiet", q)}</Group>
      </div>
    )),
  ];
  const item = (t: Reminder, late: boolean) => (
    <div key={t.id}>
      <Anchor component={Link} to={`/m/tasks/${t.id}`} size="sm" fw={500} onClick={() => setOpened(false)}>{t.title}</Anchor>
      <Group gap={6}>
        <Text size="xs" c={late ? "red.7" : "orange.8"}>{dueText(t.due_time ? `${t.due}T${t.due_time}` : t.due, data!.now, me)}</Text>
        {t.related && <Text size="xs" c="dimmed">· {t.related.title}</Text>}
        {t.priority === "high" && <Badge size="xs" variant="light" color="red" tt="none">High</Badge>}
      </Group>
    </div>
  );

  return (
    <Popover opened={opened} onChange={setOpened} position="bottom-end" width={360} shadow="md">
      <Popover.Target>
        <Tooltip label={total ? `Notifications: ${total}` : "Notifications: nothing new"} disabled={opened}>
          <ActionIcon variant="subtle" color={total ? "dark" : "gray"} size="lg" onClick={() => setOpened((o) => !o)}
            className="df-bell" aria-label={`Notifications: ${total}`} data-filled={total ? "" : undefined}>
            <span key={ring} className={ring ? "df-ring df-bell-icon" : "df-bell-icon"}>
              {total ? <IconBellFilled size={20} /> : <IconBell size={20} stroke={1.7} />}
            </span>
            {total > 0 && <span className="df-bell-count" data-tone={overdue.length ? "red" : "orange"}>{total > 99 ? "99+" : total}</span>}
          </ActionIcon>
        </Tooltip>
      </Popover.Target>
      <Popover.Dropdown p="sm">
        <Group justify="space-between" mb="xs" wrap="nowrap">
          <Text fw={600} size="sm">Notifications</Text>
          {total > 0 && <Button size="compact-xs" variant="subtle" color="gray" onClick={clearAll}>Clear all</Button>}
        </Group>
        {total === 0 ? (
          <Stack gap={4}>
            <Text size="sm" c="dimmed">You're all caught up.</Text>
            {hiddenCount > 0 && (
              <Anchor size="xs" component="button" type="button" onClick={bringBack} style={{ alignSelf: "flex-start" }}>
                Show {hiddenCount} cleared {hiddenCount === 1 ? "notification" : "notifications"}
              </Anchor>
            )}
          </Stack>
        ) : (
          <ScrollArea.Autosize mah={420}>
            <Stack gap="sm">
              {newSignins.length + newRefusals.length + newSec.length > 0 && <Text size="xs" fw={600} c="red.8" tt="uppercase">Security</Text>}
              {newSec.map((x) => (
                <div key={x.key}>
                  <Text size="sm" fw={500}>{x.title}</Text>
                  <Text size="xs" c="dimmed">{x.detail}</Text>
                  <Group gap={6} mt={4}>
                    <Button size="compact-xs" variant="subtle" component={Link} to="/settings/security" onClick={close}>Open the security checklist</Button>
                  </Group>
                </div>
              ))}
              {newRefusals.map((x) => (
                <div key={`virus-${x.id}`}>
                  <Text size="sm" fw={500}>Harmful file refused: {x.file_name}</Text>
                  <Text size="xs" c="dimmed">{formatDateTime(x.at, me)} · {x.signature} · {x.place}{x.user ? ` · ${x.user}` : ""}. It wasn't stored.</Text>
                  <Group gap={6} mt={4}>
                    <Button size="compact-xs" variant="subtle" component={Link} to="/settings/security" onClick={close}>See virus scanning</Button>
                  </Group>
                </div>
              ))}
              {newSignins.map((x) => (
                <div key={`signin-${x.id}`}>
                  <Text size="sm" fw={500}>New sign-in: {x.device}</Text>
                  <Text size="xs" c="dimmed">{formatDateTime(x.created_at, me)} · {x.ip}{x.current ? " · this browser" : ""}</Text>
                  <Group gap={6} mt={4}>
                    <Button size="compact-xs" variant="light" onClick={() => saveCleared([...cleared, `signin:${x.id}`], "Thanks. Marked as you.")}>It was me</Button>
                    <Button size="compact-xs" variant="subtle" color="red" component={Link} to="/settings/preferences" onClick={close}>Not me: review devices</Button>
                  </Group>
                </div>
              ))}
              {toDecide.length > 0 && <Text size="xs" fw={600} c="navy.7" tt="uppercase">Access requests</Text>}
              {toDecide.map(askRow)}
              {overdue.length > 0 && <Text size="xs" fw={600} c="red.8" tt="uppercase">Overdue</Text>}
              {overdue.map((t) => item(t, true))}
              {today.length > 0 && <Text size="xs" fw={600} c="orange.8" tt="uppercase">Today</Text>}
              {today.map((t) => item(t, false))}
              {stuckItems.length > 0 && (
                <Text size="xs" fw={600} c="orange.8" tt="uppercase">Stuck · {alerts!.counts.stuck}</Text>
              )}
              {stuckItems.map((st) => (
                <div key={`stuck-${st.module}-${st.id}`}>
                  {recLink(st.module, st.id, st.title)}
                  <Text size="xs" c="dimmed">
                    {st.module === "deals" ? "Deal" : "Outreach"} · {st.days} days at {st.stage_label}
                    {st.source === "learned" && st.usual != null ? ` (usually ${Math.round(st.usual)})` : ` (flagged after ${st.flag_after})`}
                  </Text>
                  <Group gap={6} mt={4}>{snoozeBtn("stuck", st)}</Group>
                </div>
              ))}
              {stuckMore && (["deals", "outreach"] as const).map((m) => (
                <Anchor key={m} component={Link} to={`/m/${m}?stuck=1`} size="xs" onClick={close}>
                  See every stuck {m === "deals" ? "deal" : "outreach attempt"}
                </Anchor>
              ))}
              {followUps.length > 0 && <Text size="xs" fw={600} c="navy.7" tt="uppercase">Follow-ups</Text>}
              {followUps}
              {coldItems.length > 0 && (
                <Text size="xs" fw={600} c="dimmed" tt="uppercase" mt={overdue.length + today.length ? 4 : 0}>
                  Going cold
                </Text>
              )}
              {coldItems.map(coldRow)}
              {coldMore.map((m) => (
                <Anchor key={m} component={Link} to={`/m/${m}?stale=1`} size="xs" onClick={() => setOpened(false)}>
                  {cold!.more[m]} more {m} going cold
                </Anchor>
              ))}
            </Stack>
          </ScrollArea.Autosize>
        )}
        {data && (
          <>
            <Divider my="sm" />
            <Button size="xs" variant="light" fullWidth component={Link} to="/m/tasks" onClick={() => setOpened(false)}>Open my tasks</Button>
          </>
        )}
      </Popover.Dropdown>
    </Popover>
  );
}

/** "12 days", "3 weeks", "4 months", "2 years". */
function ago(days: number) {
  if (days < 14) return `${days} ${days === 1 ? "day" : "days"}`;
  if (days < 60) return `${Math.floor(days / 7)} weeks`;
  if (days < 730) return `${Math.floor(days / 30)} months`;
  return `${Math.floor(days / 365)} years`;
}
