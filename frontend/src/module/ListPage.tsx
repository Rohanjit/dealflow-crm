import {
  ActionIcon, Button, Center, Checkbox, CloseButton, Drawer, Group, Loader, Menu, MultiSelect, NumberInput,
  Pagination, Paper, ScrollArea, Select, Stack, Table, TagsInput, Text, TextInput, Title, Tooltip,
} from "@mantine/core";
import { DateInput } from "@mantine/dates";
import { useDebouncedCallback } from "@mantine/hooks";
import {
  IconAdjustmentsHorizontal, IconArrowDown, IconArrowUp, IconColumns3, IconDownload, IconFileImport, IconPlus,
  IconSearch,
} from "@tabler/icons-react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, download } from "../api";
import { useMe, useModuleSchema, useUsers } from "../context";
import { errorMessage } from "../components/errors";
import type { FieldSchema, ModuleSchema, Page, RecordData, Schema } from "../types";
import { FieldValue } from "./fields";

const PAGE_SIZE = 50;
const RESERVED = new Set(["q", "ordering", "page"]);

function loadColumns(module: ModuleSchema): string[] {
  const enabled = module.fields.filter((f) => f.enabled);
  const fallback = enabled.filter((f) => f.list_default).map((f) => f.key);
  try {
    const saved = JSON.parse(localStorage.getItem(`dealflow.columns.${module.key}`) || "null");
    if (Array.isArray(saved)) {
      const keys = saved.filter((k) => enabled.some((f) => f.key === k));
      if (keys.length) return keys;
    }
  } catch {
    /* storage unavailable: use defaults */
  }
  return fallback;
}

function saveColumns(module: string, keys: string[]) {
  try {
    localStorage.setItem(`dealflow.columns.${module}`, JSON.stringify(keys));
  } catch {
    /* ignore */
  }
}

export default function ListPage() {
  const { module: key } = useParams();
  const { module, schema, isLoading } = useModuleSchema(key);
  if (isLoading) return <Center py="xl"><Loader /></Center>;
  if (!module || !schema) return <Navigate to="/" replace />;
  return <ListInner key={module.key} module={module} schema={schema} />;
}

