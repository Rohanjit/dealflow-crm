// Settings > Fields & layout (decision 57). Each module shows its original layout. "Customise"
// makes it editable: move, rename and delete (hide) fields, add sections and custom fields of every
// major type, and show custom fields of linked records. The primary field stays put. Saving asks for
// the person's password. Switching "Customise" off brings back the original layout; custom fields
// and their values are kept, hidden.

import {
  ActionIcon, Alert, Badge, Button, Checkbox, Divider, Group, Modal, MultiSelect, Paper, PasswordInput, ScrollArea,
  SegmentedControl, Select, Stack, Switch, TagsInput, Text, TextInput, Title, Tooltip,
} from "@mantine/core";
import { DragDropContext, Draggable, Droppable, type DropResult } from "@hello-pangea/dnd";
import {
  IconArrowBackUp, IconArrowDown, IconArrowUp, IconGripVertical, IconLink, IconLock, IconPencil, IconPlus,
} from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../api";
import { errorMessage, notifyOk } from "../components/errors";
import { fieldIcon } from "../components/ui";
import type { FieldSchema } from "../types";

type Section = { key: string; label: string };
type Item = { field: string; section: string };
type Row = {
  key: string; label: string; original_label: string; type: string; type_label: string; section: string;
  primary: boolean; can_hide: boolean; hidden: boolean; built_in: boolean; from_module: string | null;
  multi?: boolean; targets?: string[]; required?: boolean; help?: string; options?: string[]; editable_here?: boolean;
  source_customised?: boolean;
};
type Layout = {
  module: string; label: string; kind: "custom" | "switches"; customised: boolean; primary: string;
  original: { sections: Section[]; items: Item[] };
  saved: { sections: Section[]; items: Item[]; labels: Record<string, string>; hidden: string[] };
  fields: Row[]; custom: Row[]; deleted: Row[]; linked: Row[];
};
type Payload = { modules: Layout[]; types: { value: string; label: string }[]; link_targets: { value: string; label: string }[] };
type NewField = { id: string; label: string; type: string; multi: boolean; targets: string[]; required: boolean; help: string; options: string[] };
type Draft = {
  sections: Section[]; items: Item[]; labels: Record<string, string>; hidden: string[];
  newFields: NewField[]; deleted: string[]; restored: string[]; updates: Record<string, { label?: string; required?: boolean; help?: string; options?: string[] }>;
};

const MULTI_TYPES = ["dropdown", "user", "link"];
let counter = 0;
const newKey = (p: string) => `${p}${Date.now().toString(36)}${counter++}`;

export default function LayoutSettings() {
  const { data, error } = useQuery({ queryKey: ["layouts"], queryFn: () => api<Payload>("settings/layouts/") });
  const [module, setModule] = useState<string | null>(null);
  if (error) return <Paper withBorder p="lg"><Text c="red">{errorMessage(error)}</Text></Paper>;
  if (!data) return null;
  const current = data.modules.find((m) => m.module === (module ?? data.modules[0]?.module)) ?? data.modules[0];
  return (
    <Paper withBorder p="lg">
      <Stack>
        <div>
          <Title order={4}>Fields & layout</Title>
          <Text size="sm" c="dimmed">
            The fields each module shows on its forms, record pages, lists, filters, imports and exports, and the order they come in.
          </Text>
        </div>
        <ScrollArea type="auto" offsetScrollbars scrollbarSize={6}>
          <SegmentedControl w="fit-content" value={current.module} onChange={setModule}
            data={data.modules.map((m) => ({ value: m.module, label: m.label }))} />
        </ScrollArea>
        <ModuleEditor key={`${current.module}-${current.customised}`} layout={current} types={data.types} targets={data.link_targets} />
      </Stack>
    </Paper>
  );
}

