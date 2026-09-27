import { ActionIcon, Badge, Button, Group, NavLink, Paper, Stack, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { IconArrowDown, IconArrowUp, IconCheck, IconPencil, IconX } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { errorMessage, notifyError } from "../components/errors";
import type { ChoiceList, ChoiceOption } from "../types";

export default function DropdownSettings() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["choice-lists"], queryFn: () => api<{ lists: ChoiceList[] }>("choice-lists/") });
  const [key, setKey] = useState<string | null>(null);
  const [newLabel, setNewLabel] = useState("");
  const [addError, setAddError] = useState("");
  const lists = data?.lists ?? [];
  const list = lists.find((l) => l.key === (key ?? lists[0]?.key));

  const reload = () => {
    qc.invalidateQueries({ queryKey: ["choice-lists"] });
    qc.invalidateQueries({ queryKey: ["schema"] });
  };

  async function add() {
    if (!list || !newLabel.trim()) return;
    setAddError("");
    try {
      await api(`choice-lists/${list.key}/options/`, { body: { label: newLabel } });
      setNewLabel("");
      reload();
    } catch (e) {
      setAddError(errorMessage(e));
    }
  }

  async function patch(o: ChoiceOption, body: Partial<ChoiceOption>) {
    await api(`choice-options/${o.id}/`, { method: "PATCH", body });
    reload();
  }

  async function move(index: number, dir: -1 | 1) {
    if (!list) return;
    const ids = list.options.map((o) => o.id);
    const j = index + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[index], ids[j]] = [ids[j], ids[index]];
    try {
      await api(`choice-lists/${list.key}/reorder/`, { body: { ids } });
      reload();
    } catch (e) {
      notifyError(e);
    }
  }

  if (!list) return null;
  return (
    <Group align="flex-start" gap="lg" wrap="nowrap">
      <Paper withBorder p={6} w={220} style={{ flexShrink: 0 }}>
        {lists.map((l) => (
          <NavLink key={l.key} label={l.label} description={`${l.options.filter((o) => o.is_active).length} options`}
            active={l.key === list.key} onClick={() => { setKey(l.key); setNewLabel(""); setAddError(""); }} />
        ))}
      </Paper>
      <Paper withBorder p="lg" style={{ flex: 1, minWidth: 0 }}>
        <Stack>
          <div>
            <Title order={4}>{list.label}</Title>
            <Text size="sm" c="dimmed">Renaming an option updates every record that uses it. Removing hides it from new records; records that already have it keep it.</Text>
          </div>
          <Stack gap={4}>
            {list.options.map((o, i) => (
              <OptionRow key={o.id} o={o} first={i === 0} last={i === list.options.length - 1}
                onMove={(d) => move(i, d)} onPatch={(b) => patch(o, b)} />
            ))}
          </Stack>
          <Group align="flex-start" gap="xs">
            <TextInput placeholder="New option" value={newLabel} onChange={(e) => setNewLabel(e.currentTarget.value)} error={addError}
              onKeyDown={(e) => { if (e.key === "Enter") add(); }} w={320} />
            <Button onClick={add} disabled={!newLabel.trim()}>Add</Button>
          </Group>
        </Stack>
      </Paper>
    </Group>
  );
}

function OptionRow({
  o, first, last, onMove, onPatch,
}: { o: ChoiceOption; first: boolean; last: boolean; onMove: (d: -1 | 1) => void; onPatch: (b: Partial<ChoiceOption>) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(o.label);
  const [error, setError] = useState("");

  async function save() {
    setError("");
    try {
      await onPatch({ label });
      setEditing(false);
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  async function setActive(is_active: boolean) {
    try {
      await onPatch({ is_active });
    } catch (e) {
      notifyError(e);
    }
  }

  return (
    <Group justify="space-between" wrap="nowrap" px="xs" py={4} style={{ borderRadius: 6, background: o.is_active ? undefined : "var(--mantine-color-gray-0)" }}>
      {editing ? (
        <Group gap={4} style={{ flex: 1 }} wrap="nowrap" align="flex-start">
          <TextInput size="xs" value={label} onChange={(e) => setLabel(e.currentTarget.value)} error={error} autoFocus style={{ flex: 1, maxWidth: 320 }}
            onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") { setEditing(false); setLabel(o.label); } }} />
          <ActionIcon variant="light" color="teal" onClick={save} aria-label="Save"><IconCheck size={14} /></ActionIcon>
          <ActionIcon variant="subtle" color="gray" onClick={() => { setEditing(false); setLabel(o.label); setError(""); }} aria-label="Cancel"><IconX size={14} /></ActionIcon>
        </Group>
      ) : (
        <Group gap="xs">
          <Text size="sm" c={o.is_active ? undefined : "dimmed"} td={o.is_active ? undefined : "line-through"}>{o.label}</Text>
          {!o.is_active && <Badge size="xs" color="gray" variant="light">Removed</Badge>}
        </Group>
      )}
      <Group gap={2} wrap="nowrap">
        <Tooltip label="Move up"><ActionIcon variant="subtle" color="gray" disabled={first} onClick={() => onMove(-1)} aria-label="Move up"><IconArrowUp size={14} /></ActionIcon></Tooltip>
        <Tooltip label="Move down"><ActionIcon variant="subtle" color="gray" disabled={last} onClick={() => onMove(1)} aria-label="Move down"><IconArrowDown size={14} /></ActionIcon></Tooltip>
        {!editing && <Tooltip label="Rename"><ActionIcon variant="subtle" color="gray" onClick={() => setEditing(true)} aria-label="Rename"><IconPencil size={14} /></ActionIcon></Tooltip>}
        {o.is_active
          ? <Button size="compact-xs" variant="subtle" color="red" onClick={() => setActive(false)}>Remove</Button>
          : <Button size="compact-xs" variant="subtle" onClick={() => setActive(true)}>Restore</Button>}
      </Group>
    </Group>
  );
}
