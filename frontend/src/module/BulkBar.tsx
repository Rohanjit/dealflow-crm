// What you can do with the records ticked in a list: edit a field on all of them, move them to a
// stage, add investors to a deal's tracker, export them, merge duplicates, or delete them.

import {
  Alert, Button, Group, List, Modal, SegmentedControl, Select, Stack, Text,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { modals } from "@mantine/modals";
import {
  IconArrowsJoin, IconDownload, IconMailFast, IconPencil, IconPlaylistAdd, IconProgress, IconTrash, IconX,
} from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api, download, qs } from "../api";
import { useMe } from "../context";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import type { FieldSchema, LookupItem, ModuleSchema } from "../types";
import { emptyValue, FieldInput } from "./fields";
import MergeModal from "./MergeModal";
import { useNavigate } from "react-router-dom";
import { newCampaign } from "../pages/Campaigns";

type BulkResult = { updated: number; failed: { id: number; title: string; errors: Record<string, string | string[]> }[] };

/** Fields that can be set on many records at once from the "Edit a field" dialog. */
export function bulkFields(module: ModuleSchema): FieldSchema[] {
  return module.fields.filter((f) => f.enabled && !f.readonly && !f.pipeline_role && !f.via && f.key !== module.title_field);
}

export default function BulkBar({
  module, ids, onClear, exportColumns,
}: { module: ModuleSchema; ids: number[]; onClear: () => void; exportColumns: string }) {
  const me = useMe();
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<"edit" | "stage" | "deal" | "merge" | null>(null);
  const n = ids.length;
  const noun = n === 1 ? module.singular.toLowerCase() : module.label.toLowerCase();
  const canMerge = module.mergeable && module.perms.edit && module.perms.delete;
  const canTrack = module.key === "investors" && !!me.perms.deal_investors?.create;
  const canCampaign = me.flags.send_campaigns && (module.key === "contacts" || module.key === "investors") && !!me.perms.contacts?.view;
  const nav = useNavigate();

  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: ["records"] });
    await qc.invalidateQueries({ queryKey: ["record"] });
    await qc.invalidateQueries({ queryKey: ["dashboard"] });
  };

  function remove() {
    modals.openConfirmModal({
      title: `Delete ${n} ${noun}?`,
      children: <Text size="sm">They move to Deleted items and disappear from lists and search. They can be brought back from there.</Text>,
      labels: { confirm: `Delete ${n}`, cancel: "Cancel" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          const r = await api<{ deleted: number }>(`m/${module.key}/bulk/`, { body: { ids, action: "delete" } });
          notifyOk(`${r.deleted} ${r.deleted === 1 ? module.singular.toLowerCase() : module.label.toLowerCase()} deleted. You can bring them back from Deleted items.`);
          onClear();
          await refresh();
        } catch (e) {
          notifyError(e, "Couldn't delete");
        }
      },
    });
  }

  return (
    <>
      <div className="df-bulkbar" role="toolbar" aria-label="Selected records">
        <Text size="sm" fw={600} style={{ whiteSpace: "nowrap" }}>{n} selected</Text>
        <div className="df-bulkbar-actions">
          {module.perms.edit && bulkFields(module).length > 0 && (
            <Button size="xs" variant="default" leftSection={<IconPencil size={14} />} onClick={() => setDialog("edit")}>Edit a field</Button>
          )}
          {module.perms.edit && module.pipeline && (
            <Button size="xs" variant="default" leftSection={<IconProgress size={14} />} onClick={() => setDialog("stage")}>Change stage</Button>
          )}
          {canTrack && (
            <Button size="xs" variant="default" leftSection={<IconPlaylistAdd size={14} />} onClick={() => setDialog("deal")}>Add to a deal's tracker</Button>
          )}
          {canCampaign && (
            <Button size="xs" variant="default" leftSection={<IconMailFast size={14} />}
              onClick={() => newCampaign(nav, module.key === "contacts" ? { contacts: ids } : { investors: ids, who: "primary" },
                `Email to ${n} ${noun}`)}>
              Email selected
            </Button>
          )}
          <Button size="xs" variant="default" leftSection={<IconDownload size={14} />}
            onClick={() => download(`m/${module.key}/export/?${new URLSearchParams({ ids: ids.join(","), columns: exportColumns })}`)}>
            Export
          </Button>
          {canMerge && (
            <Button size="xs" variant="default" leftSection={<IconArrowsJoin size={14} />} disabled={n < 2 || n > 10}
              title={n < 2 ? "Select two or more records that are the same" : n > 10 ? "Merge up to 10 at a time" : undefined}
              onClick={() => setDialog("merge")}>
              Merge
            </Button>
          )}
          {module.perms.delete && (
            <Button size="xs" variant="default" color="red" leftSection={<IconTrash size={14} />} onClick={remove}>Delete</Button>
          )}
        </div>
        <Button size="xs" variant="subtle" color="gray" leftSection={<IconX size={14} />} onClick={onClear}>Clear selection</Button>
      </div>
      {dialog === "edit" && <EditFieldModal module={module} ids={ids} onClose={() => setDialog(null)} onDone={refresh} />}
      {dialog === "stage" && <StageModal module={module} ids={ids} onClose={() => setDialog(null)} onDone={refresh} />}
      {dialog === "deal" && <AddToDealModal ids={ids} onClose={() => setDialog(null)} />}
      {dialog === "merge" && (
        <MergeModal module={module} ids={ids} onClose={() => setDialog(null)} onMerged={() => { setDialog(null); onClear(); refresh(); }} />
      )}
    </>
  );
}