function draftFrom(l: Layout): Draft {
  // Fields that came after this layout was saved (a newer Dealflow, a new custom field) go to their
  // original section, or the last one.
  const items = [...l.saved.items];
  const keys = new Set(items.map((it) => it.field));
  const sectionKeys = new Set(l.saved.sections.map((s) => s.key));
  const last = l.saved.sections[l.saved.sections.length - 1]?.key ?? "";
  for (const f of [...l.fields, ...l.custom]) {
    if (!keys.has(f.key)) items.push({ field: f.key, section: sectionKeys.has(f.section) ? f.section : last });
  }
  return { sections: l.saved.sections, items, labels: { ...l.saved.labels }, hidden: [...l.saved.hidden],
    newFields: [], deleted: [], restored: [], updates: {} };
}

const idOf = (x: Row | NewField) => ("id" in x ? x.id : x.key);

function ModuleEditor({ layout: l, types, targets }: { layout: Layout; types: Payload["types"]; targets: Payload["link_targets"] }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(l.customised);
  const [draft, setDraft] = useState<Draft>(() => draftFrom(l));
  const [dirty, setDirty] = useState(false);
  const [ask, setAsk] = useState<null | "save" | "off">(null);
  const [adding, setAdding] = useState<string | null>(null); // section key
  const [editingField, setEditingField] = useState<Row | NewField | null>(null);
  const [renaming, setRenaming] = useState<{ key: string; label: string; kind: "field" | "section" } | null>(null);
  const change = (fn: (d: Draft) => Draft) => { setDraft(fn); setDirty(true); };

  // Every field that could appear, by key.
  const rows = useMemo(() => {
    const out: Record<string, Row> = {};
    for (const f of l.fields) out[f.key] = f;
    for (const f of [...l.custom, ...l.deleted]) out[f.key] = f;
    for (const f of l.linked) out[f.key] = { ...f, from_module: f.from_module };
    for (const nf of draft.newFields) {
      out[nf.id] = { key: nf.id, label: nf.label, original_label: nf.label, type: nf.type, type_label: types.find((t) => t.value === nf.type)?.label ?? nf.type,
        section: "", primary: false, can_hide: true, hidden: false, built_in: false, from_module: null, required: nf.required, multi: nf.multi };
    }
    return out;
  }, [l, draft.newFields, types]);
  const labelOf = (key: string) => {
    if (!editing) return rows[key]?.original_label ?? key; // the original layout shows the original names
    const nf = draft.newFields.find((n) => n.id === key);
    if (nf) return nf.label;
    return draft.updates[key]?.label ?? draft.labels[key] ?? rows[key]?.label ?? key;
  };
  const deletedCustom = new Set([...l.deleted.map((d) => d.key).filter((k) => !draft.restored.includes(k)), ...draft.deleted]);
  const placed = draft.items.filter((it) => rows[it.field] && !draft.hidden.includes(it.field) && !deletedCustom.has(it.field));
  const allNames = () => new Set(placed.map((it) => labelOf(it.field).toLowerCase()));

  function onDragEnd(r: DropResult) {
    if (!r.destination) return;
    move(r.draggableId, r.destination.droppableId, r.destination.index);
  }
  function move(field: string, section: string, index: number) {
    change((d) => {
      const without = d.items.filter((it) => it.field !== field);
      const inSection = without.filter((it) => it.section === section && rows[it.field] && !d.hidden.includes(it.field) && !deletedCustom.has(it.field));
      const before = inSection[index];
      const at = before ? without.indexOf(before) : (inSection.length ? without.indexOf(inSection[inSection.length - 1]) + 1 : without.length);
      const items = [...without];
      items.splice(at, 0, { field, section });
      return { ...d, items };
    });
  }
  function nudge(field: string, dir: -1 | 1) {
    const it = placed.find((x) => x.field === field)!;
    const inSection = placed.filter((x) => x.section === it.section);
    const i = inSection.indexOf(it);
    const secIdx = draft.sections.findIndex((s) => s.key === it.section);
    if (i + dir < 0 || i + dir >= inSection.length) {
      // Off the end of a section: into the next or previous one.
      const next = draft.sections[secIdx + dir];
      if (!next) return;
      const target = placed.filter((x) => x.section === next.key);
      move(field, next.key, dir < 0 ? target.length : 0);
      return;
    }
    move(field, it.section, i + dir);
  }
  function hide(row: Row) {
    if (row.built_in) change((d) => ({ ...d, hidden: [...d.hidden, row.key] }));
    else if (draft.newFields.some((n) => n.id === row.key)) change((d) => ({ ...d, newFields: d.newFields.filter((n) => n.id !== row.key), items: d.items.filter((it) => it.field !== row.key) }));
    else if (l.linked.some((x) => x.key === row.key)) change((d) => ({ ...d, items: d.items.filter((it) => it.field !== row.key) }));
    else change((d) => ({ ...d, deleted: [...d.deleted, row.key], restored: d.restored.filter((k) => k !== row.key) }));
  }
  function bringBack(key: string) {
    const section = draft.sections[draft.sections.length - 1].key;
    change((d) => ({
      ...d, hidden: d.hidden.filter((k) => k !== key), deleted: d.deleted.filter((k) => k !== key),
      restored: l.deleted.some((x) => x.key === key) ? [...d.restored, key] : d.restored,
      items: d.items.some((it) => it.field === key) ? d.items : [...d.items, { field: key, section }],
    }));
  }
  function addSection() {
    const key = newKey("s_");
    change((d) => ({ ...d, sections: [...d.sections, { key, label: uniqueSection(d.sections, "New section") }] }));
  }
  function showLinked(row: Row) {
    change((d) => ({ ...d, items: [...d.items.filter((it) => it.field !== row.key), { field: row.key, section: d.sections[d.sections.length - 1].key }] }));
  }

  async function save(password: string, customised: boolean) {
    const body = customised ? {
      password, customised: true, sections: draft.sections, items: draft.items.filter((it) => rows[it.field]), labels: draft.labels,
      hidden: draft.hidden, new_fields: draft.newFields, deleted_fields: draft.deleted, restored_fields: draft.restored, field_updates: draft.updates,
    } : { password, customised: false };
    await api(`settings/layouts/${l.module}/`, { method: "PUT", body });
    await qc.invalidateQueries({ queryKey: ["layouts"] });
    await qc.invalidateQueries({ queryKey: ["schema"] });
    await qc.invalidateQueries({ queryKey: ["choice-lists"] });
    notifyOk(customised ? `${l.label}: layout saved.` : `${l.label}: back to the original layout. Custom fields are kept, hidden.`);
  }

  if (l.kind === "switches") return <Switches l={l} />;

  const sectionsShown = editing ? draft.sections : l.original.sections;
  const itemsShown = editing ? placed : l.original.items;
  const hiddenRows = [...draft.hidden.map((k) => rows[k]).filter(Boolean), ...[...deletedCustom].map((k) => rows[k]).filter(Boolean)];
  const linkedAvailable = l.linked.filter((x) => !draft.items.some((it) => it.field === x.key));

  return (
    <Stack>
      <Paper withBorder p="md" bg="gray.0">
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <div>
            <Switch size="md" checked={editing} label={<Text fw={600}>Customise {l.label} fields</Text>}
              onChange={(e) => {
                if (e.currentTarget.checked) { setEditing(true); setDraft(draftFrom(l)); setDirty(!l.customised); }
                else if (l.customised) setAsk("off");
                else { setEditing(false); setDraft(draftFrom(l)); setDirty(false); }
              }} />
            <Text size="sm" c="dimmed" mt={4} maw={640}>
              {editing
                ? "Add your own fields, rename, move or delete fields, and add sections. Changes apply to everyone once you save. Switch off to go back to the original layout; your custom fields and what's typed in them are kept, hidden."
                : "This is the original layout. Switch on to add your own fields or change the layout."}
            </Text>
          </div>
          {editing && (
            <Group gap="xs" wrap="nowrap">
              {dirty && <Button variant="default" onClick={() => { setDraft(draftFrom(l)); setDirty(false); if (!l.customised) setEditing(false); }}>Discard changes</Button>}
              <Button onClick={() => setAsk("save")} disabled={!dirty}>Save changes</Button>
            </Group>
          )}
        </Group>
      </Paper>

      {editing && (
        <Text size="xs" c="dimmed">
          Drag a field by its handle, or use the arrows, to move it. <IconLock size={12} style={{ verticalAlign: -1 }} /> {labelOf(l.primary)} is
          the field that tells records apart, so it always comes first and can't be changed.
        </Text>
      )}

      <DragDropContext onDragEnd={onDragEnd}>
        <Stack gap="md">
          {sectionsShown.map((s, si) => {
            const fields = itemsShown.filter((it) => it.section === s.key);
            return (
              <Paper key={s.key} withBorder radius="md" p="md">
                <Group justify="space-between" mb="xs">
                  <Text fw={600}>{s.label}</Text>
                  {editing && (
                    <Group gap={4}>
                      <Button size="compact-xs" variant="subtle" leftSection={<IconPencil size={12} />} onClick={() => setRenaming({ key: s.key, label: s.label, kind: "section" })}>Rename section</Button>
                      {fields.length === 0 && draft.sections.length > 1 && (
                        <Button size="compact-xs" variant="subtle" color="red" onClick={() => change((d) => ({ ...d, sections: d.sections.filter((x) => x.key !== s.key) }))}>Delete section</Button>
                      )}
                      {si > 0 && <Tooltip label="Move section up"><ActionIcon variant="subtle" color="gray" aria-label="Move section up" onClick={() => change((d) => ({ ...d, sections: swap(d.sections, si, si - 1) }))}><IconArrowUp size={14} /></ActionIcon></Tooltip>}
                    </Group>
                  )}
                </Group>
                <Droppable droppableId={s.key} isDropDisabled={!editing}>
                  {(dp) => (
                    <Stack gap={4} ref={dp.innerRef} {...dp.droppableProps} mih={editing ? 36 : undefined}>
                      {fields.map((it, i) => {
                        const row = rows[it.field];
                        if (!row) return null;
                        const locked = row.primary || it.field === l.primary;
                        return (
                          <Draggable key={it.field} draggableId={it.field} index={i} isDragDisabled={!editing || locked}>
                            {(p, snap) => (
                              <Group ref={p.innerRef} {...p.draggableProps} justify="space-between" wrap="nowrap" px="xs" py={6}
                                className="df-layout-row" data-dragging={snap.isDragging ? "" : undefined}
                                style={{ ...p.draggableProps.style, borderRadius: 6, background: snap.isDragging ? "var(--mantine-color-body)" : undefined }}>
                                <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}>
                                  {editing && (locked ? <IconLock size={16} color="var(--mantine-color-gray-6)" />
                                    : <span {...p.dragHandleProps} aria-label={`Drag ${labelOf(it.field)}`} style={{ display: "flex", cursor: "grab" }}><IconGripVertical size={16} color="var(--mantine-color-gray-6)" /></span>)}
                                  <FieldTypeIcon type={row.type} />
                                  <Text size="sm" truncate>{labelOf(it.field)}</Text>
                                  {!row.built_in && !row.from_module && <Badge size="xs" variant="light" color="violet" tt="none">Custom</Badge>}
                                  {row.from_module && <Badge size="xs" variant="light" color="gray" tt="none" leftSection={<IconLink size={10} />}>From {row.from_module}{row.editable_here === false ? " · read-only" : ""}</Badge>}
                                  {(row.required || draft.updates[it.field]?.required) && <Badge size="xs" variant="light" color="orange" tt="none">Required</Badge>}
                                  {editing && draft.labels[it.field] && draft.labels[it.field] !== row.original_label && <Text size="xs" c="dimmed" truncate>was “{row.original_label}”</Text>}
                                </Group>
                                <Group gap={2} wrap="nowrap">
                                  <Text size="xs" c="dimmed" visibleFrom="sm" mr={4}>{row.type_label}{row.multi ? " (several)" : ""}</Text>
                                  {editing && !locked && (
                                    <>
                                      <Tooltip label="Move up"><ActionIcon variant="subtle" color="gray" aria-label={`Move ${labelOf(it.field)} up`} onClick={() => nudge(it.field, -1)}><IconArrowUp size={14} /></ActionIcon></Tooltip>
                                      <Tooltip label="Move down"><ActionIcon variant="subtle" color="gray" aria-label={`Move ${labelOf(it.field)} down`} onClick={() => nudge(it.field, 1)}><IconArrowDown size={14} /></ActionIcon></Tooltip>
                                      {!row.from_module && (
                                        <Button size="compact-xs" variant="subtle" onClick={() => {
                                          if (row.built_in) setRenaming({ key: row.key, label: labelOf(row.key), kind: "field" });
                                          else setEditingField(draft.newFields.find((n) => n.id === row.key) ?? row);
                                        }}>{row.built_in ? "Rename" : "Edit"}</Button>
                                      )}
                                      {(row.can_hide || row.from_module) && (
                                        <Button size="compact-xs" variant="subtle" color="red" onClick={() => hide(row)}>{row.from_module ? "Remove" : "Delete"}</Button>
                                      )}
                                    </>
                                  )}
                                  {editing && !locked && !row.can_hide && !row.from_module && (
                                    <Tooltip label="Other screens need this field, so it can't be deleted"><Badge size="xs" variant="light" color="gray">Always on</Badge></Tooltip>
                                  )}
                                </Group>
                              </Group>
                            )}
                          </Draggable>
                        );
                      })}
                      {dp.placeholder}
                      {editing && fields.length === 0 && <Text size="xs" c="dimmed" px="xs">Drag fields here, or add one.</Text>}
                    </Stack>
                  )}
                </Droppable>
                {editing && (
                  <Button mt="xs" size="xs" variant="light" leftSection={<IconPlus size={14} />} onClick={() => setAdding(s.key)}>Add a field to {s.label}</Button>
                )}
              </Paper>
            );
          })}
        </Stack>
      </DragDropContext>

      {editing && (
        <Group>
          <Button variant="default" leftSection={<IconPlus size={16} />} onClick={addSection}>Add a section</Button>
        </Group>
      )}

      {editing && l.linked.length > 0 && (
        <Paper withBorder radius="md" p="md">
          <Text fw={600}>Fields from linked records</Text>
          <Text size="sm" c="dimmed" mb="sm">Custom fields of the records these {l.label.toLowerCase()} link to. Show any of them here too.</Text>
          {linkedAvailable.length === 0 ? <Text size="sm" c="dimmed">All of them are shown above.</Text> : (
            <Stack gap={6}>
              {linkedAvailable.map((x) => (
                <Group key={x.key} justify="space-between" wrap="nowrap">
                  <Group gap={8} wrap="nowrap"><FieldTypeIcon type={x.type} /><Text size="sm">{x.label}</Text><Badge size="xs" variant="light" color="gray" tt="none">From {x.from_module}</Badge></Group>
                  <Button size="compact-xs" variant="light" onClick={() => showLinked(x)}>Show here</Button>
                </Group>
              ))}
            </Stack>
          )}
        </Paper>
      )}

      {editing && hiddenRows.length > 0 && (
        <Paper withBorder radius="md" p="md">
          <Text fw={600}>Deleted fields</Text>
          <Text size="sm" c="dimmed" mb="sm">Hidden from everyone. What was typed in them is kept; bring a field back to see it again.</Text>
          <Stack gap={6}>
            {hiddenRows.map((r) => (
              <Group key={r.key} justify="space-between" wrap="nowrap">
                <Group gap={8} wrap="nowrap"><FieldTypeIcon type={r.type} /><Text size="sm" c="dimmed">{labelOf(r.key)}</Text>{!r.built_in && <Badge size="xs" variant="light" color="violet" tt="none">Custom</Badge>}</Group>
                <Button size="compact-xs" variant="light" leftSection={<IconArrowBackUp size={12} />} onClick={() => bringBack(r.key)}>Bring back</Button>
              </Group>
            ))}
          </Stack>
        </Paper>
      )}

      {adding && (
        <FieldModal title="Add a field" types={types} targets={targets} sections={draft.sections} section={adding}
          taken={allNames()} onClose={() => setAdding(null)}
          onSave={(nf, section) => {
            change((d) => ({ ...d, newFields: [...d.newFields, nf], items: [...d.items, { field: nf.id, section }] }));
            setAdding(null);
          }} />
      )}
      {editingField && (
        <FieldModal title="Edit field" types={types} targets={targets} sections={draft.sections} existing={editingField}
          taken={new Set([...allNames()].filter((n) => n !== labelOf(idOf(editingField)).toLowerCase()))}
          onClose={() => setEditingField(null)}
          onSave={(nf) => {
            const id = idOf(editingField);
            if (draft.newFields.some((n) => n.id === id)) change((d) => ({ ...d, newFields: d.newFields.map((n) => n.id === id ? { ...nf, id } : n) }));
            else change((d) => ({ ...d, updates: { ...d.updates, [id]: { label: nf.label, required: nf.required, help: nf.help, options: nf.options } } }));
            setEditingField(null);
          }} />
      )}
      {renaming && (
        <RenameModal value={renaming} taken={renaming.kind === "section" ? new Set(draft.sections.filter((s) => s.key !== renaming.key).map((s) => s.label.toLowerCase())) : new Set([...allNames()].filter((n) => n !== renaming.label.toLowerCase()))}
          onClose={() => setRenaming(null)}
          onSave={(label) => {
            if (renaming.kind === "section") change((d) => ({ ...d, sections: d.sections.map((s) => s.key === renaming.key ? { ...s, label } : s) }));
            else change((d) => ({ ...d, labels: { ...d.labels, [renaming.key]: label } }));
            setRenaming(null);
          }} />
      )}
      {ask && (
        <PasswordModal
          title={ask === "save" ? `Save the ${l.label} layout?` : `Go back to the original ${l.label} layout?`}
          text={ask === "save" ? "Everyone will see the new layout straight away." : "Custom fields and what's typed in them are kept, hidden. Your custom layout comes back if you switch on again."}
          confirm={ask === "save" ? "Save" : "Go back to original"}
          onClose={() => setAsk(null)}
          onConfirm={async (pw) => { await save(pw, ask === "save"); setAsk(null); setDirty(false); if (ask === "off") setEditing(false); }} />
      )}
    </Stack>
  );
}