function ListInner({ module, schema }: { module: ModuleSchema; schema: Schema }) {
  const me = useMe();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const [columns, setColumns] = useState<string[]>(() => loadColumns(module));
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [search, setSearch] = useState(params.get("q") ?? "");

  const fieldMap = useMemo(() => Object.fromEntries(module.fields.map((f) => [f.key, f])), [module]);
  const shown = columns.map((k) => fieldMap[k]).filter((f): f is FieldSchema => !!f && f.enabled);
  if (!shown.some((f) => f.key === module.title_field)) shown.unshift(fieldMap[module.title_field]);

  const page = Number(params.get("page") || 1);
  const apiParams = new URLSearchParams(params);
  apiParams.set("page_size", String(PAGE_SIZE));
  const queryString = apiParams.toString();

  const { data, isFetching, error } = useQuery({
    queryKey: ["records", module.key, queryString],
    queryFn: () => api<Page<RecordData>>(`m/${module.key}/?${queryString}`),
    placeholderData: keepPreviousData,
  });

  const setParam = (updates: Record<string, string | null>, resetPage = true) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(updates)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    if (resetPage) next.delete("page");
    setParams(next, { replace: true });
  };
  const pushSearch = useDebouncedCallback((v: string) => setParam({ q: v.trim() || null }), 300);

  const ordering = params.get("ordering") ?? "";
  const sortBy = (f: FieldSchema) => {
    if (!f.sortable) return;
    const next = ordering === f.key ? `-${f.key}` : ordering === `-${f.key}` ? null : f.key;
    setParam({ ordering: next });
  };

  const activeFilters = [...params.keys()].filter((k) => !RESERVED.has(k));
  const exportParams = new URLSearchParams(params);
  exportParams.delete("page");
  exportParams.set("columns", shown.map((f) => f.key).join(","));

  const total = data?.count ?? 0;

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>{module.label}</Title>
          <Text c="dimmed" size="sm">{data ? `${total.toLocaleString(me.number_grouping === "indian" ? "en-IN" : "en-US")} ${total === 1 ? module.singular.toLowerCase() : module.label.toLowerCase()}` : " "}</Text>
        </div>
        <Group gap="xs">
          {module.perms.create && (
            <Button variant="default" leftSection={<IconFileImport size={16} />} component={Link} to={`/m/${module.key}/import`}>Import</Button>
          )}
          <Button variant="default" leftSection={<IconDownload size={16} />} onClick={() => download(`m/${module.key}/export/?${exportParams}`)}>
            Export to Excel
          </Button>
          {module.perms.create && (
            <Button leftSection={<IconPlus size={16} />} component={Link} to={`/m/${module.key}/new`}>New {module.singular.toLowerCase()}</Button>
          )}
        </Group>
      </Group>

      <Group gap="xs">
        <TextInput
          style={{ flex: 1, maxWidth: 420 }}
          placeholder={`Search ${module.label.toLowerCase()}`}
          leftSection={<IconSearch size={16} />}
          value={search}
          rightSection={search ? <CloseButton size="sm" onClick={() => { setSearch(""); setParam({ q: null }); }} /> : null}
          onChange={(e) => { setSearch(e.currentTarget.value); pushSearch(e.currentTarget.value); }}
        />
        <Button variant={activeFilters.length ? "light" : "default"} leftSection={<IconAdjustmentsHorizontal size={16} />} onClick={() => setFiltersOpen(true)}>
          Filters{activeFilters.length ? ` (${countFilters(activeFilters)})` : ""}
        </Button>
        <ColumnPicker module={module} columns={shown.map((f) => f.key)} onChange={(c) => { setColumns(c); saveColumns(module.key, c); }} />
        {activeFilters.length > 0 && (
          <Button variant="subtle" size="compact-sm" onClick={() => {
            const next = new URLSearchParams();
            if (params.get("q")) next.set("q", params.get("q")!);
            if (ordering) next.set("ordering", ordering);
            setParams(next, { replace: true });
          }}>Clear filters</Button>
        )}
        {isFetching && <Loader size="xs" />}
      </Group>

      {error ? (
        <Paper withBorder p="lg"><Text c="red">{errorMessage(error)}</Text></Paper>
      ) : (
        <Paper withBorder radius="md">
          <ScrollArea>
            <Table className="df-table" highlightOnHover verticalSpacing="sm" horizontalSpacing="md" miw={900}>
              <Table.Thead>
                <Table.Tr>
                  {shown.map((f) => (
                    <Table.Th key={f.key} className={f.sortable ? "df-sort" : undefined} onClick={() => sortBy(f)}>
                      <Group gap={4} wrap="nowrap">
                        {f.label}
                        {ordering === f.key && <IconArrowUp size={12} />}
                        {ordering === `-${f.key}` && <IconArrowDown size={12} />}
                      </Group>
                    </Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data?.results.map((r) => (
                  <Table.Tr key={r.id} className="df-row-link" onClick={() => nav(`/m/${module.key}/${r.id}`)}>
                    {shown.map((f) => (
                      <Table.Td key={f.key} maw={f.type === "longtext" ? 320 : undefined}
                        miw={f.key === module.title_field ? 180 : f.multi || f.type === "tags" ? 200 : undefined}>
                        {f.key === module.title_field ? (
                          <Text size="sm" fw={600} c="navy.9">{String(r[f.key] ?? "")}</Text>
                        ) : (
                          <FieldValue f={f} v={r[f.key]} compact />
                        )}
                      </Table.Td>
                    ))}
                  </Table.Tr>
                ))}
                {data && data.results.length === 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={shown.length}>
                      <EmptyState module={module} filtered={activeFilters.length > 0 || !!params.get("q")} />
                    </Table.Td>
                  </Table.Tr>
                )}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        </Paper>
      )}

      {total > PAGE_SIZE && (
        <Group justify="space-between">
          <Text size="sm" c="dimmed">
            Showing {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of {total}
          </Text>
          <Pagination total={Math.ceil(total / PAGE_SIZE)} value={page} onChange={(p) => setParam({ page: String(p) }, false)} />
        </Group>
      )}

      <FilterDrawer
        opened={filtersOpen}
        onClose={() => setFiltersOpen(false)}
        module={module}
        schema={schema}
        params={params}
        onApply={(updates) => { setParam(updates); setFiltersOpen(false); }}
      />
    </Stack>
  );
}

