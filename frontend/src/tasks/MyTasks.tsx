// "My tasks": the signed-in person's unfinished tasks, grouped into Overdue, Today, Upcoming and
// No due date, with a quick way to add one.

import { Badge, Button, Group, Paper, Stack, Text, TextInput } from "@mantine/core";
import { Skeleton } from "../components/ui";
import { DateInput } from "@mantine/dates";
import { IconPlus } from "@tabler/icons-react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useState } from "react";

import { api } from "../api";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import { useMe } from "../context";
import { dateParser } from "../module/fields";
import type { ModuleSchema, Page, RecordData } from "../types";
import { useReminders } from "./api";
import { dueState, localNow, type DueState } from "./due";
import TaskRow from "./TaskRow";
import { MeetingRow } from "../sync/MeetingRow";
import { useAgenda } from "../sync/api";

const GROUPS: { state: DueState; label: string; color: string }[] = [
  { state: "overdue", label: "Overdue", color: "red" },
  { state: "today", label: "Today", color: "orange" },
  { state: "upcoming", label: "Upcoming", color: "navy" },
  { state: "none", label: "No due date", color: "gray" },
];

export default function MyTasks({ module, params }: { module: ModuleSchema; params: URLSearchParams }) {
  const me = useMe();
  const qc = useQueryClient();
  const { data: reminders } = useReminders();
  const now = reminders?.now ?? localNow();
  const q = new URLSearchParams(params);
  q.set("view", "mine");
  q.delete("page");
  if (!q.get("ordering")) q.set("ordering", "due,-priority");
  q.set("page_size", "500");
  const { data, isLoading, error } = useQuery({
    queryKey: ["records", "tasks", "mine", q.toString()],
    queryFn: () => api<Page<RecordData>>(`m/tasks/?${q}`),
    placeholderData: keepPreviousData,
  });
  const [title, setTitle] = useState("");
  const [due, setDue] = useState<Date | null>(null);
  const [busy, setBusy] = useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    try {
      await api("m/tasks/", { body: { title: title.trim(), due: due ? dayjs(due).format("YYYY-MM-DD") : null } });
      setTitle("");
      setDue(null);
      notifyOk("Task added.");
      await qc.invalidateQueries({ queryKey: ["records", "tasks"] });
      await qc.invalidateQueries({ queryKey: ["task-alerts"] });
    } catch (err) {
      notifyError(err, "Couldn't add the task");
    } finally {
      setBusy(false);
    }
  }

  const rows = data?.results ?? [];
  const { data: agenda } = useAgenda();
  const today = dayjs(now).format("YYYY-MM-DD");
  const meetings = (agenda?.meetings ?? []);
  const meetingsIn = (state: DueState) => meetings.filter((m) =>
    state === "today" ? dayjs(m.at).format("YYYY-MM-DD") <= today : state === "upcoming" ? dayjs(m.at).format("YYYY-MM-DD") > today : false);
  const grouped = GROUPS.map((g) => ({ ...g, rows: rows.filter((r) => dueState(r.due as string | null, now) === g.state),
    meetings: meetingsIn(g.state) }));

  return (
    <Stack gap="md" maw={980}>
      {module.perms.create && (
        <form onSubmit={add}>
          <Group gap="xs" align="flex-end" wrap="wrap">
            <TextInput style={{ flex: 1, minWidth: 260 }} placeholder="Add a task for yourself, e.g. Send teaser to Northwind"
              aria-label="New task" value={title} onChange={(e) => setTitle(e.currentTarget.value)} />
            <DateInput w={170} placeholder="Due date (optional)" aria-label="Due date" clearable valueFormat={me.date_format}
              dateParser={dateParser(me.date_format)} value={due} onChange={setDue} />
            <Button type="submit" leftSection={<IconPlus size={16} />} loading={busy} disabled={!title.trim()}>Add task</Button>
          </Group>
          <Text size="xs" c="dimmed" mt={6}>Tick the circle next to a task when it's done. Click a task's name to see or change its details. Meetings from your calendar show here too.</Text>
        </form>
      )}
      {isLoading ? (
        <Stack gap={10}>{[0, 1, 2, 3].map((i) => <Skeleton key={i} h={34} />)}</Stack>
      ) : error ? (
        <Text c="red" size="sm">{errorMessage(error)}</Text>
      ) : rows.length === 0 && meetings.length === 0 ? (
        <Paper withBorder radius="md" p="xl">
          <Text fw={600} ta="center">Nothing on your list</Text>
          <Text size="sm" c="dimmed" ta="center">Tasks assigned to you show here until they're done.</Text>
        </Paper>
      ) : (
        grouped.filter((g) => g.rows.length || g.meetings.length).map((g) => (
          <Paper key={g.state} withBorder radius="md" className="df-fade">
            <Group gap="xs" px="md" pt="sm" pb={6}>
              <Text fw={600} size="sm" c={g.state === "overdue" ? "red.8" : undefined}>{g.label}</Text>
              <Badge size="sm" variant="light" color={g.color}>{g.rows.length + g.meetings.length}</Badge>
            </Group>
            <div>
              {g.meetings.map((m) => <MeetingRow key={`m${m.id}`} meeting={m} />)}
              {g.rows.map((r) => <TaskRow key={r.id} task={r} now={now} canEdit={module.perms.edit} />)}
            </div>
          </Paper>
        ))
      )}
    </Stack>
  );
}
