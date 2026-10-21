// Settings > Export (decision 61): build a report from any module you can see (records through the
// list's own filters, the columns you pick, a sort order) and download it as Excel, CSV or PDF (a
// table, or one page per record). Reports can be saved to run again; saved ones are private unless
// shared, and whoever runs one only gets what they can see.

import {
  ActionIcon, Anchor, Badge, Button, Checkbox, Divider, Grid, Group, Modal, Paper, Radio, SegmentedControl, Select, Stack,
  Switch, Text, TextInput, Title, Tooltip,
} from "@mantine/core";
import { modals } from "@mantine/modals";
import { IconAdjustmentsHorizontal, IconDownload, IconFileExport, IconTrash, IconX } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api, ApiError, postDownload } from "../api";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import { PageSkeleton } from "../components/ui";
import { useApp, useSchema } from "../context";
import { FilterDrawer } from "../module/ListPage";
import type { ModuleSchema, Page, RecordData } from "../types";

type Format = "xlsx" | "csv" | "pdf";
type Layout = "table" | "pages";
type Config = {
  module: string; columns: string[]; filters: Record<string, string>; ordering: string;
  format: Format; layout: Layout; title: string;
};
type Saved = {
  id: number; name: string; module: string; module_label: string; shared: boolean; mine: boolean; owner: string;
  config: Config; updated_at: string;
};
const FORMAT_LABEL: Record<Format, string> = { xlsx: "Excel", csv: "CSV", pdf: "PDF" };
const NOT_FILTERS = new Set(["q", "ordering", "page", "page_size", "columns", "module"]);

