import {
  Button, Checkbox, CloseButton, Drawer, Group, Loader, Menu, MultiSelect, NumberInput,
  Pagination, Paper, ScrollArea, SegmentedControl, Select, Stack, Table, Tabs, TagsInput, Text, TextInput,
} from "@mantine/core";
import { DateInput } from "@mantine/dates";
import { useDebouncedCallback, useDebouncedValue } from "@mantine/hooks";
import {
  IconAdjustmentsHorizontal, IconArrowDown, IconArrowsSort, IconArrowUp, IconArrowUpRight, IconColumns3, IconDownload, IconFileImport, IconLayoutKanban,
  IconLock, IconPlus, IconSearch, IconTable, IconX,
} from "@tabler/icons-react";
import { keepPreviousData, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, download, qs } from "../api";
import { useMe, useModuleSchema, useSchema, useUsers } from "../context";
import { errorMessage } from "../components/errors";
import { fieldIcon, moduleIcon, PageHeader, TableSkeleton } from "../components/ui";
import { MONTH_NAMES } from "../format";
import type { FieldSchema, LookupItem, ModuleSchema, Page, RecordData, Schema } from "../types";
import MyTasks from "../tasks/MyTasks";
import { afterVaultChange } from "../vault/api";
import VaultGate from "../vault/VaultGate";
import BoardView from "./BoardView";
import { FieldValue } from "./fields";
import { useModuleLabels } from "./LinkInput";