function countFilters(keys: string[]) {
  // A range counts once (x_min + x_max, x_from + x_to, ticket + currency + unit).
  return new Set(keys.map((k) => k.replace(/_(min|max|from|to|currency|unit)$/, ""))).size;
}

function EmptyState({ module, filtered }: { module: ModuleSchema; filtered: boolean }) {
  return (
    <Stack align="center" gap={6} py={48}>
      <Text fw={600}>{filtered ? `No ${module.label.toLowerCase()} match` : `No ${module.label.toLowerCase()} yet`}</Text>
      <Text size="sm" c="dimmed">
        {filtered ? "Try a different search or clear the filters." : module.perms.create ? "Add one, or import your existing Excel sheet." : "Nothing has been added yet."}
      </Text>
      {!filtered && module.perms.create && (
        <Group mt="xs">
          <Button size="xs" component={Link} to={`/m/${module.key}/new`}>New {module.singular.toLowerCase()}</Button>
          <Button size="xs" variant="default" component={Link} to={`/m/${module.key}/import`}>Import from Excel</Button>
        </Group>
      )}
    </Stack>
  );
}

function ColumnPicker({ module, columns, onChange }: { module: ModuleSchema; columns: string[]; onChange: (c: string[]) => void }) {
  const enabled = module.fields.filter((f) => f.enabled);
  const toggle = (k: string) => {
    if (k === module.title_field) return;
    const next = columns.includes(k) ? columns.filter((c) => c !== k) : enabled.map((f) => f.key).filter((x) => columns.includes(x) || x === k);
    onChange(next);
  };
  return (
    <Menu closeOnItemClick={false} position="bottom-start" width={260}>
      <Menu.Target>
        <Tooltip label="Choose columns">
          <ActionIcon variant="default" size="lg" aria-label="Choose columns"><IconColumns3 size={18} /></ActionIcon>
        </Tooltip>
      </Menu.Target>
      <Menu.Dropdown>
        <ScrollArea.Autosize mah={420}>
          {module.groups.map((g) => (
            <div key={g}>
              <Menu.Label>{g}</Menu.Label>
              {enabled.filter((f) => f.group === g).map((f) => (
                <Menu.Item key={f.key} onClick={() => toggle(f.key)} disabled={f.key === module.title_field}
                  leftSection={<Checkbox size="xs" readOnly checked={columns.includes(f.key) || f.key === module.title_field} />}>
                  {f.label}
                </Menu.Item>
              ))}
            </div>
          ))}
        </ScrollArea.Autosize>
        <Menu.Divider />
        <Menu.Item onClick={() => onChange(enabled.filter((f) => f.list_default).map((f) => f.key))}>Reset to default</Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}

type Draft = Record<string, string>;

function FilterDrawer({
  opened, onClose, module, schema, params, onApply,
}: {
  opened: boolean; onClose: () => void; module: ModuleSchema; schema: Schema; params: URLSearchParams;
  onApply: (u: Record<string, string | null>) => void;
}) {
  const me = useMe();
  const { data: users } = useUsers();
  const filterable = module.fields.filter((f) => f.filterable && f.enabled);
  const keys = new Set<string>();
  for (const f of filterable) {
    if (f.type === "date") { keys.add(`${f.key}_from`); keys.add(`${f.key}_to`); }
    else keys.add(f.key);
  }
  if (module.key === "investors") ["ticket", "ticket_currency", "ticket_unit"].forEach((k) => keys.add(k));
  const initial = (): Draft => Object.fromEntries([...keys].map((k) => [k, params.get(k) ?? ""]));
  const [draft, setDraft] = useState<Draft>(initial);
  const [lastOpened, setLastOpened] = useState(false);
  if (opened !== lastOpened) {
    setLastOpened(opened);
    if (opened) setDraft(initial());
  }
  const set = (k: string, v: string) => setDraft((d) => ({ ...d, [k]: v }));
  const csv = (k: string) => (draft[k] ? draft[k].split(",").filter(Boolean) : []);

  return (
    <Drawer opened={opened} onClose={onClose} position="right" title={<Text fw={600}>Filter {module.label.toLowerCase()}</Text>} size="md">
      <Stack>
        {module.key === "investors" && (
          <div>
            <Text size="sm" fw={500}>Ticket size fits</Text>
            <Text size="xs" c="dimmed" mb={6}>Investors whose min–max ticket covers this amount (same currency only).</Text>
            <Group gap={6} wrap="nowrap">
              <Select w={100} placeholder="Cur." data={schema.currencies.map((c) => c.code)} value={draft.ticket_currency || null}
                onChange={(v) => set("ticket_currency", v ?? "")} />
              <NumberInput style={{ flex: 1 }} hideControls placeholder="Amount" value={draft.ticket || ""} onChange={(v) => set("ticket", String(v))} />
              <Select w={92} data={schema.units.map((u) => ({ value: u.value || "none", label: u.value || "—" }))}
                value={draft.ticket_unit || "none"} onChange={(v) => set("ticket_unit", !v || v === "none" ? "" : v)} />
            </Group>
          </div>
        )}
        {filterable.map((f) => {
          switch (f.type) {
            case "dropdown": {
              const opts = schema.choice_lists[f.choice_list!]?.options ?? [];
              return (
                <MultiSelect key={f.key} label={f.label} searchable clearable
                  data={opts.map((o) => ({ value: String(o.id), label: o.is_active ? o.label : `${o.label} (removed)` }))}
                  value={csv(f.key)} onChange={(v) => set(f.key, v.join(","))} />
              );
            }
            case "choice": case "tristate":
              return <MultiSelect key={f.key} label={f.label} clearable data={f.choices} value={csv(f.key)} onChange={(v) => set(f.key, v.join(","))} />;
            case "user":
              return (
                <MultiSelect key={f.key} label={f.label} searchable clearable
                  data={[{ value: "me", label: `Me (${me.full_name})` }, { value: "none", label: "Nobody assigned" },
                    ...(users ?? []).filter((u) => u.id !== me.id).map((u) => ({ value: String(u.id), label: u.full_name }))]}
                  value={csv(f.key)} onChange={(v) => set(f.key, v.join(","))} />
              );
            case "tags":
              return <TagsInput key={f.key} label={f.label} description="Matches any of these" value={csv(f.key)} onChange={(v) => set(f.key, v.join(","))} />;
            case "boolean":
              return <Select key={f.key} label={f.label} clearable data={[{ value: "true", label: "Yes" }, { value: "false", label: "No" }]} value={draft[f.key] || null} onChange={(v) => set(f.key, v ?? "")} />;
            case "date":
              return (
                <div key={f.key}>
                  <Text size="sm" fw={500} mb={4}>{f.label}</Text>
                  <Group gap={6} grow>
                    <DateInput placeholder="From" clearable valueFormat={me.date_format}
                      value={draft[`${f.key}_from`] ? dayjs(draft[`${f.key}_from`]).toDate() : null}
                      onChange={(d) => set(`${f.key}_from`, d ? dayjs(d).format("YYYY-MM-DD") : "")} />
                    <DateInput placeholder="To" clearable valueFormat={me.date_format}
                      value={draft[`${f.key}_to`] ? dayjs(draft[`${f.key}_to`]).toDate() : null}
                      onChange={(d) => set(`${f.key}_to`, d ? dayjs(d).format("YYYY-MM-DD") : "")} />
                  </Group>
                </div>
              );
            default:
              return <TextInput key={f.key} label={f.label} placeholder="Contains…" value={draft[f.key] || ""} onChange={(e) => set(f.key, e.currentTarget.value)} />;
          }
        })}
        <Group justify="space-between" mt="md">
          <Button variant="subtle" onClick={() => setDraft(Object.fromEntries([...keys].map((k) => [k, ""])))}>Reset</Button>
          <Button onClick={() => onApply(Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, v || null])))}>Apply filters</Button>
        </Group>
        {[...params.keys()].some((k) => !RESERVED.has(k)) && (
          <Text size="xs" c="dimmed">Filters are kept in the page address, so you can bookmark this view.</Text>
        )}
      </Stack>
    </Drawer>
  );
}
