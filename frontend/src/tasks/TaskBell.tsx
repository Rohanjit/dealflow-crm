// Reminders in the header: a bell with the number of tasks due today or overdue (plus relationships
// going cold), a list on click, and a notice when a task falls due (or someone assigns you one)
// while Dealflow is open.

import { Anchor, Badge, Button, Divider, Group, Popover, ScrollArea, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconBell } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useMe } from "../context";
import { useCold, useColdAction, useReminders, type ColdItem, type Reminder } from "./api";
import { useAccessRequests, useDecide, type AccessRequest } from "../deals/Restricted";
import { dueText } from "./TaskRow";

export default function TaskBell() {
  const me = useMe();
  const nav = useNavigate();
  const { data } = useReminders();
  const cold = useCold().data;
  const coldAction = useColdAction();
  const [acting, setActing] = useState<string | null>(null);
  const asks = useAccessRequests(!!me.perms.deals?.view).data;
  const decide = useDecide();
  const total = (data?.count ?? 0) + (cold?.count ?? 0) + (asks?.count ?? 0);
  const [opened, setOpened] = useState(false);
  const seen = useRef<Map<number, "overdue" | "today"> | null>(null);
  // The bell rings (a short wiggle) whenever the count goes up.
  const [ring, setRing] = useState(0);
  const lastCount = useRef<number | null>(null);
  useEffect(() => {
    if (!data && !cold && !asks) return;
    if (lastCount.current !== null && total > lastCount.current) setRing((r) => r + 1);
    lastCount.current = total;
  }, [data, cold, asks, total]);

  useEffect(() => {
    if (!data) return;
    const current = new Map<number, "overdue" | "today">([
      ...data.overdue.map((t) => [t.id, "overdue"] as const),
      ...data.today.map((t) => [t.id, "today"] as const),
    ]);
    const open = (t: Reminder) => nav(`/m/tasks/${t.id}`);
    if (seen.current === null) {
      if (data.overdue.length) {
        notifications.show({
          color: "red",
          title: data.overdue.length === 1 ? "1 overdue task" : `${data.overdue.length} overdue tasks`,
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
  }, [data, nav]);

  if (!data && !cold && !asks) return null;
  const toDecide = asks?.to_decide ?? [];
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
  const overdue = data?.overdue ?? [];
  const today = data?.today ?? [];
  const coldItems = cold?.items ?? [];
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
        <Button variant="subtle" color={overdue.length ? "red" : "gray"} size="compact-sm" onClick={() => setOpened((o) => !o)}
          leftSection={<IconBell size={18} key={ring} className={ring ? "df-ring" : undefined} />}
          aria-label={`Reminders: ${total}`}>
          Reminders
          {total > 0 && <span className="df-count-pill" data-tone={overdue.length ? "red" : "orange"}>{total}</span>}
        </Button>
      </Popover.Target>
      <Popover.Dropdown p="sm">
        {total === 0 ? (
          <Text size="sm" c="dimmed">Nothing due today. Enjoy it.</Text>
        ) : (
          <ScrollArea.Autosize mah={420}>
            <Stack gap="sm">
              {toDecide.length > 0 && <Text size="xs" fw={600} c="navy.7" tt="uppercase">Access requests</Text>}
              {toDecide.map(askRow)}
              {overdue.length > 0 && <Text size="xs" fw={600} c="red.8" tt="uppercase">Overdue</Text>}
              {overdue.map((t) => item(t, true))}
              {today.length > 0 && <Text size="xs" fw={600} c="orange.8" tt="uppercase">Today</Text>}
              {today.map((t) => item(t, false))}
              {coldItems.length > 0 && (
                <Text size="xs" fw={600} c="dimmed" tt="uppercase" mt={overdue.length + today.length ? 4 : 0}>
                  Going cold · {cold!.count} {cold!.count === 1 ? "relationship" : "relationships"}
                </Text>
              )}
              {coldItems.map(coldRow)}
              {(["investors", "contacts"] as const).filter((m) => cold?.more[m]).map((m) => (
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
