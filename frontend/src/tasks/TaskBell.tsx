// Reminders in the header: a bell with the number of tasks due today or overdue, a list on click,
// and a notice when one falls due (or someone assigns you one) while Dealflow is open.

import { ActionIcon, Anchor, Badge, Button, Divider, Group, Indicator, Popover, ScrollArea, Stack, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconBell } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useMe } from "../context";
import { useReminders, type Reminder } from "./api";
import { dueText } from "./TaskRow";

export default function TaskBell() {
  const me = useMe();
  const nav = useNavigate();
  const { data } = useReminders();
  const [opened, setOpened] = useState(false);
  const seen = useRef<Map<number, "overdue" | "today"> | null>(null);

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

  if (!data) return null;
  const item = (t: Reminder, late: boolean) => (
    <div key={t.id}>
      <Anchor component={Link} to={`/m/tasks/${t.id}`} size="sm" fw={500} onClick={() => setOpened(false)}>{t.title}</Anchor>
      <Group gap={6}>
        <Text size="xs" c={late ? "red.7" : "orange.8"}>{dueText(t.due_time ? `${t.due}T${t.due_time}` : t.due, data.now, me)}</Text>
        {t.related && <Text size="xs" c="dimmed">· {t.related.title}</Text>}
        {t.priority === "high" && <Badge size="xs" variant="light" color="red" tt="none">High</Badge>}
      </Group>
    </div>
  );

  return (
    <Popover opened={opened} onChange={setOpened} position="bottom-end" width={340} shadow="md">
      <Popover.Target>
        <Tooltip label="Reminders" disabled={opened}>
          <Indicator label={data.count} size={16} disabled={!data.count} color={data.overdue.length ? "red" : "orange"} offset={4}>
            <ActionIcon variant="subtle" color="gray" size="lg" aria-label={`Reminders: ${data.count}`} onClick={() => setOpened((o) => !o)}>
              <IconBell size={20} />
            </ActionIcon>
          </Indicator>
        </Tooltip>
      </Popover.Target>
      <Popover.Dropdown p="sm">
        {data.count === 0 ? (
          <Text size="sm" c="dimmed">Nothing due today. Enjoy it.</Text>
        ) : (
          <ScrollArea.Autosize mah={360}>
            <Stack gap="sm">
              {data.overdue.length > 0 && <Text size="xs" fw={600} c="red.8" tt="uppercase">Overdue</Text>}
              {data.overdue.map((t) => item(t, true))}
              {data.today.length > 0 && <Text size="xs" fw={600} c="orange.8" tt="uppercase">Today</Text>}
              {data.today.map((t) => item(t, false))}
            </Stack>
          </ScrollArea.Autosize>
        )}
        <Divider my="sm" />
        <Button size="xs" variant="light" fullWidth component={Link} to="/m/tasks" onClick={() => setOpened(false)}>Open my tasks</Button>
      </Popover.Dropdown>
    </Popover>
  );
}