function Failures({ result, module }: { result: BulkResult; module: ModuleSchema }) {
  if (!result.failed.length) return null;
  const label = (k: string) => (k === "detail" ? "" : `${module.fields.find((f) => f.key === k)?.label ?? k}: `);
  return (
    <Alert color="orange" title={`${result.failed.length} couldn't be changed`}>
      <List size="sm" spacing={4}>
        {result.failed.map((f) => (
          <List.Item key={f.id}>
            <b>{f.title}</b>: {Object.entries(f.errors).map(([k, v]) => `${label(k)}${Array.isArray(v) ? v.join(" ") : v}`).join(" ")}
          </List.Item>
        ))}
      </List>
    </Alert>
  );
}

async function runEdit(module: ModuleSchema, ids: number[], values: Record<string, unknown>, modes: Record<string, string>) {
  return api<BulkResult>(`m/${module.key}/bulk/`, { body: { ids, action: "edit", values, modes } });
}

function EditFieldModal({ module, ids, onClose, onDone }: { module: ModuleSchema; ids: number[]; onClose: () => void; onDone: () => Promise<void> }) {
  const fields = bulkFields(module);
  const [key, setKey] = useState<string | null>(null);
  const f = fields.find((x) => x.key === key);
  const [value, setValue] = useState<unknown>(null);
  const [mode, setMode] = useState<"add" | "remove" | "replace">("add");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<BulkResult | null>(null);
  const multi = !!f && (f.type === "tags" || f.multi);

  const groups = module.groups
    .map((g) => ({ group: g, items: fields.filter((x) => x.group === g).map((x) => ({ value: x.key, label: x.label })) }))
    .filter((g) => g.items.length);

  async function apply() {
    if (!f) return;
    setBusy(true);
    setError("");
    try {
      const r = await runEdit(module, ids, { [f.key]: value }, multi ? { [f.key]: mode } : {});
      setResult(r);
      await onDone();
      if (r.updated) notifyOk(`${f.label} changed on ${r.updated} ${r.updated === 1 ? module.singular.toLowerCase() : module.label.toLowerCase()}.`);
      if (!r.failed.length) onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal opened onClose={onClose} title={`Edit a field on ${ids.length} ${ids.length === 1 ? module.singular.toLowerCase() : module.label.toLowerCase()}`} size="lg">
      <Stack>
        {error && <Alert color="red">{error}</Alert>}
        {result && <Failures result={result} module={module} />}
        <Select label="Which field?" placeholder="Pick a field" searchable data={groups} value={key}
          onChange={(k) => { setKey(k); const nf = fields.find((x) => x.key === k); setValue(nf ? emptyValue(nf) : null); setResult(null); }} />
        {f && multi && (
          <div>
            <Text size="sm" fw={500} mb={4}>What to do</Text>
            <SegmentedControl fullWidth value={mode} onChange={(m) => setMode(m as typeof mode)} data={[
              { value: "add", label: "Add these" }, { value: "remove", label: "Remove these" }, { value: "replace", label: "Replace with these" },
            ]} />
          </div>
        )}
        {f && <FieldInput f={{ ...f, required: false }} value={value} onChange={setValue} />}
        {f && (
          <Text size="xs" c="dimmed">
            {multi
              ? mode === "add" ? "Each record keeps what it has and gets these too." : mode === "remove" ? "These are taken off each record; anything else stays." : "Each record's values are replaced by exactly these."
              : "Every selected record gets this value. Leave it empty to clear the field."}
          </Text>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button onClick={apply} loading={busy} disabled={!f}>Apply to {ids.length}</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function StageModal({ module, ids, onClose, onDone }: { module: ModuleSchema; ids: number[]; onClose: () => void; onDone: () => Promise<void> }) {
  const p = module.pipeline!;
  const [target, setTarget] = useState<string | null>(null);
  const [extra, setExtra] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<BulkResult | null>(null);
  const exit = target?.startsWith("exit:") ? p.exits.find((e) => e.value === target.slice(5)) : undefined;
  const asks = exit ? [exit.reason_field, exit.date_field].filter(Boolean).map((k) => module.fields.find((f) => f.key === k)!).filter(Boolean) : [];

  async function apply() {
    if (!target) return;
    setBusy(true);
    setError("");
    try {
      const values = exit ? { [p.status_field]: exit.value, ...extra } : { [p.stage_field]: target.slice(6) };
      const r = await runEdit(module, ids, values, {});
      setResult(r);
      await onDone();
      if (r.updated) notifyOk(`${r.updated} ${r.updated === 1 ? module.singular.toLowerCase() : module.label.toLowerCase()} moved.`);
      if (!r.failed.length) onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal opened onClose={onClose} title={`Change the stage of ${ids.length} ${ids.length === 1 ? module.singular.toLowerCase() : module.label.toLowerCase()}`}>
      <Stack>
        {error && <Alert color="red">{error}</Alert>}
        {result && <Failures result={result} module={module} />}
        <Select label="Move them to" placeholder="Pick a stage" value={target} onChange={(v) => { setTarget(v); setExtra({}); setResult(null); }}
          data={[
            { group: "Stages", items: p.stages.map((s) => ({ value: `stage:${s.value}`, label: s.label })) },
            { group: "Pause or end", items: p.exits.map((e) => ({ value: `exit:${e.value}`, label: e.label })) },
          ]} />
        {asks.map((f) => (
          <FieldInput key={f.key} f={{ ...f, required: true }} value={extra[f.key] ?? emptyValue(f)}
            onChange={(v) => setExtra((x) => ({ ...x, [f.key]: v }))} />
        ))}
        <Text size="xs" c="dimmed">Each record moves as if you'd moved it on its own page, and its log notes the change.</Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button onClick={apply} loading={busy} disabled={!target}>Move {ids.length}</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function AddToDealModal({ ids, onClose }: { ids: number[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [debounced] = useDebouncedValue(search.trim(), 200);
  const [deal, setDeal] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { data } = useQuery({
    queryKey: ["lookup", "deals", debounced],
    queryFn: () => api<LookupItem[]>(`lookup/deals/${qs({ q: debounced })}`),
    staleTime: 30_000,
  });
  const options = useMemo(() => (data ?? []).map((d) => ({ value: String(d.id), label: d.subtitle ? `${d.title} · ${d.subtitle}` : d.title })), [data]);

  async function add() {
    if (!deal) return;
    setBusy(true);
    setError("");
    try {
      const r = await api<{ added: number; skipped: string[]; deal: { title: string } }>(`deals/${deal}/tracker/add/`, { body: { investor_ids: ids } });
      await qc.invalidateQueries({ queryKey: ["records"] });
      notifyOk(`${r.added} added to ${r.deal.title}'s tracker${r.skipped.length ? `. Already there: ${r.skipped.join(", ")}` : ""}.`);
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal opened onClose={onClose} title={`Add ${ids.length} ${ids.length === 1 ? "investor" : "investors"} to a deal's tracker`}>
      <Stack>
        {error && <Alert color="red">{error}</Alert>}
        <Select label="Which deal?" placeholder="Search deals" searchable data={options} value={deal} onChange={setDeal}
          searchValue={search} onSearchChange={setSearch} filter={({ options: o }) => o} nothingFoundMessage="No deals match" />
        <Text size="xs" c="dimmed">They're added at the first stage (Identified). Investors already on that deal's tracker are skipped.</Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button onClick={add} loading={busy} disabled={!deal}>Add to tracker</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
