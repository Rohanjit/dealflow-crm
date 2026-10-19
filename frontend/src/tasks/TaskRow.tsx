// One task in a list: tick it off, see when it's due (red once overdue), what it's about and who has it.

import { Anchor, Badge, Checkbox, Group, Text, Tooltip } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";

import { useMe, useUsers } from "../context";
import { formatDate } from "../format";
import type { LinkValue, RecordData } from "../types";
import { useCompleteTask } from "./api";
import { dueState } from "./due";

const PRIORITY: Record<string, { label: string; color: string } | undefined> = {
  high: { label: "High", color: "red" },
  low: { label: "Low", color: "gray" },
};

export function dueText(due: string | null, now: string, prefs: { date_format: string; number_grouping: string }) {
  if (!due) return "";
  const [day, time] = due.split("T");
  const today = now.split("T")[0];
  const label = day === today ? "Today" : formatDate(day, prefs);
  return time ? `${label} ${time}` : label;
}

export default function TaskRow({
  task, now, canEdit, showAssignee = false, showRelated = true,
}: { task: RecordData; now: string; canEdit: boolean; showAssignee?: boolean; showRelated?: boolean }) {
  const me = useMe();
  const { data: users } = useUsers();
  const complete = useCompleteTask();
  // Ticking shows the task as done straight away (tick, strike-through, fade) before the list updates.
  const [ticked, setTicked] = useState<boolean | null>(null);
  const done = ticked ?? task.status === "done";
  const due = task.due as string | null;
  const state = done ? "none" : dueState(due, now);
  const related = task.related as LinkValue | null;
  const assignee = users?.find((u) => u.id === task.assigned_to)?.full_name;
  const priority = PRIORITY[String(task.priority)];
  const meta = [
    showRelated && related ? related : null,
    showAssignee ? (assignee ?? "Nobody assigned") : null,
  ].filter(Boolean);

  return (
    <Group justify="space-between" wrap="nowrap" gap="sm" py={8} px="md" className="df-task-row"
      data-done={done ? "" : undefined} style={ticked !== null && ticked !== (task.status === "done") ? { opacity: 0.55 } : undefined}>
      <Group gap="sm" wrap="nowrap" align="flex-start" style={{ minWidth: 0 }}>
        <Tooltip label={done ? "Mark as not done" : "Mark as done"} disabled={!canEdit}>
          <Checkbox mt={2} radius="xl" checked={done} disabled={!canEdit} aria-label={`${done ? "Reopen" : "Complete"} ${task._title}`}
            onChange={(e) => {
              const next = e.currentTarget.checked;
              setTicked(next);
              window.setTimeout(() => { complete(task, next).finally(() => setTicked(null)); }, 420);
            }} />
        </Tooltip>
        <div style={{ minWidth: 0 }}>
          <Anchor component={Link} to={`/m/tasks/${task.id}`} size="sm" fw={500} className="df-task-title"
            style={{ wordBreak: "break-word" }}>{task._title}</Anchor>
          {meta.length > 0 && (
            <Group gap={6} wrap="wrap">
              {meta.map((m, i) => (
                <Group key={i} gap={6} wrap="nowrap">
                  {i > 0 && <Text size="xs" c="dimmed">·</Text>}
                  {typeof m === "string" ? <Text size="xs" c="dimmed">{m}</Text> : (
                    me.perms[(m as LinkValue).module]?.view
                      ? <Anchor component={Link} to={`/m/${(m as LinkValue).module}/${(m as LinkValue).id}`} size="xs">{(m as LinkValue).title}</Anchor>
                      : <Text size="xs" c="dimmed">{(m as LinkValue).title}</Text>
                  )}
                </Group>
              ))}
            </Group>
          )}
        </div>
      </Group>
      <Group gap={6} wrap="nowrap" style={{ flexShrink: 0 }}>
        {task.status === "in_progress" && <Badge size="sm" variant="light" color="navy" tt="none">In progress</Badge>}
        {priority && !done && <Badge size="sm" variant="light" color={priority.color} tt="none">{priority.label}</Badge>}
        {due && (
          <Text size="sm" c={state === "overdue" ? "red.7" : state === "today" ? "orange.8" : "dimmed"} fw={state === "overdue" ? 600 : undefined}
            style={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
            {dueText(due, now, me)}
          </Text>
        )}
      </Group>
    </Group>
  );
}