function swap<T>(xs: T[], i: number, j: number): T[] {
  const out = [...xs];
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

function uniqueSection(sections: Section[], base: string) {
  const taken = new Set(sections.map((s) => s.label.toLowerCase()));
  let n = 1, label = base;
  while (taken.has(label.toLowerCase())) label = `${base} ${++n}`;
  return label;
}

function FieldTypeIcon({ type }: { type: string }) {
  const Icon = fieldIcon({ type, multi: false } as FieldSchema);
  return <Icon size={15} color="var(--mantine-color-gray-6)" style={{ flexShrink: 0 }} />;
}

function FieldModal({
  title, types, targets, sections, section, existing, taken, onClose, onSave,
}: {
  title: string; types: Payload["types"]; targets: Payload["link_targets"]; sections: Section[]; section?: string;
  existing?: Row | NewField; taken: Set<string>; onClose: () => void; onSave: (nf: NewField, section: string) => void;
}) {
  const isNew = !existing || "id" in existing;
  const [label, setLabel] = useState(existing?.label ?? "");
  const [type, setType] = useState<string | null>(existing?.type ?? null);
  const [multi, setMulti] = useState(!!existing?.multi);
  const [linkTo, setLinkTo] = useState<string[]>(existing?.targets ?? []);
  const [required, setRequired] = useState(!!existing?.required);
  const [help, setHelp] = useState(existing?.help ?? "");
  const [options, setOptions] = useState<string[]>(existing && "id" in existing ? existing.options : []);
  const [where, setWhere] = useState<string | null>(section ?? sections[0]?.key ?? null);
  const [err, setErr] = useState("");
  const typeLocked = existing && !("id" in existing);
  const existingOptions = existing && !("id" in existing) ? existing.options ?? [] : [];

  function submit() {
    const name = label.trim().replace(/\s+/g, " ");
    if (!name) return setErr("Give the field a name.");
    if (taken.has(name.toLowerCase())) return setErr("Another field in this module already has that name.");
    if (!type) return setErr("Pick a type.");
    if (type === "link" && !linkTo.length) return setErr("Pick what it links to.");
    onSave({ id: existing && "id" in existing ? existing.id : `new:${newKey("")}`, label: name, type, multi: MULTI_TYPES.includes(type) && multi,
      targets: type === "link" ? linkTo : [], required, help: help.trim(), options }, where!);
  }
  return (
    <Modal opened onClose={onClose} title={<Text fw={600}>{title}</Text>}>
      <Stack>
        <TextInput label="Name" placeholder="e.g. Fund strategy" value={label} onChange={(e) => { setLabel(e.currentTarget.value); setErr(""); }} data-autofocus />
        <Select label="Type" placeholder="Pick a type" data={types} value={type} onChange={setType} disabled={!!typeLocked} searchable
          description={typeLocked ? "A field's type can't change once it holds data." : undefined} />
        {type === "dropdown" && (
          <TagsInput label={existingOptions.length ? "Add options" : "Options"} placeholder="Type an option and press Enter" value={options} onChange={setOptions}
            description={existingOptions.length ? `Already has: ${existingOptions.join(", ")}. You can also edit options in Settings > Dropdown lists.` : "You can change them later in Settings > Dropdown lists."} />
        )}
        {type === "link" && (
          <MultiSelect label="Links to" placeholder="Pick one or more" data={targets} value={linkTo} onChange={setLinkTo} disabled={!!typeLocked} />
        )}
        {type && MULTI_TYPES.includes(type) && !typeLocked && (
          <Checkbox label={type === "user" ? "Several people" : type === "link" ? "Several records" : "Several options can be picked"} checked={multi} onChange={(e) => setMulti(e.currentTarget.checked)} />
        )}
        <Checkbox label="Required" description="New records can't be saved without it. Existing records are asked the next time someone edits them."
          checked={required} onChange={(e) => setRequired(e.currentTarget.checked)} />
        <TextInput label="Help text (optional)" placeholder="Shown under the field on forms" value={help} onChange={(e) => setHelp(e.currentTarget.value)} />
        {isNew && !existing && <Select label="Section" data={sections.map((s) => ({ value: s.key, label: s.label }))} value={where} onChange={setWhere} allowDeselect={false} />}
        {err && <Alert color="red" variant="light">{err}</Alert>}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button onClick={submit}>{existing ? "Done" : "Add field"}</Button>
        </Group>
        <Text size="xs" c="dimmed">Nothing is saved until you click Save changes.</Text>
      </Stack>
    </Modal>
  );
}

function RenameModal({ value, taken, onClose, onSave }: {
  value: { key: string; label: string; kind: "field" | "section" }; taken: Set<string>; onClose: () => void; onSave: (label: string) => void;
}) {
  const [label, setLabel] = useState(value.label);
  const [err, setErr] = useState("");
  function submit() {
    const name = label.trim().replace(/\s+/g, " ");
    if (!name) return setErr("Enter a name.");
    if (taken.has(name.toLowerCase())) return setErr(`Another ${value.kind} already has that name.`);
    onSave(name);
  }
  return (
    <Modal opened onClose={onClose} title={<Text fw={600}>Rename {value.kind === "section" ? "section" : "field"}</Text>}>
      <Stack>
        <TextInput label="Name" value={label} onChange={(e) => { setLabel(e.currentTarget.value); setErr(""); }} error={err} data-autofocus
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }} />
        <Group justify="flex-end"><Button variant="default" onClick={onClose}>Cancel</Button><Button onClick={submit}>Rename</Button></Group>
      </Stack>
    </Modal>
  );
}

