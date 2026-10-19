import { Button, Group, Paper, Select, Stack, Table, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { modals } from "@mantine/modals";
import { IconSearch } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Navigate } from "react-router-dom";

import { api, qs } from "../api";
import { useApp } from "../context";
import { notifyError, notifyOk } from "../components/errors";
import { formatDateTime } from "../format";
import { moduleIcon, PageHeader, TableSkeleton } from "../components/ui";

type Item = {
  module: string; module_label: string; id: number; title: string; subtitle: string; archived_at: string; archived_by: string | null;
};

export default function ArchivePage() {
  const { info, me } = useApp();
  const qc = useQueryClient();
  const [module, setModule] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [dq] = useDebouncedValue(q, 250);
  const { data, isLoading } = useQuery({
    queryKey: ["archive", module, dq],
    queryFn: () => api<{ results: Item[] }>(`archive/${qs({ module, q: dq })}`),
    enabled: me.flags.restore_archive,
  });
  if (!me.flags.restore_archive) return <Navigate to="/" replace />;

  async function restore(it: Item) {
    try {
      await api(`archive/${it.module}/${it.id}/`, { method: "POST" });
      notifyOk(`${it.title} restored.`);
      qc.invalidateQueries({ queryKey: ["archive"] });
      qc.invalidateQueries({ queryKey: ["records", it.module] });
    } catch (e) {
      notifyError(e, "Couldn't restore");
    }
  }

  function purge(it: Item) {
    modals.openConfirmModal({
      title: `Delete ${it.title} permanently?`,
      children: <Text size="sm">This can't be undone. The record is removed from Dealflow for good (it stays only in earlier backups).</Text>,
      labels: { confirm: "Delete permanently", cancel: "Cancel" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await api(`archive/${it.module}/${it.id}/`, { method: "DELETE" });
          notifyOk(`${it.title} deleted permanently.`);
          qc.invalidateQueries({ queryKey: ["archive"] });
        } catch (e) {
          notifyError(e, "Couldn't delete");
        }
      },
    });
  }

  const modules = info.modules.filter((m) => info.built_modules.includes(m.key));
  return (
    <Stack maw={1100}>
      <PageHeader icon={moduleIcon("archive")} title="Deleted items"
        sub={<>Anything deleted lands here. Use <b>Restore</b> to bring it back{me.role.is_admin ? <>, or <b>Delete permanently</b> to remove it forever</> : ""}.</>} />
      <Group>
        <TextInput placeholder="Search by name" leftSection={<IconSearch size={16} />} value={q} onChange={(e) => setQ(e.currentTarget.value)} w={300} />
        <Select placeholder="All modules" clearable data={modules.map((m) => ({ value: m.key, label: m.label }))} value={module} onChange={setModule} w={200} />
      </Group>
      <Paper withBorder radius="md" className="df-fade">
        {isLoading ? <TableSkeleton cols={4} rows={5} /> : (
          <Table className="df-table" verticalSpacing="sm" horizontalSpacing="md">
            <Table.Thead>
              <Table.Tr><Table.Th>Name</Table.Th><Table.Th>Module</Table.Th><Table.Th>Deleted by</Table.Th><Table.Th>Deleted on</Table.Th><Table.Th /></Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data?.results.map((it) => (
                <Table.Tr key={`${it.module}-${it.id}`}>
                  <Table.Td>
                    <Text size="sm" fw={500}>{it.title}</Text>
                    {it.subtitle && <Text size="xs" c="dimmed">{it.subtitle}</Text>}
                  </Table.Td>
                  <Table.Td><Text size="sm">{it.module_label}</Text></Table.Td>
                  <Table.Td><Text size="sm">{it.archived_by ?? "—"}</Text></Table.Td>
                  <Table.Td><Text size="sm">{formatDateTime(it.archived_at, me)}</Text></Table.Td>
                  <Table.Td>
                    <Group gap="xs" justify="flex-end">
                      <Button size="xs" variant="light" onClick={() => restore(it)}>Restore</Button>
                      {me.role.is_admin && <Button size="xs" variant="subtle" color="red" onClick={() => purge(it)}>Delete permanently</Button>}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
              {data && data.results.length === 0 && (
                <Table.Tr><Table.Td colSpan={5}><Text c="dimmed" size="sm" ta="center" py="xl">The archive is empty.</Text></Table.Td></Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        )}
      </Paper>
    </Stack>
  );
}
