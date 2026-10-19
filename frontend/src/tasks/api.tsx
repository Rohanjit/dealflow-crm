// Shared bits for Tasks & Reminders: the reminders feed (polled every minute) and completing a task
// with an Undo.

import { Button, Group, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { notifyError } from "../components/errors";
import { useApp } from "../context";
import type { RecordData } from "../types";

export type Reminder = {
  id: number;
  title: string;
  due: string | null;
  due_time: string | null;
  priority: "low" | "medium" | "high";
  status: string;
  related: { module: string; id: number; title: string } | null;
  assigned_by: string | null;
};
export type Reminders = { now: string; overdue: Reminder[]; today: Reminder[]; count: number };

/** An investor or contact nobody has been in touch with for a while (backend core/stale.py). */
export type ColdItem = {
  module: "investors" | "contacts"; id: number; title: string; subtitle: string;
  last_contacted: string | null; days: number; unowned: boolean;
};
export type Cold = { items: ColdItem[]; count: number; more: Partial<Record<"investors" | "contacts", number>>; days: Record<string, number> };

export function useCold() {
  return useQuery({
    queryKey: ["stale"],
    queryFn: () => api<Cold>("stale/"),
    refetchInterval: 5 * 60_000,
  });
}

/** "Contacted today" or "Snooze 30 days" on a going-cold reminder. */
export function useColdAction() {
  const qc = useQueryClient();
  return async (item: ColdItem, action: "contacted" | "snooze") => {
    try {
      const fresh = await api<Cold>(`stale/${item.module}/${item.id}/`, { body: { action } });
      qc.setQueryData(["stale"], fresh);
      await qc.invalidateQueries({ queryKey: ["records", item.module] });
      await qc.invalidateQueries({ queryKey: ["record", item.module, String(item.id)] });
      notifications.show({ color: "teal", message: action === "contacted" ? `${item.title}: last contacted set to today.` : `${item.title}: snoozed for 30 days.` });
    } catch (e) {
      notifyError(e, "Couldn't update");
    }
  };
}

export function useReminders() {
  const { info, me } = useApp();
  return useQuery({
    queryKey: ["task-alerts"],
    queryFn: () => api<Reminders>("tasks/alerts/"),
    refetchInterval: 60_000,
    refetchIntervalInBackground: true,
    enabled: info.built_modules.includes("tasks") && !!me.perms.tasks?.view,
  });
}

/** Tick a task done (or back to open), refresh every list that shows it, and offer Undo. */
export function useCompleteTask() {
  const qc = useQueryClient();
  const refresh = () => Promise.all([
    qc.invalidateQueries({ queryKey: ["records", "tasks"] }),
    qc.invalidateQueries({ queryKey: ["record", "tasks"] }),
    qc.invalidateQueries({ queryKey: ["task-alerts"] }),
  ]);
  async function set(task: RecordData, status: string) {
    await api(`m/tasks/${task.id}/`, { method: "PATCH", body: { status } });
    await refresh();
  }
  return async function complete(task: RecordData, done: boolean) {
    const before = String(task.status ?? "open");
    try {
      await set(task, done ? "done" : "open");
    } catch (e) {
      notifyError(e, "Couldn't update the task");
      return;
    }
    const id = `task-${task.id}`;
    notifications.show({
      id,
      color: "teal",
      message: (
        <Group gap="xs" justify="space-between" wrap="nowrap">
          <Text size="sm">{done ? "Done" : "Reopened"}: {task._title}</Text>
          <Button size="compact-xs" variant="subtle" onClick={async () => {
            notifications.hide(id);
            try { await set(task, before); } catch (e) { notifyError(e, "Couldn't undo"); }
          }}>Undo</Button>
        </Group>
      ),
    });
  };
}