const PAGE_SIZE = 50;
// How a list counts its records when the module's name doesn't work as a plural ("2 vault").
const NOUNS: Record<string, [string, string]> = {
  vault: ["document", "documents"], tasks: ["task", "tasks"], deals: ["deal", "deals"],
};
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
  if (isLoading) return <TableSkeleton />;
  if (!module || !schema) return <Navigate to="/" replace />;
  const inner = <ListInner key={module.key} module={module} schema={schema} />;
  return module.key === "vault" ? <VaultGate>{inner}</VaultGate> : inner;
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
  // Quick switch above the list (e.g. Active / Won / Lost). Its default isn't in the address.
  const segParam = module.segments?.param;
  const segValue = segParam ? params.get(segParam) ?? module.segments!.default : "";
  const withSegment = (p: URLSearchParams) => {
    if (segParam && segValue && !p.has(segParam)) p.set(segParam, segValue);
    return p;
  };
  const [view, setView] = useState<"board" | "table">(() => (module.pipeline ? loadView(module.key) : "table"));
  const board = !!module.pipeline && view === "board";
  // Tasks open on "My tasks", a grouped to-do list rather than a table.
  const mine = module.key === "tasks" && segValue === "mine";
  const apiParams = withSegment(new URLSearchParams(params));
  apiParams.set("page_size", String(PAGE_SIZE));
  const queryString = apiParams.toString();

  const { data, isFetching, error } = useQuery({
    queryKey: ["records", module.key, queryString],
    queryFn: () => api<Page<RecordData>>(`m/${module.key}/?${queryString}`),
    placeholderData: keepPreviousData,
    enabled: !board && !mine,
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

  const activeFilters = [...params.keys()].filter((k) => !RESERVED.has(k) && k !== segParam);
  const exportParams = board ? new URLSearchParams(params) : withSegment(new URLSearchParams(params));
  if (board && segParam) exportParams.delete(segParam);
  exportParams.delete("page");
  exportParams.set("columns", shown.map((f) => f.key).join(","));

  const total = data?.count ?? 0;

  return (
    <Stack gap="md">
      <PageHeader icon={moduleIcon(module.key)} title={module.label}
        sub={mine ? "Assigned to you and not done yet" : data ? `${total.toLocaleString(me.number_grouping === "indian" ? "en-IN" : "en-US")} ${total === 1 ? (NOUNS[module.key]?.[0] ?? module.singular.toLowerCase()) : (NOUNS[module.key]?.[1] ?? module.label.toLowerCase())}` : "\u00a0"}
        actions={
          <>
            {module.perms.create && module.importable && (
              <Button variant="default" leftSection={<IconFileImport size={16} />} component={Link} to={`/m/${module.key}/import`}>Import</Button>
            )}
            <Button variant="default" leftSection={<IconDownload size={16} />} onClick={() => download(`m/${module.key}/export/?${exportParams}`)}>
              Export
            </Button>
            {module.key === "vault" && <LockButton />}
            {module.perms.create && (
              <Button leftSection={<IconPlus size={16} />} component={Link} to={`/m/${module.key}/new`}>
                {module.key === "vault" ? "Upload" : `New ${module.singular.toLowerCase()}`}
              </Button>
            )}
          </>
        } />

      <ModuleTabs current={module.key} />
      <Group gap="xs" justify="space-between" wrap="wrap" mt={4}>
        <Group gap={4} wrap="wrap">
          {module.pipeline && (
            <>
              <ViewTab active={view === "board"} icon={<IconLayoutKanban size={16} />} label="Board"
                onClick={() => { setView("board"); saveView(module.key, "board"); }} />
              <ViewTab active={view === "table"} icon={<IconTable size={16} />} label="Table"
                onClick={() => { setView("table"); saveView(module.key, "table"); }} />
            </>
          )}
          {module.segments && !board && (
            <SegmentedControl ml={module.pipeline ? "sm" : 0} data={module.segments.options} value={segValue}
              onChange={(v) => setParam({ [module.segments!.param]: v === module.segments!.default ? null : v || null })} />
          )}
        </Group>
        <Group gap={4} wrap="nowrap">
          {isFetching && <Loader size="xs" mr={4} />}
          <TextInput size="xs" w={240} placeholder={`Search ${NOUNS[module.key]?.[1] ?? module.label.toLowerCase()}…`}
            aria-label={`Search ${module.label}`} leftSection={<IconSearch size={14} />} value={search}
            rightSection={search ? <CloseButton size="xs" onClick={() => { setSearch(""); setParam({ q: null }); }} aria-label="Clear search" /> : null}
            onChange={(e) => { setSearch(e.currentTarget.value); pushSearch(e.currentTarget.value); }} />
          <Button variant={activeFilters.length ? "light" : "default"} color={activeFilters.length ? "navy" : "gray"} size="xs"
            leftSection={<IconAdjustmentsHorizontal size={15} />} onClick={() => setFiltersOpen(true)}>
            Filters{activeFilters.length ? ` (${countFilters(activeFilters)} on)` : ""}
          </Button>
          {activeFilters.length > 0 && (
            <Button variant="subtle" color="gray" size="xs" leftSection={<IconX size={14} />} onClick={() => {
              const next = new URLSearchParams();
              if (params.get("q")) next.set("q", params.get("q")!);
              if (ordering) next.set("ordering", ordering);
              if (segParam && params.get(segParam)) next.set(segParam, params.get(segParam)!);
              setParams(next, { replace: true });
            }}>Clear filters</Button>
          )}
          {!board && !mine && <ColumnPicker module={module} columns={shown.map((f) => f.key)} onChange={(c) => { setColumns(c); saveColumns(module.key, c); }} />}
        </Group>
      </Group>

      {!mine && (
        <Text size="xs" c="dimmed" mt={-6}>
          {board
            ? "Drag a card to another column to move it to that stage. Click a card to open it."
            : "Click a row to open it. Click a column title to sort by it."}
        </Text>
      )}

      {mine ? (
        <MyTasks module={module} params={params} />
      ) : board ? (
        <BoardView module={module} params={params} />
      ) : error ? (
        <Paper withBorder p="lg"><Text c="red">{errorMessage(error)}</Text></Paper>
      ) : (
        !data ? <TableSkeleton cols={Math.min(shown.length, 6)} /> : (
        <div className="df-db df-fade">
          <ScrollArea type="hover" scrollbarSize={8}>
            <Table className="df-table" verticalSpacing={8} horizontalSpacing="sm" miw={900}>
              <Table.Thead>
                <Table.Tr>
                  {shown.map((f) => {
                    const I = fieldIcon(f);
                    return (
                      <Table.Th key={f.key} className={f.sortable ? "df-sort" : undefined} onClick={() => sortBy(f)}>
                        <span className="df-th">
                          <I size={14} stroke={1.7} />
                          {f.label}
                          {ordering === f.key ? <IconArrowUp size={12} /> : ordering === `-${f.key}` ? <IconArrowDown size={12} />
                            : f.sortable ? <IconArrowsSort size={12} className="df-sort-hint" /> : null}
                        </span>
                      </Table.Th>
                    );
                  })}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.results.map((r) => (
                  <Table.Tr key={r.id} className="df-row-link" onClick={() => nav(`/m/${module.key}/${r.id}`)}>
                    {shown.map((f) => (
                      <Table.Td key={f.key} maw={f.type === "longtext" ? 320 : undefined}
                        miw={f.key === module.title_field ? 220 : f.multi || f.type === "tags" ? 200 : undefined}>
                        {f.key === module.title_field ? (
                          <div className="df-title-cell">
                            <Text size="sm" className="df-title-text">{r._title}</Text>
                            <span className="df-open-chip"><IconArrowUpRight size={11} />Open</span>
                          </div>
                        ) : (
                          <FieldValue f={f} v={r[f.key]} compact />
                        )}
                      </Table.Td>
                    ))}
                  </Table.Tr>
                ))}
                {data.results.length === 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={shown.length}>
                      <EmptyState module={module} filtered={activeFilters.length > 0 || !!params.get("q") || !!(segParam && params.get(segParam))} />
                    </Table.Td>
                  </Table.Tr>
                )}
              </Table.Tbody>
            </Table>
          </ScrollArea>
          {module.perms.create && data.results.length > 0 && (
            <Link to={`/m/${module.key}/new`} className="df-ghost-link" style={{ margin: "6px 0 0", textDecoration: "none" }}>
              <IconPlus size={14} /> {module.key === "vault" ? "Upload" : "New"}
            </Link>
          )}
        </div>
        )
      )}

      {!board && !mine && total > PAGE_SIZE && (
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

function ViewTab({ active, icon, label, onClick }: { active: boolean; icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <Button variant="subtle" color={active ? "dark" : "gray"} size="compact-sm" leftSection={icon} onClick={onClick}
      fw={active ? 600 : 500} c={active ? undefined : "dimmed"} bg={active ? "var(--df-hover)" : undefined}
      aria-pressed={active}>
      {label}
    </Button>
  );
}

/** Close the Vault now rather than waiting for it to close after 15 idle minutes. */
function LockButton() {
  const qc = useQueryClient();
  return (
    <Button variant="default" leftSection={<IconLock size={16} />}
      onClick={async () => afterVaultChange(qc, await api("vault/lock/", { method: "POST" }))}>
      Lock
    </Button>
  );
}

function loadView(module: string): "board" | "table" {
  try {
    return localStorage.getItem(`dealflow.view.${module}`) === "table" ? "table" : "board";
  } catch {
    return "board";
  }
}

function saveView(module: string, v: string) {
  try {
    localStorage.setItem(`dealflow.view.${module}`, v);
  } catch {
    /* ignore */
  }
}

// Record types that live together under one menu item.
const MODULE_TABS: Record<string, { key: string; label: string }[]> = {
  outreach: [{ key: "outreach", label: "Pipeline" }, { key: "companies", label: "Companies" }],
  companies: [{ key: "outreach", label: "Pipeline" }, { key: "companies", label: "Companies" }],
};

function ModuleTabs({ current }: { current: string }) {
  const nav = useNavigate();
  const { data: schema } = useSchema();
  const tabs = (MODULE_TABS[current] ?? []).filter((t) => schema?.modules.some((m) => m.key === t.key));
  if (tabs.length < 2) return null;
  return (
    <Tabs value={current} onChange={(v) => v && nav(`/m/${v}`)}>
      <Tabs.List>
        {tabs.map((t) => <Tabs.Tab key={t.key} value={t.key}>{t.label}</Tabs.Tab>)}
      </Tabs.List>
    </Tabs>
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
        {filtered ? "Try a different search or clear the filters."
          : module.key === "deals" ? "Mandates won in Outreach appear here automatically, or add a deal by hand."
            : module.perms.create ? (module.importable ? "Add one, or import your existing Excel sheet." : "Add the first one.")
              : "Nothing has been added yet."}
      </Text>
      {!filtered && module.perms.create && (
        <Group mt="xs">
          <Button size="xs" component={Link} to={`/m/${module.key}/new`}>{module.key === "vault" ? "Upload documents" : `New ${module.singular.toLowerCase()}`}</Button>
          {module.importable && (
            <Button size="xs" variant="default" component={Link} to={`/m/${module.key}/import`}>Import from Excel</Button>
          )}
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
        <Button variant="default" size="xs" leftSection={<IconColumns3 size={15} />}>Columns</Button>
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
    if (f.type === "date" || f.type === "datetime") { keys.add(`${f.key}_from`); keys.add(`${f.key}_to`); }
    else if (f.type === "birthday") keys.add(`${f.key}_month`);
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
            case "date": case "datetime":
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
            case "link":
              return <LinkFilter key={f.key} f={f} value={csv(f.key)} onChange={(v) => set(f.key, v.join(","))} />;
            case "birthday":
              return (
                <MultiSelect key={f.key} label={`${f.label} in`} clearable placeholder="Any month"
                  data={MONTH_NAMES.map((m, i) => ({ value: String(i + 1), label: m }))}
                  value={csv(`${f.key}_month`)} onChange={(v) => set(`${f.key}_month`, v.join(","))} />
              );
            default:
              return <TextInput key={f.key} label={f.label} placeholder="Contains…" value={draft[f.key] || ""} onChange={(e) => set(f.key, e.currentTarget.value)} />;
          }
        })}
        <Group justify="space-between" mt="md">
          <Button variant="subtle" onClick={() => setDraft(Object.fromEntries([...keys].map((k) => [k, ""])))}>Reset</Button>
          <Button onClick={() => onApply(Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, v || null])))}>Apply filters</Button>
        </Group>
        {[...params.keys()].some((k) => !RESERVED.has(k) && k !== module.segments?.param) && (
          <Text size="xs" c="dimmed">Filters are kept in the page address, so you can bookmark this view.</Text>
        )}
      </Stack>
    </Drawer>
  );
}

