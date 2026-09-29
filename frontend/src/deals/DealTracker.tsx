// A deal's investor tracker: every investor approached for this deal, how far each has got, and
// the funnel those numbers make. Stage moves use the same rules as any pipeline.

import {
  ActionIcon, Anchor, Badge, Box, Button, Group, Loader, Menu, Paper, ScrollArea, Select, SimpleGrid, Stack, Table,
  Text, Tooltip,
} from "@mantine/core";
import { modals } from "@mantine/modals";
import {
  IconArchive, IconBulb, IconDots, IconExternalLink, IconPlayerPause, IconPlayerPlay, IconX,
} from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, qs } from "../api";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import { useMe, useModuleSchema, useSchema, useUsers } from "../context";
import { formatDate } from "../format";
import { textValue } from "../module/fields";
import LinkInput from "../module/LinkInput";
import { exitOf, resumeVerb, stageLabel, stageOf, usePipelineMove } from "../module/pipeline";
import type { LinkValue, Page, RecordData, Section } from "../types";
import Suggestions from "./Suggestions";

export default function DealTracker({ section, deal }: { section: Section; deal: RecordData }) {
  const me = useMe();
  const qc = useQueryClient();
  const { data: schema } = useSchema();
  const { data: users } = useUsers();
  const { module: tracker } = useModuleSchema(section.module);
  const [suggesting, setSuggesting] = useState(false);
  const [adding, setAdding] = useState(false);
  const dealLink = `deals:${deal.id}`;
  const { data, isLoading, error } = useQuery({
    queryKey: ["records", "deal_investors", "tracker", deal.id],
    queryFn: () => api<Page<RecordData>>(`m/deal_investors/${qs({ deal: dealLink, ordering: "status,-stage,investor", page_size: 500 })}`),
  });
  const { move, modal } = usePipelineMove(tracker ?? ({ key: "deal_investors" } as never));
  if (!tracker?.pipeline) return null;
  const p = tracker.pipeline;
  const rows = data?.results ?? [];
  const investorField = tracker.fields.find((f) => f.key === "investor")!;
  const reasonField = tracker.fields.find((f) => f.key === "decline_reason");
  const ctx = { schema, users, prefs: me };
  const canSuggest = tracker.perms.create && !!me.perms.investors?.view;

  // How many reached each stage: an investor who declined at "Meeting held" had been sent the teaser too.
  const reached = p.stages.map((s, i) => ({
    ...s,
    count: rows.filter((r) => p.stages.findIndex((x) => x.value === stageOf(r, p).stage) >= i).length,
  }));
  const counts = {
    active: rows.filter((r) => stageOf(r, p).isOpen).length,
    ...Object.fromEntries(p.exits.map((e) => [e.value, rows.filter((r) => stageOf(r, p).status === e.value).length])),
  } as Record<string, number>;

  async function add(inv: LinkValue | null) {
    if (!inv) return;
    setAdding(true);
    try {
      await api("m/deal_investors/", { body: { deal: dealLink, investor: `${inv.module}:${inv.id}` } });
      notifyOk(`${inv.title} added to the tracker.`);
      await qc.invalidateQueries({ queryKey: ["records", "deal_investors"] });
      await qc.invalidateQueries({ queryKey: ["matches", deal.id] });
    } catch (e) {
      notifyError(e, `Couldn't add ${inv.title}`);
    } finally {
      setAdding(false);
    }
  }

  function remove(r: RecordData) {
    modals.openConfirmModal({
      title: `Remove ${r._title} from this deal?`,
      children: <Text size="sm">Its tracker entry and notes move to the Archive, where they can be restored.</Text>,
      labels: { confirm: "Remove", cancel: "Cancel" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await api(`m/deal_investors/${r.id}/`, { method: "DELETE" });
          notifyOk(`${r._title} removed from the tracker.`);
          await qc.invalidateQueries({ queryKey: ["records", "deal_investors"] });
          await qc.invalidateQueries({ queryKey: ["matches", deal.id] });
        } catch (e) {
          notifyError(e, "Couldn't remove it");
        }
      },
    });
  }

  return (
    <Paper withBorder radius="md">
      <Group justify="space-between" px="lg" pt="md" pb="sm" wrap="wrap" gap="xs">
        <Group gap="xs">
          <Text fw={600}>{section.label}</Text>
          {data && <Badge variant="light" color="gray" size="sm">{rows.length}</Badge>}
        </Group>
        {canSuggest && (
          <Button size="xs" variant="light" leftSection={<IconBulb size={14} />} onClick={() => setSuggesting(true)}>
            Suggested investors
          </Button>
        )}
      </Group>

      {isLoading ? (
        <Group px="lg" pb="lg"><Loader size="sm" /></Group>
      ) : error ? (
        <Text c="red" size="sm" px="lg" pb="lg">{errorMessage(error)}</Text>
      ) : (
        <Stack gap="md" pb="md">
          {rows.length > 0 && (
            <Box px="lg">
              <SimpleGrid cols={{ base: 2, sm: 4, md: 7 }} spacing={6} verticalSpacing={6}>
                {reached.map((s) => (
                  <Tooltip key={s.value} label={`${s.count} of ${rows.length} reached ${s.label}`}>
                    <Paper radius="sm" p={8} bg="gray.0" withBorder>
                      <Text fw={700} size="lg" lh={1.1}>{s.count}</Text>
                      <Text size="xs" c="dimmed" lh={1.25} lineClamp={2} mih={30}>{s.label}</Text>
                      <Box mt={6} h={4} bg="gray.2" style={{ borderRadius: 2 }}>
                        <Box h={4} bg="navy.6"
                          style={{ width: `${rows.length ? (s.count / rows.length) * 100 : 0}%`, borderRadius: 2 }} />
                      </Box>
                    </Paper>
                  </Tooltip>
                ))}
              </SimpleGrid>
              <Group gap="xs" mt="xs">
                <Badge variant="dot" color="navy">{counts.active} active</Badge>
                {p.exits.map((e) => (
                  <Badge key={e.value} variant="dot" color={e.tone}>{counts[e.value]} {e.label.toLowerCase()}</Badge>
                ))}
              </Group>
            </Box>
          )}

          {rows.length === 0 ? (
            <Text size="sm" c="dimmed" px="lg">
              No investors on this deal yet.{" "}
              {tracker.perms.create && (canSuggest
                ? "Add them one by one, or see suggestions based on the deal's sector, stage, size and geography."
                : "Add them one by one below.")}
            </Text>
          ) : (
            <ScrollArea>
              <Table className="df-table" verticalSpacing="xs" horizontalSpacing="lg" miw={720}>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Investor</Table.Th>
                    <Table.Th w={230}>Stage</Table.Th>
                    <Table.Th>Since</Table.Th>
                    <Table.Th miw={150}>Status</Table.Th>
                    <Table.Th w={44} />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {rows.map((r) => {
                    const { stage, status, isOpen } = stageOf(r, p);
                    const exit = exitOf(p, status);
                    const inv = r.investor as LinkValue;
                    const reason = reasonField && r[reasonField.key] ? textValue(reasonField, r[reasonField.key], ctx) : "";
                    return (
                      <Table.Tr key={r.id}>
                        <Table.Td>
                          <Group gap={6} wrap="nowrap">
                            {me.perms.investors?.view
                              ? <Anchor component={Link} to={`/m/investors/${inv.id}`} size="sm" fw={600}>{inv.title}</Anchor>
                              : <Text size="sm" fw={600}>{inv.title}</Text>}
                            {inv.archived && <Badge size="xs" variant="light" color="gray">Archived</Badge>}
                          </Group>
                        </Table.Td>
                        <Table.Td>
                          <Select size="xs" data={p.stages} value={stage} allowDeselect={false} disabled={!tracker.perms.edit}
                            aria-label={`Stage for ${inv.title}`} comboboxProps={{ withinPortal: true }}
                            onChange={(v) => v && v !== stage && move(r, { kind: "stage", stage: v })}
                            styles={{ input: { color: isOpen ? undefined : "var(--mantine-color-dimmed)" } }} />
                        </Table.Td>
                        <Table.Td><Text size="sm" c="dimmed">{formatDate(r[p.entered_field] as string | null, me)}</Text></Table.Td>
                        <Table.Td>
                          {isOpen ? (
                            <Badge variant="light" tt="none" color={stage === p.won ? "teal" : "navy"}>
                              {stage === p.won ? stageLabel(p, stage) : "Active"}
                            </Badge>
                          ) : (
                            <Stack gap={2} align="flex-start">
                              <Badge variant="light" tt="none" color={exit?.tone ?? "red"} style={{ flexShrink: 0 }}>{exit?.label ?? status}</Badge>
                              {reason && <Text size="xs" c="dimmed">{reason}</Text>}
                            </Stack>
                          )}
                        </Table.Td>
                        <Table.Td>
                          <Menu position="bottom-end" withinPortal>
                            <Menu.Target>
                              <ActionIcon variant="subtle" color="gray" aria-label={`More for ${inv.title}`}><IconDots size={16} /></ActionIcon>
                            </Menu.Target>
                            <Menu.Dropdown>
                              {tracker.perms.edit && !isOpen && (
                                <Menu.Item leftSection={<IconPlayerPlay size={14} />} onClick={() => move(r, { kind: "resume" })}>
                                  {resumeVerb(p, status)} at {stageLabel(p, stage)}
                                </Menu.Item>
                              )}
                              {tracker.perms.edit && stage !== p.won && p.exits.filter((e) => e.value !== status).map((e) => (
                                <Menu.Item key={e.value} color={e.tone === "red" ? "red" : undefined}
                                  leftSection={e.paused ? <IconPlayerPause size={14} /> : <IconX size={14} />}
                                  onClick={() => move(r, { kind: "exit", exit: e.value })}>
                                  {e.action}
                                </Menu.Item>
                              ))}
                              <Menu.Item component={Link} to={`/m/deal_investors/${r.id}`} leftSection={<IconExternalLink size={14} />}>
                                Notes and history
                              </Menu.Item>
                              {tracker.perms.delete && (
                                <Menu.Item color="red" leftSection={<IconArchive size={14} />} onClick={() => remove(r)}>
                                  Remove from deal
                                </Menu.Item>
                              )}
                            </Menu.Dropdown>
                          </Menu>
                        </Table.Td>
                      </Table.Tr>
                    );
                  })}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          )}

          {tracker.perms.create && (
            <Box px="lg" maw={460}>
              <LinkInput key={rows.length} f={{ ...investorField, label: "Add an investor", required: false, help: "" }}
                value={null} onChange={add} schemaModules={schema?.modules ?? []} />
              {adding && <Loader size="xs" mt={4} />}
            </Box>
          )}
        </Stack>
      )}
      {modal}
      {canSuggest && (
        <Suggestions opened={suggesting} onClose={() => setSuggesting(false)} deal={deal} onAdd={add} />
      )}
    </Paper>
  );
}