export default function ExportSettings() {
  const { data: schema } = useSchema();
  const { info, me } = useApp();
  const qc = useQueryClient();
  const [urlParams] = useSearchParams();
  const modules = useMemo(
    () => (schema?.modules ?? []).filter((m) => info.built_modules.includes(m.key) && me.perms[m.key]?.view),
    [schema, info, me],
  );

  const [cfg, setCfg] = useState<Config | null>(null);
  const [loaded, setLoaded] = useState<Saved | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const module = modules.find((m) => m.key === cfg?.module);

  // Start from a list ("Other formats" on a list's Export) or from the first module.
  const started = useRef(false);
  useEffect(() => {
    if (started.current || !modules.length) return;
    started.current = true;
    const asked = modules.find((m) => m.key === urlParams.get("module")) ?? modules[0];
    const filters: Record<string, string> = {};
    urlParams.forEach((v, k) => { if (!NOT_FILTERS.has(k) || k === "q") filters[k] = v; });
    const columns = (urlParams.get("columns") ?? "").split(",").filter(Boolean);
    setCfg({
      ...fresh(asked), filters: urlParams.get("module") ? filters : fresh(asked).filters,
      columns: columns.length ? columns : fresh(asked).columns, ordering: urlParams.get("ordering") ?? "",
    });
  }, [modules, urlParams]);

  const reports = useQuery({ queryKey: ["reports"], queryFn: () => api<{ mine: Saved[]; shared: Saved[] }>("reports/") });

  // How many records the report covers, through the list itself (so it matches what the list shows).
  const countParams = useMemo(() => {
    if (!cfg) return "";
    const p = new URLSearchParams(cfg.filters);
    p.set("page_size", "1");
    return p.toString();
  }, [cfg]);
  const count = useQuery({
    queryKey: ["records", cfg?.module, "count", countParams],
    queryFn: () => api<Page<RecordData>>(`m/${cfg!.module}/?${countParams}`),
    enabled: !!cfg,
    retry: false,
  });

  if (!schema || !cfg || !module) return <PageSkeleton rows={8} />;
  const set = (patch: Partial<Config>) => setCfg((c) => ({ ...c!, ...patch }));
  const params = new URLSearchParams(cfg.filters);
  const filterKeys = Object.keys(cfg.filters).filter((k) => !NOT_FILTERS.has(k) && k !== module.segments?.param
    && !module.quick_filters.some((q) => q.param === k));
  const enabledFields = module.fields.filter((f) => f.enabled);
  const vaultLocked = count.error instanceof ApiError && String(count.error.code ?? "").startsWith("vault");

  function pickModule(key: string | null) {
    const m = modules.find((x) => x.key === key);
    if (!m) return;
    setLoaded(null);
    setCfg(fresh(m));
  }

  function loadSaved(r: Saved) {
    const m = modules.find((x) => x.key === r.module);
    if (!m) return;
    setLoaded(r);
    setCfg({ ...fresh(m), ...r.config });
    notifyOk(`Opened “${r.name}”. Change anything, then download or save.`);
  }

  async function downloadNow() {
    setBusy(true);
    try {
      await postDownload("reports/run/", { config: cfg });
    } catch (e) {
      notifyError(e, "Couldn't make the file");
    } finally {
      setBusy(false);
    }
  }

  async function runSaved(r: Saved) {
    try {
      await postDownload(`reports/${r.id}/run/`, {});
    } catch (e) {
      notifyError(e, "Couldn't make the file");
    }
  }

  function remove(r: Saved) {
    modals.openConfirmModal({
      title: `Delete “${r.name}”?`,
      children: <Text size="sm">{r.shared ? "It's shared, so it goes for everyone." : "Only the saved settings go; files you downloaded stay."}</Text>,
      labels: { confirm: "Delete", cancel: "Cancel" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await api(`reports/${r.id}/`, { method: "DELETE" });
          if (loaded?.id === r.id) setLoaded(null);
          await qc.invalidateQueries({ queryKey: ["reports"] });
          notifyOk(`“${r.name}” deleted.`);
        } catch (e) {
          notifyError(e, "Couldn't delete");
        }
      },
    });
  }

  async function saveChanges() {
    if (!loaded) return;
    try {
      const r = await api<Saved>(`reports/${loaded.id}/`, { method: "PATCH", body: { config: cfg } });
      setLoaded(r);
      await qc.invalidateQueries({ queryKey: ["reports"] });
      notifyOk(`“${r.name}” saved.`);
    } catch (e) {
      notifyError(e, "Couldn't save");
    }
  }

  const allColumns = cfg.columns.length === 0;
  const toggleColumn = (key: string, on: boolean) => {
    const current = allColumns ? enabledFields.map((f) => f.key) : cfg.columns;
    set({ columns: on ? enabledFields.map((f) => f.key).filter((k) => k === key || current.includes(k)) : current.filter((k) => k !== key) });
  };
  const orderKey = cfg.ordering.replace(/^-/, "");
  const sortable = enabledFields.filter((f) => f.sortable);

  return (
    <Grid gutter="lg" align="flex-start">
      <Grid.Col span={{ base: 12, lg: 8 }}>
        <Paper withBorder p="lg">
          <Stack gap="lg">
            <div>
              <Title order={4}>Export a report</Title>
              <Text size="sm" c="dimmed">
                Pick what to export, which records and columns, and a format. You only ever get what you can see in Dealflow.
              </Text>
              {loaded && (
                <Group gap={6} mt={6}>
                  <Badge variant="light" color="navy">Editing “{loaded.name}”</Badge>
                  <Anchor size="xs" component="button" type="button" onClick={() => pickModule(module.key)}>Start a new report</Anchor>
                </Group>
              )}
            </div>

            <Group grow align="flex-start">
              <Select label="What to export" data={modules.map((m) => ({ value: m.key, label: m.label }))} value={cfg.module}
                onChange={pickModule} allowDeselect={false} />
              <TextInput label="Title" description="Shown at the top of a PDF and in the file name" value={cfg.title}
                onChange={(e) => set({ title: e.currentTarget.value })} />
            </Group>

            <Section title="Which records">
              <Group gap="sm" align="flex-end" wrap="wrap">
                {module.segments && (
                  <Select label="Show" w={200} data={module.segments.options} allowDeselect={false}
                    value={cfg.filters[module.segments.param] ?? module.segments.default}
                    onChange={(v) => set({ filters: { ...cfg.filters, [module.segments!.param]: v ?? "" } })} />
                )}
                <TextInput label="Search" placeholder={`Search ${module.label.toLowerCase()}…`} w={240} value={cfg.filters.q ?? ""}
                  onChange={(e) => set({ filters: { ...cfg.filters, q: e.currentTarget.value } })} />
                <Button variant={filterKeys.length ? "light" : "default"} color={filterKeys.length ? "navy" : "gray"}
                  leftSection={<IconAdjustmentsHorizontal size={15} />} onClick={() => setFiltersOpen(true)}>
                  Filters{filterKeys.length ? ` (${filterKeys.length} on)` : ""}
                </Button>
                {filterKeys.length > 0 && (
                  <Button variant="subtle" color="gray" leftSection={<IconX size={14} />}
                    onClick={() => set({ filters: Object.fromEntries(Object.entries(cfg.filters).filter(([k]) => !filterKeys.includes(k))) })}>
                    Clear filters
                  </Button>
                )}
              </Group>
              {module.quick_filters.map((q) => (
                <Checkbox key={q.param} mt="xs" label={q.label} description={q.help} checked={cfg.filters[q.param] === q.value}
                  onChange={(e) => {
                    const next = { ...cfg.filters };
                    if (e.currentTarget.checked) next[q.param] = q.value;
                    else delete next[q.param];
                    set({ filters: next });
                  }} />
              ))}
              <Text size="sm" mt="xs" c={vaultLocked || count.error ? "red" : undefined}>
                {vaultLocked ? (
                  <>The Vault is locked. <Anchor component={Link} to="/m/vault">Open it with your PIN</Anchor>, then come back.</>
                ) : count.error ? errorMessage(count.error) : count.data ? (
                  <b>{count.data.count.toLocaleString(me.number_grouping === "indian" ? "en-IN" : "en-US")} {count.data.count === 1 ? "record" : "records"}</b>
                ) : "Counting…"}
              </Text>
            </Section>

            <Section title="Columns" hint={cfg.format === "pdf" && cfg.layout === "pages"
              ? "On one page per record, these are the fields shown (empty ones are left out)."
              : `${module.fields.find((f) => f.key === module.title_field)?.label ?? "The name"} always comes first.`}>
              <Group gap={6} mb="xs">
                <Button size="compact-xs" variant="default" onClick={() => set({ columns: enabledFields.filter((f) => f.list_default).map((f) => f.key) })}>The list's columns</Button>
                <Button size="compact-xs" variant="default" onClick={() => set({ columns: [] })}>Every field</Button>
                <Button size="compact-xs" variant="default" onClick={() => set({ columns: [module.title_field] })}>Just the name</Button>
              </Group>
              {module.groups.map((g) => {
                const fields = enabledFields.filter((f) => f.group === g);
                if (!fields.length) return null;
                return (
                  <div key={g}>
                    <Text size="xs" fw={600} c="dimmed" mt="xs" mb={4}>{g}</Text>
                    <Group gap="md">
                      {fields.map((f) => (
                        <Checkbox key={f.key} size="xs" label={f.label} disabled={f.key === module.title_field}
                          checked={f.key === module.title_field || allColumns || cfg.columns.includes(f.key)}
                          onChange={(e) => toggleColumn(f.key, e.currentTarget.checked)} />
                      ))}
                    </Group>
                  </div>
                );
              })}
            </Section>

            <Section title="Sort by">
              <Group gap="sm">
                <Select w={240} placeholder="As the list sorts" clearable data={sortable.map((f) => ({ value: f.key, label: f.label }))}
                  value={orderKey || null} onChange={(v) => set({ ordering: v ? (cfg.ordering.startsWith("-") ? `-${v}` : v) : "" })} />
                <SegmentedControl disabled={!orderKey} value={cfg.ordering.startsWith("-") ? "desc" : "asc"}
                  data={[{ value: "asc", label: "Low to high / A–Z" }, { value: "desc", label: "High to low / Z–A" }]}
                  onChange={(v) => set({ ordering: v === "desc" ? `-${orderKey}` : orderKey })} />
              </Group>
            </Section>

            <Section title="Format">
              <SegmentedControl value={cfg.format} onChange={(v) => set({ format: v as Format })}
                data={[{ value: "xlsx", label: "Excel" }, { value: "csv", label: "CSV" }, { value: "pdf", label: "PDF" }]} />
              <Text size="xs" c="dimmed" mt={6}>
                {cfg.format === "xlsx" ? "A spreadsheet that can be imported back into Dealflow. Up to 50,000 records."
                  : cfg.format === "csv" ? "Plain text that any spreadsheet or tool can open. Up to 50,000 records."
                    : "A file to print or send. Your date and number formats are used."}
              </Text>
              {cfg.format === "pdf" && (
                <Radio.Group mt="sm" value={cfg.layout} onChange={(v) => set({ layout: v as Layout })}>
                  <Stack gap={6}>
                    <Radio value="table" label="A table" description="One row per record with the chosen columns, up to 2,000 records." />
                    <Radio value="pages" label="One page per record" description="Each record's fields, plus a deal's investor tracker and recent activity. Up to 200 records." />
                  </Stack>
                </Radio.Group>
              )}
            </Section>

            <Divider />
            <Group justify="space-between">
              <Group gap="xs">
                {loaded?.mine && <Button variant="default" onClick={saveChanges}>Save changes to “{loaded.name}”</Button>}
                <Button variant="default" onClick={() => setSaveOpen(true)}>{loaded?.mine ? "Save as a new report…" : "Save this report…"}</Button>
              </Group>
              <Button leftSection={<IconDownload size={16} />} loading={busy} onClick={downloadNow} disabled={vaultLocked}>
                Download {FORMAT_LABEL[cfg.format]}
              </Button>
            </Group>
          </Stack>
        </Paper>
      </Grid.Col>

      <Grid.Col span={{ base: 12, lg: 4 }}>
        <Paper withBorder p="lg">
          <Title order={5} mb={4}>Saved reports</Title>
          <Text size="xs" c="dimmed" mb="sm">Run one again with today's records, or open it to change it.</Text>
          {reports.isLoading ? <Text size="sm" c="dimmed">Loading…</Text> : reports.error ? <Text c="red" size="sm">{errorMessage(reports.error)}</Text> : (
            <Stack gap="md">
              <SavedList title="Yours" items={reports.data!.mine} empty="None yet. Build a report and choose “Save this report”."
                onRun={runSaved} onOpen={loadSaved} onDelete={remove} />
              <SavedList title="Shared by others" items={reports.data!.shared} empty="Nobody has shared a report yet."
                onRun={runSaved} onOpen={loadSaved} onDelete={me.role.is_admin ? remove : undefined} />
            </Stack>
          )}
        </Paper>
      </Grid.Col>

      <FilterDrawer opened={filtersOpen} onClose={() => setFiltersOpen(false)} module={module} schema={schema} params={params}
        onApply={(u) => {
          const next = { ...cfg.filters };
          for (const [k, v] of Object.entries(u)) {
            if (v) next[k] = v;
            else delete next[k];
          }
          set({ filters: next });
          setFiltersOpen(false);
        }} />
      <SaveModal opened={saveOpen} onClose={() => setSaveOpen(false)} cfg={cfg} defaultName={loaded ? `${loaded.name} (copy)` : cfg.title}
        onSaved={(r) => { setLoaded(r); qc.invalidateQueries({ queryKey: ["reports"] }); }} />
    </Grid>
  );
}

