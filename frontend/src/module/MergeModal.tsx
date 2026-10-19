// Merge duplicates: put the records side by side, pick the one to keep and, where they differ,
// which value wins. Everything linked to the others moves to the one kept; the others go to
// Deleted items.

import { Alert, Button, Group, Loader, Modal, Radio, ScrollArea, Stack, Table, Text } from "@mantine/core";
import { IconArrowsJoin } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { errorMessage, notifyOk } from "../components/errors";
import type { FieldSchema, ModuleSchema, Page, RecordData } from "../types";
import { FieldValue, isEmpty } from "./fields";

const COMBINE = -1; // multi-value fields: keep every value from every record

function same(a: unknown, b: unknown) {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

function itemKey(v: unknown) {
  return v && typeof v === "object" && "module" in (v as object) ? `${(v as { module: string }).module}:${(v as { id: number }).id}` : JSON.stringify(v);
}

export default function MergeModal({
  module, ids, onClose, onMerged,
}: { module: ModuleSchema; ids: number[]; onClose: () => void; onMerged: () => void }) {
  const { data, isLoading, error: loadError } = useQuery({
    queryKey: ["merge", module.key, ids.join(",")],
    queryFn: () => api<Page<RecordData>>(`m/${module.key}/?ids=${ids.join(",")}&page_size=20`),
  });
  const records = useMemo(() => {
    const byId = new Map((data?.results ?? []).map((r) => [r.id, r]));
    return ids.map((id) => byId.get(id)).filter((r): r is RecordData => !!r);
  }, [data, ids]);
  const [keep, setKeep] = useState<number | null>(null);
  const [picks, setPicks] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Fields worth choosing: shown, typed in by people, and different between the records.
  const fields = useMemo(() => module.fields.filter((f) => f.enabled && !f.readonly && !f.pipeline_role && !f.via), [module]);
  const differing = useMemo(
    () => fields.filter((f) => records.some((r) => !same(r[f.key], records[0]?.[f.key]))),
    [fields, records],
  );
  const multi = (f: FieldSchema) => f.type === "tags" || f.multi;

  // Start with the record that has the most filled in, and each field from it unless it's empty there.
  useEffect(() => {
    if (!records.length || keep !== null) return;
    const filled = (r: RecordData) => fields.filter((f) => !isEmpty(r[f.key])).length;
    const best = [...records].sort((a, b) => filled(b) - filled(a))[0];
    setKeep(best.id);
  }, [records, fields, keep]);
  useEffect(() => {
    if (keep === null) return;
    const kept = records.find((r) => r.id === keep)!;
    const next: Record<string, number> = {};
    for (const f of differing) {
      if (multi(f)) next[f.key] = COMBINE;
      else next[f.key] = !isEmpty(kept[f.key]) ? keep : (records.find((r) => !isEmpty(r[f.key]))?.id ?? keep);
    }
    setPicks(next);
  }, [keep, differing, records]); // eslint-disable-line react-hooks/exhaustive-deps

  function valueFor(f: FieldSchema): unknown {
    const pick = picks[f.key];
    if (pick === COMBINE) {
      const seen = new Set<string>();
      const out: unknown[] = [];
      for (const r of [records.find((x) => x.id === keep)!, ...records.filter((x) => x.id !== keep)]) {
        for (const v of (r[f.key] as unknown[]) ?? []) {
          const k = itemKey(v);
          if (!seen.has(k)) { seen.add(k); out.push(v); }
        }
      }
      return out;
    }
    return records.find((r) => r.id === pick)?.[f.key];
  }

  async function merge() {
    if (keep === null) return;
    setBusy(true);
    setError("");
    const kept = records.find((r) => r.id === keep)!;
    const values: Record<string, unknown> = {};
    for (const f of differing) {
      const v = valueFor(f);
      if (!same(v, kept[f.key])) values[f.key] = v;
    }
    try {
      const r = await api<{ merged: number; record: RecordData }>(`m/${module.key}/merge/`, {
        body: { keep, merge: records.filter((x) => x.id !== keep).map((x) => x.id), values },
      });
      notifyOk(`Merged ${r.merged + 1} into ${r.record._title}. The others are in Deleted items.`);
      onMerged();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const noun = module.label.toLowerCase();
  return (
    <Modal opened onClose={onClose} size="90%" title={<Group gap={8}><IconArrowsJoin size={18} /><Text fw={600}>Merge {records.length || ids.length} {noun}</Text></Group>}>
      {isLoading ? <Group p="xl" justify="center"><Loader /></Group> : loadError ? <Alert color="red">{errorMessage(loadError)}</Alert> : (
        <Stack>
          <Text size="sm">
            Pick the record to <b>keep</b>, then, where they differ, which value to keep. Everything linked to the others
            (contacts, outreach, deals, tasks, tracker entries, documents and notes) moves to the one you keep.
            The others go to Deleted items.
          </Text>
          {error && <Alert color="red">{error}</Alert>}
          <ScrollArea type="auto">
            <Table className="df-table df-merge" verticalSpacing={8} miw={260 + records.length * 220}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th w={200}>Keep which record?</Table.Th>
                  {records.map((r) => (
                    <Table.Th key={r.id} data-keep={r.id === keep ? "" : undefined}>
                      <Radio checked={r.id === keep} onChange={() => setKeep(r.id)} label={<Text fw={600} size="sm">{r._title}</Text>}
                        aria-label={`Keep ${r._title}`} />
                    </Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {differing.length === 0 && (
                  <Table.Tr><Table.Td colSpan={records.length + 1}><Text size="sm" c="dimmed">These records hold the same details. Merging just combines what's linked to them.</Text></Table.Td></Table.Tr>
                )}
                {differing.map((f) => (
                  <Table.Tr key={f.key}>
                    <Table.Td>
                      <Text size="sm" fw={500}>{f.label}</Text>
                      {multi(f) && (
                        <Radio mt={4} size="xs" checked={picks[f.key] === COMBINE} onChange={() => setPicks((p) => ({ ...p, [f.key]: COMBINE }))}
                          label="Combine all" />
                      )}
                    </Table.Td>
                    {records.map((r) => (
                      <Table.Td key={r.id} data-keep={r.id === keep ? "" : undefined} data-picked={picks[f.key] === r.id ? "" : undefined}
                        onClick={() => setPicks((p) => ({ ...p, [f.key]: r.id }))} style={{ cursor: "pointer" }}>
                        <Group gap={8} wrap="nowrap" align="flex-start">
                          <Radio size="xs" mt={2} checked={picks[f.key] === r.id} onChange={() => setPicks((p) => ({ ...p, [f.key]: r.id }))}
                            aria-label={`Use ${f.label} from ${r._title}`} />
                          <div style={{ minWidth: 0 }}>
                            {isEmpty(r[f.key]) ? <Text size="sm" c="dimmed">Empty</Text> : <FieldValue f={f} v={r[f.key]} />}
                          </div>
                        </Group>
                      </Table.Td>
                    ))}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
          <Text size="xs" c="dimmed">Only details that differ are shown. Details that are the same stay as they are.</Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Cancel</Button>
            <Button onClick={merge} loading={busy} disabled={keep === null || records.length < 2} leftSection={<IconArrowsJoin size={16} />}>
              Merge into {records.find((r) => r.id === keep)?._title ?? "…"}
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