/** Filter by specific linked records (e.g. contacts at Blume and Kotak). Searches on the server. */
function LinkFilter({ f, value, onChange }: { f: FieldSchema; value: string[]; onChange: (v: string[]) => void }) {
  const labels = useModuleLabels();
  const [search, setSearch] = useState("");
  const [debounced] = useDebouncedValue(search.trim(), 200);
  const found = useQueries({
    queries: f.targets.map((m) => ({
      queryKey: ["lookup", m, debounced],
      queryFn: () => api<LookupItem[]>(`lookup/${m}/${qs({ q: debounced })}`),
      staleTime: 30_000,
    })),
  });
  // Names for records already chosen (they may not be in the current search results).
  const chosen = useQueries({
    queries: f.targets.map((m) => {
      const ids = value.filter((v) => v.startsWith(`${m}:`)).map((v) => v.split(":")[1]);
      return {
        queryKey: ["lookup-ids", m, ids.join(",")],
        queryFn: () => api<LookupItem[]>(`lookup/${m}/${qs({ ids: ids.join(",") })}`),
        enabled: ids.length > 0,
        staleTime: 60_000,
      };
    }),
  });
  const items = new Map<string, string>();
  [...chosen, ...found].forEach((r) => r.data?.forEach((x) => items.set(`${x.module}:${x.id}`, x.title)));
  value.forEach((v) => { if (!items.has(v)) items.set(v, "…"); });
  const noun = f.targets.map((m) => (labels[m] ?? m).toLowerCase()).join(" or ");
  return (
    <MultiSelect label={f.label} searchable clearable placeholder={value.length ? undefined : `Search ${noun}`}
      data={[...items].map(([v, label]) => ({ value: v, label }))}
      value={value} onChange={onChange}
      searchValue={search} onSearchChange={setSearch} filter={({ options }) => options}
      nothingFoundMessage={debounced ? `No ${noun} match` : undefined} />
  );
}