function PasswordModal({ title, text, confirm, onClose, onConfirm }: {
  title: string; text: string; confirm: string; onClose: () => void; onConfirm: (pw: string) => Promise<void>;
}) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true); setErr("");
    try { await onConfirm(pw); } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  }
  return (
    <Modal opened onClose={onClose} title={<Text fw={600}>{title}</Text>}>
      <Stack>
        <Text size="sm">{text}</Text>
        <PasswordInput label="Your password" description="To confirm it's you." value={pw} onChange={(e) => setPw(e.currentTarget.value)} error={err} data-autofocus
          onKeyDown={(e) => { if (e.key === "Enter" && pw) go(); }} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button onClick={go} loading={busy} disabled={!pw}>{confirm}</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

/** Tasks & Reminders and the Vault: just switch fields on or off. */
function Switches({ l }: { l: Layout }) {
  const qc = useQueryClient();
  const [hidden, setHidden] = useState<string[]>(l.saved.hidden);
  const [ask, setAsk] = useState(false);
  const dirty = hidden.join() !== l.saved.hidden.join();
  return (
    <Stack>
      <Text size="sm" c="dimmed">Custom fields aren't available for {l.label}. You can switch its fields off: they disappear for everyone, and anything typed in them is kept.</Text>
      {l.original.sections.map((s) => (
        <Paper key={s.key} withBorder radius="md" p="md">
          <Text fw={600} mb="xs">{s.label}</Text>
          <Stack gap={6}>
            {l.fields.filter((f) => f.section === s.key).map((f) => (
              <Group key={f.key} justify="space-between" wrap="nowrap">
                <Group gap={8} wrap="nowrap"><FieldTypeIcon type={f.type} /><Text size="sm" c={hidden.includes(f.key) ? "dimmed" : undefined}>{f.label}</Text></Group>
                {f.can_hide
                  ? <Switch checked={!hidden.includes(f.key)} aria-label={f.label} onChange={(e) => setHidden((h) => e.currentTarget.checked ? h.filter((k) => k !== f.key) : [...h, f.key])} />
                  : <Badge size="xs" variant="light" color="gray">Always on</Badge>}
              </Group>
            ))}
          </Stack>
        </Paper>
      ))}
      <Group><Button disabled={!dirty} onClick={() => setAsk(true)}>Save changes</Button>{dirty && <Button variant="default" onClick={() => setHidden(l.saved.hidden)}>Discard changes</Button>}</Group>
      {ask && (
        <PasswordModal title={`Save ${l.label} fields?`} text="Everyone will see the change straight away." confirm="Save" onClose={() => setAsk(false)}
          onConfirm={async (pw) => {
            await api(`settings/layouts/${l.module}/`, { method: "PUT", body: { password: pw, hidden } });
            await qc.invalidateQueries({ queryKey: ["layouts"] });
            await qc.invalidateQueries({ queryKey: ["schema"] });
            notifyOk(`${l.label}: fields saved.`);
            setAsk(false);
          }} />
      )}
      <Divider />
    </Stack>
  );
}
