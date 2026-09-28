// Records from another module linked to this one, e.g. an investor's contacts.

import { Badge, Button, Group, Loader, Paper, ScrollArea, Table, Text } from "@mantine/core";
import { IconPlus } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { api, qs } from "../api";
import { useSchema } from "../context";
import { errorMessage } from "../components/errors";
import type { ModuleSchema, Page, RecordData, Section } from "../types";
import { FieldValue } from "./fields";

export default function RelatedSection({ section, parent, record }: { section: Section; parent: ModuleSchema; record: RecordData }) {
  const nav = useNavigate();
  const { data: schema } = useSchema();
  const rel = schema?.modules.find((m) => m.key === section.module);
  const linkParam = `${parent.key}:${record.id}`;
  const { data, isLoading, error } = useQuery({
    queryKey: ["records", section.module, "related", section.link_field, linkParam, section.order],
    queryFn: () => api<Page<RecordData>>(`m/${section.module}/${qs({ [section.link_field!]: linkParam, ordering: section.order, page_size: 200 })}`),
    enabled: !!rel,
  });
  if (!rel) return null;

  const columns = (section.columns ?? []).map((k) => rel.fields.find((f) => f.key === k && f.enabled)).filter((f) => !!f);
  const count = data?.count ?? 0;
  const back = `/m/${parent.key}/${record.id}`;
  const addUrl = `/m/${rel.key}/new${qs({ [section.link_field!]: linkParam, return: back })}`;

  return (
    <Paper withBorder radius="md">
      <Group justify="space-between" px="lg" pt="md" pb="sm">
        <Group gap="xs">
          <Text fw={600}>{section.label}</Text>
          {data && <Badge variant="light" color="gray" size="sm">{count}</Badge>}
        </Group>
        {rel.perms.create && (
          <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} component={Link} to={addUrl}>
            Add {rel.singular.toLowerCase()}
          </Button>
        )}
      </Group>
      {isLoading ? (
        <Group px="lg" pb="lg"><Loader size="sm" /></Group>
      ) : error ? (
        <Text c="red" size="sm" px="lg" pb="lg">{errorMessage(error)}</Text>
      ) : count === 0 ? (
        <Text size="sm" c="dimmed" px="lg" pb="lg">No {rel.label.toLowerCase()} linked yet.</Text>
      ) : (
        <ScrollArea>
          <Table className="df-table" highlightOnHover verticalSpacing="xs" horizontalSpacing="lg" miw={640}>
            <Table.Thead>
              <Table.Tr>{columns.map((f) => <Table.Th key={f!.key}>{f!.type === "boolean" ? "" : f!.label}</Table.Th>)}</Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data!.results.map((r) => (
                <Table.Tr key={r.id} className="df-row-link" onClick={() => nav(`/m/${rel.key}/${r.id}`)}>
                  {columns.map((f) => (
                    <Table.Td key={f!.key}>
                      {f!.key === rel.title_field ? (
                        <Text size="sm" fw={600} c="navy.9">{String(r[f!.key])}</Text>
                      ) : f!.type === "boolean" ? (
                        r[f!.key] ? <Badge size="sm" variant="light" color="teal">{f!.label.replace(/^Primary point of contact$/, "Primary")}</Badge> : null
                      ) : (
                        <FieldValue f={f!} v={r[f!.key]} compact />
                      )}
                    </Table.Td>
                  ))}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      )}
    </Paper>
  );
}
