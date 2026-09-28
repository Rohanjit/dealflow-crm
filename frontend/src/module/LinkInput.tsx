// Picker for a link field (e.g. a contact's firm): searches the target modules on the server,
// and can create a new record from what was typed when the user is allowed to.

import { Group, Loader, Select, Text, type ComboboxItem, type ComboboxItemGroup } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, ApiError, qs } from "../api";
import { useApp } from "../context";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import type { FieldSchema, LinkValue, LookupItem, ModuleSchema, RecordData } from "../types";

const CREATE = "__create__";

export function useModuleLabels() {
  const { info } = useApp();
  return Object.fromEntries(info.modules.map((m) => [m.key, m.label])) as Record<string, string>;
}

/** Fetch titles for links that arrived without one (e.g. prefilled from the page address). */
export function useLinkTitle(value: LinkValue | null) {
  const needs = !!value && !value.title;
  const q = useQuery({
    queryKey: ["lookup-ids", value?.module, value?.id],
    queryFn: () => api<LookupItem[]>(`lookup/${value!.module}/${qs({ ids: value!.id })}`),
    enabled: needs,
  });
  if (!value) return null;
  if (!needs) return value;
  const hit = q.data?.[0];
  return hit ? { ...value, title: hit.title, archived: hit.archived } : value;
}

export default function LinkInput({
  f, value, onChange, error, schemaModules,
}: {
  f: FieldSchema;
  value: LinkValue | null;
  onChange: (v: LinkValue | null) => void;
  error?: string;
  schemaModules: ModuleSchema[];
}) {
  const { me } = useApp();
  const qc = useQueryClient();
  const labels = useModuleLabels();
  const current = useLinkTitle(value);
  const [search, setSearch] = useState(current?.title ?? "");
  const [creating, setCreating] = useState(false);
  const currentTitle = current?.title ?? "";
  useEffect(() => {
    // A prefilled link gets its name a moment later; show it once it arrives.
    if (currentTitle) setSearch((s) => s || currentTitle);
  }, [currentTitle]);
  const typed = search.trim();
  // When the box shows the chosen record's name, list everything rather than just that name.
  const [debounced] = useDebouncedValue(current && typed === current.title ? "" : typed, 200);

  const results = useQueries({
    queries: f.targets.map((m) => ({
      queryKey: ["lookup", m, debounced],
      queryFn: () => api<LookupItem[]>(`lookup/${m}/${qs({ q: debounced })}`),
      staleTime: 30_000,
    })),
  });
  const loading = results.some((r) => r.isFetching);
  const denied = results.length > 0 && results.every((r) => r.error instanceof ApiError && r.error.status === 403);

  // Where a new record would be created: the first target this user may add to.
  const createIn = f.targets.find((m) => me.perms[m]?.create);
  const createModule = schemaModules.find((m) => m.key === createIn);
  const exact = results.some((r) => r.data?.some((x) => x.title.toLowerCase() === typed.toLowerCase()));

  const toItem = (x: LookupItem): ComboboxItem => ({ value: `${x.module}:${x.id}`, label: x.title });
  const groups: ComboboxItemGroup[] = f.targets.map((m, i) => ({
    group: f.targets.length > 1 ? labels[m] ?? m : "",
    items: (results[i].data ?? []).map(toItem),
  }));
  const selectedKey = current ? `${current.module}:${current.id}` : null;
  if (current && !groups.some((g) => g.items.some((it) => (it as ComboboxItem).value === selectedKey))) {
    groups.unshift({ group: "", items: [{ value: selectedKey!, label: current.title }] });
  }
  if (createIn && typed && !exact && typed !== current?.title) {
    groups.push({ group: "", items: [{ value: CREATE, label: `+ Add “${typed}” as a new ${createModule?.singular.toLowerCase() ?? "record"}` }] });
  }
  const data = f.targets.length > 1 ? groups.filter((g) => g.items.length) : groups.flatMap((g) => g.items as ComboboxItem[]);
  const subtitles = new Map<string, string>();
  results.forEach((r) => r.data?.forEach((x) => subtitles.set(`${x.module}:${x.id}`, x.subtitle)));

  async function create() {
    if (!createIn || !createModule) return;
    setCreating(true);
    try {
      const rec = await api<RecordData>(`m/${createIn}/`, { body: { [createModule.title_field]: typed } });
      await qc.invalidateQueries({ queryKey: ["lookup", createIn] });
      await qc.invalidateQueries({ queryKey: ["records", createIn] });
      onChange({ module: createIn, id: rec.id, title: rec._title, archived: false });
      setSearch(rec._title);
      notifyOk(`${rec._title} added to ${createModule.label}.`);
    } catch (e) {
      notifyError(e, `Couldn't add ${typed}`);
    } finally {
      setCreating(false);
    }
  }

  function pick(key: string | null) {
    if (key === CREATE) {
      create();
      return;
    }
    if (!key) {
      onChange(null);
      setSearch("");
      return;
    }
    const [module, id] = key.split(":");
    const hit = results.flatMap((r) => r.data ?? []).find((x) => x.module === module && String(x.id) === id);
    if (hit) {
      onChange({ module: hit.module, id: hit.id, title: hit.title, archived: hit.archived });
      setSearch(hit.title);
    }
  }

  const noun = f.targets.map((m) => (labels[m] ?? m).toLowerCase()).join(" or ");
  const lookupError = results.find((r) => r.error && !(r.error instanceof ApiError && r.error.status === 403))?.error;
  const note = denied
    ? `Your role can't see ${noun}.`
    : current?.archived
      ? `${current.title} is in the Archive.`
      : f.help || undefined;
  return (
    <Select
      label={f.label}
      required={f.required}
      description={note}
      error={error || (lookupError ? errorMessage(lookupError) : undefined)}
      disabled={denied || creating}
      searchable
      clearable
      placeholder={`Search ${noun}`}
      data={data}
      value={selectedKey}
      searchValue={search}
      onSearchChange={setSearch}
      onChange={pick}
      filter={({ options }) => options}
      nothingFoundMessage={loading ? "Searching…" : typed ? `No ${noun} match “${typed}”` : `No ${noun} yet`}
      rightSection={loading || creating ? <Loader size={14} /> : undefined}
      renderOption={({ option }) => (
        <Group gap={6} wrap="nowrap">
          <div>
            <Text size="sm" fw={option.value === CREATE ? 600 : undefined} c={option.value === CREATE ? "navy" : undefined}>{option.label}</Text>
            {subtitles.get(option.value) && <Text size="xs" c="dimmed">{subtitles.get(option.value)}</Text>}
          </div>
        </Group>
      )}
    />
  );
}