function fresh(m: ModuleSchema): Config {
  return {
    module: m.key,
    columns: m.fields.filter((f) => f.enabled && f.list_default).map((f) => f.key),
    filters: m.segments ? { [m.segments.param]: m.segments.default } : {},
    ordering: "",
    format: "xlsx",
    layout: "table",
    title: `${m.label} report`,
  };
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <Text fw={600} size="sm">{title}</Text>
      {hint && <Text size="xs" c="dimmed" mb={6}>{hint}</Text>}
      <div style={{ marginTop: hint ? 0 : 6 }}>{children}</div>
    </div>
  );
}

function SavedList({ title, items, empty, onRun, onOpen, onDelete }: {
  title: string; items: Saved[]; empty: string; onRun: (r: Saved) => void; onOpen: (r: Saved) => void; onDelete?: (r: Saved) => void;
}) {
  return (
    <div>
      <Text size="xs" fw={600} c="dimmed" mb={6}>{title}</Text>
      {items.length === 0 ? <Text size="sm" c="dimmed">{empty}</Text> : (
        <Stack gap={10}>
          {items.map((r) => (
            <div key={r.id}>
              <Group justify="space-between" wrap="nowrap" gap={4}>
                <div style={{ minWidth: 0 }}>
                  <Text size="sm" fw={500} truncate>{r.name}</Text>
                  <Text size="xs" c="dimmed" truncate>
                    {r.module_label} · {FORMAT_LABEL[r.config.format]}{r.config.format === "pdf" ? (r.config.layout === "pages" ? ", page per record" : ", table") : ""}
                    {r.mine ? (r.shared ? " · shared" : "") : ` · by ${r.owner}`}
                  </Text>
                </div>
                {(r.mine || onDelete) && onDelete && (
                  <Tooltip label="Delete this saved report">
                    <ActionIcon variant="subtle" color="gray" aria-label={`Delete ${r.name}`} onClick={() => onDelete(r)}><IconTrash size={15} /></ActionIcon>
                  </Tooltip>
                )}
              </Group>
              <Group gap={6} mt={4}>
                <Button size="compact-xs" leftSection={<IconFileExport size={13} />} onClick={() => onRun(r)}>Download</Button>
                <Button size="compact-xs" variant="default" onClick={() => onOpen(r)}>{r.mine ? "Open to change" : "Open a copy"}</Button>
              </Group>
            </div>
          ))}
        </Stack>
      )}
    </div>
  );
}

function SaveModal({ opened, onClose, cfg, defaultName, onSaved }: {
  opened: boolean; onClose: () => void; cfg: Config; defaultName: string; onSaved: (r: Saved) => void;
}) {
  const [name, setName] = useState(defaultName);
  const [shared, setShared] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [wasOpen, setWasOpen] = useState(false);
  if (opened !== wasOpen) {
    setWasOpen(opened);
    if (opened) { setName(defaultName); setShared(false); setError(null); }
  }
  async function save() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<Saved>("reports/", { body: { name, shared, config: cfg } });
      notifyOk(`“${r.name}” saved. It's under Saved reports.`);
      onSaved(r);
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal opened={opened} onClose={onClose} title={<Text fw={600}>Save this report</Text>}>
      <Stack>
        <TextInput label="Name" data-autofocus value={name} onChange={(e) => setName(e.currentTarget.value)} error={error} />
        <Switch checked={shared} onChange={(e) => setShared(e.currentTarget.checked)} label="Share with everyone"
          description="Colleagues can run it too. Each person still gets only the records and fields they can see." />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button onClick={save} loading={busy} disabled={!name.trim()}>Save</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
