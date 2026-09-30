// Documents about a record (a company, outreach attempt, deal or investor), on that record's page.
// Behind the Vault PIN: when the Vault is locked the panel offers to open it right here.

import { ActionIcon, Anchor, Badge, Button, Group, Loader, Paper, ScrollArea, Table, Text, Tooltip } from "@mantine/core";
import { IconCloudUpload, IconDownload, IconLock } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, ApiError, qs } from "../api";
import { errorMessage } from "../components/errors";
import { useMe, useModuleSchema, useSchema } from "../context";
import { formatBytes, formatDate } from "../format";
import type { ModuleSchema, Page, RecordData, Section } from "../types";
import { downloadUrl, isVaultLock, useVaultStatus } from "./api";
import { UnlockForm } from "./VaultGate";

export default function DocumentsPanel({ section, parent, record }: { section: Section; parent: ModuleSchema; record: RecordData }) {
  const me = useMe();
  const { module: vault } = useModuleSchema("vault");
  const { data: schema } = useSchema();
  const { data: status } = useVaultStatus();
  const about = `${parent.key}:${record.id}`;
  const { data, isLoading, error } = useQuery({
    queryKey: ["records", "vault", "about", about, status?.open],
    queryFn: () => api<Page<RecordData>>(`m/vault/${qs({ about, ordering: "-created_at", page_size: 100 })}`),
    enabled: !!status?.open,
    retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2,
  });
  if (!vault) return null;
  const locked = status && (!status.open || isVaultLock(error));
  const rows = data?.results ?? [];
  const category = (id: unknown) => schema?.choice_lists.document_categories?.options.find((o) => o.id === id)?.label ?? "";
  const back = `/m/${parent.key}/${record.id}`;

  return (
    <Paper withBorder radius="md">
      <Group justify="space-between" px="lg" pt="md" pb="sm">
        <Group gap="xs">
          <Text fw={600}>{section.label}</Text>
          {data && !locked && <Badge variant="light" color="gray" size="sm">{rows.length}</Badge>}
          {locked && <IconLock size={16} color="var(--mantine-color-gray-6)" />}
        </Group>
        {vault.perms.create && !locked && (
          <Button size="xs" variant="light" leftSection={<IconCloudUpload size={14} />} component={Link}
            to={`/m/vault/new${qs({ tagged: about, return: back })}`}>Upload</Button>
        )}
      </Group>
      {!status ? (
        <Group px="lg" pb="lg"><Loader size="sm" /></Group>
      ) : !status.has_pin ? (
        <Text size="sm" c="dimmed" px="lg" pb="lg">
          Documents are in the Vault, behind a PIN. <Anchor component={Link} to="/m/vault" size="sm">Set your Vault PIN</Anchor> to see them.
        </Text>
      ) : status.locked_out_until ? (
        <Text size="sm" c="dimmed" px="lg" pb="lg">The Vault is locked after too many wrong PINs. Try again later.</Text>
      ) : locked ? (
        <div style={{ padding: "0 var(--mantine-spacing-lg) var(--mantine-spacing-lg)" }}>
          <Text size="sm" c="dimmed" mb="xs">The Vault is locked. Enter your PIN to see this record's documents.</Text>
          <UnlockForm compact />
        </div>
      ) : isLoading ? (
        <Group px="lg" pb="lg"><Loader size="sm" /></Group>
      ) : error ? (
        <Text c="red" size="sm" px="lg" pb="lg">{errorMessage(error)}</Text>
      ) : rows.length === 0 ? (
        <Text size="sm" c="dimmed" px="lg" pb="lg">No documents yet.</Text>
      ) : (
        <ScrollArea>
          <Table className="df-table" verticalSpacing="xs" horizontalSpacing="lg" miw={600}>
            <Table.Thead>
              <Table.Tr><Table.Th>Title</Table.Th><Table.Th>Category</Table.Th><Table.Th>File</Table.Th><Table.Th>Uploaded</Table.Th><Table.Th w={44} /></Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((d) => (
                <Table.Tr key={d.id}>
                  <Table.Td><Anchor component={Link} to={`/m/vault/${d.id}`} size="sm" fw={600}>{d._title}</Anchor></Table.Td>
                  <Table.Td><Text size="sm">{category(d.category)}</Text></Table.Td>
                  <Table.Td><Text size="sm" c="dimmed">{String(d.file_type)} · {formatBytes(Number(d.file_size))}</Text></Table.Td>
                  <Table.Td><Text size="sm" c="dimmed">{formatDate(d.created_at as string, me)}</Text></Table.Td>
                  <Table.Td>
                    <Tooltip label={`Download ${d.file_name}`}>
                      <ActionIcon variant="subtle" color="gray" component="a" href={downloadUrl(d.id)} aria-label={`Download ${d._title}`}>
                        <IconDownload size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      )}
    </Paper>
  );
}
