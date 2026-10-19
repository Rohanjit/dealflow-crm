// Tasks about one record (an outreach attempt, deal, investor or contact), on that record's page.

import { Badge, Button, Group, Paper, Stack, Text, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconPlus } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, qs } from "../api";
import { errorMessage } from "../components/errors";
import { useModuleSchema } from "../context";
import type { ModuleSchema, Page, RecordData, Section } from "../types";
import { useReminders } from "./api";
import { localNow } from "./due";
import TaskRow from "./TaskRow";
import { Skeleton } from "../components/ui";

export default function TasksPanel({ section, parent, record }: { section: Section; parent: ModuleSchema; record: RecordData }) {
  const { module: tasks } = useModuleSchema("tasks");
  const { data: reminders } = useReminders();
  const now = reminders?.now ?? localNow();
  const [showDone, setShowDone] = useState(false);
  const link = `${parent.key}:${record.id}`;
  const { data, isLoading, error } = useQuery({
    queryKey: ["records", "tasks", "about", link],
    queryFn: () => api<Page<RecordData>>(`m/tasks/${qs({ related: link, ordering: section.order ?? "status,due", page_size: 200 })}`),
  });
  if (!tasks) return null;
  const rows = data?.results ?? [];
  const open = rows.filter((r) => r.status !== "done");
  const done = rows.filter((r) => r.status === "done");
  const back = `/m/${parent.key}/${record.id}`;

  return (
    <Paper withBorder radius="md">
      <Group justify="space-between" px="lg" pt="md" pb="sm">
        <Group gap="xs">
          <Text fw={600}>{section.label}</Text>
          {data && <Badge variant="light" color="gray" size="sm">{open.length}</Badge>}
        </Group>
        {tasks.perms.create && (
          <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} component={Link}
            to={`/m/tasks/new${qs({ related: link, return: back })}`}>Add task</Button>
        )}
      </Group>
      {isLoading ? (
        <Stack gap={8} px="lg" pb="lg"><Skeleton h={26} /><Skeleton h={26} w="70%" /></Stack>
      ) : error ? (
        <Text c="red" size="sm" px="lg" pb="lg">{errorMessage(error)}</Text>
      ) : rows.length === 0 ? (
        <Text size="sm" c="dimmed" px="lg" pb="lg">No tasks yet.</Text>
      ) : (
        <div style={{ paddingBottom: 8 }}>
          {open.map((r) => <TaskRow key={r.id} task={r} now={now} canEdit={tasks.perms.edit} showAssignee showRelated={false} />)}
          {open.length === 0 && <Text size="sm" c="dimmed" px="lg" pb={4}>All done.</Text>}
          {done.length > 0 && (
            <>
              <UnstyledButton px="lg" py={6} onClick={() => setShowDone((v) => !v)}>
                <Group gap={4}>
                  {showDone ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
                  <Text size="xs" c="dimmed">{done.length} done</Text>
                </Group>
              </UnstyledButton>
              {showDone && done.map((r) => <TaskRow key={r.id} task={r} now={now} canEdit={tasks.perms.edit} showAssignee showRelated={false} />)}
            </>
          )}
        </div>
      )}
    </Paper>
  );
}
