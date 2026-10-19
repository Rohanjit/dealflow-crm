// A record's interaction log: dated notes, calls, meetings and emails, plus automatic
// stage changes. Newest first.

import {
  ActionIcon, Button, Group, Menu, Paper, SegmentedControl, Stack, Text, Textarea, ThemeIcon, Title,
} from "@mantine/core";
import { DateInput } from "@mantine/dates";
import { modals } from "@mantine/modals";
import {
  IconArrowsRightLeft, IconDots, IconMail, IconNote, IconPhone, IconUsersGroup,
} from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useState } from "react";

import { api } from "../api";
import { useMe } from "../context";
import { errorMessage, notifyError } from "../components/errors";
import { formatDate } from "../format";
import type { ModuleSchema, Note } from "../types";
import { dateParser } from "./fields";
import { Skeleton } from "../components/ui";

const KINDS = [
  { value: "note", label: "Note", icon: IconNote, color: "gray" },
  { value: "call", label: "Call", icon: IconPhone, color: "teal" },
  { value: "meeting", label: "Meeting", icon: IconUsersGroup, color: "grape" },
  { value: "email", label: "Email", icon: IconMail, color: "blue" },
];
const STAGE = { icon: IconArrowsRightLeft, color: "navy" };

export default function Timeline({ module, recordId }: { module: ModuleSchema; recordId: number }) {
  const me = useMe();
  const qc = useQueryClient();
  const key = ["notes", module.key, recordId];
  const { data, isLoading, error } = useQuery({ queryKey: key, queryFn: () => api<Note[]>(`m/${module.key}/${recordId}/notes/`) });
  const [kind, setKind] = useState("note");
  const [date, setDate] = useState<Date | null>(new Date());
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  async function add() {
    setBusy(true);
    setProblem("");
    try {
      await api(`m/${module.key}/${recordId}/notes/`, {
        body: { kind, text, date: date ? dayjs(date).format("YYYY-MM-DD") : undefined },
      });
      setText("");
      setKind("note");
      setDate(new Date());
      await qc.invalidateQueries({ queryKey: key });
    } catch (e) {
      setProblem(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Paper withBorder radius="md" p="lg">
      <Title order={5} mb="sm">Interaction log</Title>
      {module.perms.edit && (
        <Stack gap={8} mb="lg">
          <SegmentedControl size="xs" data={KINDS.map((k) => ({ value: k.value, label: k.label }))} value={kind} onChange={setKind} />
          <Textarea placeholder={kind === "note" ? "Add a note" : `What was discussed in the ${kind}?`} autosize minRows={2} maxRows={10}
            value={text} onChange={(e) => setText(e.currentTarget.value)} error={problem || undefined} />
          <Group justify="space-between" gap="xs">
            <DateInput size="xs" w={150} valueFormat={me.date_format} value={date} onChange={setDate} maxDate={new Date()}
              dateParser={dateParser(me.date_format)} aria-label="Date" />
            <Button size="xs" onClick={add} loading={busy} disabled={!text.trim()}>Add to log</Button>
          </Group>
        </Stack>
      )}
      {isLoading ? <Stack gap={8}><Skeleton h={34} /><Skeleton h={34} w="80%" /></Stack> : error ? <Text c="red" size="sm">{errorMessage(error)}</Text> : (
        <Stack gap="md">
          {data!.length === 0 && <Text size="sm" c="dimmed">Nothing logged yet.</Text>}
          {data!.map((n) => <Entry key={n.id} note={n} queryKey={key} />)}
        </Stack>
      )}
    </Paper>
  );
}

function Entry({ note: n, queryKey }: { note: Note; queryKey: unknown[] }) {
  const me = useMe();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(n.text);
  const kind = KINDS.find((k) => k.value === n.kind);
  const Icon = n.kind === "stage" ? STAGE.icon : kind?.icon ?? IconNote;
  const color = n.kind === "stage" ? STAGE.color : kind?.color ?? "gray";

  async function save() {
    try {
      await api(`notes/${n.id}/`, { method: "PATCH", body: { text } });
      setEditing(false);
      await qc.invalidateQueries({ queryKey });
    } catch (e) {
      notifyError(e);
    }
  }

  function remove() {
    modals.openConfirmModal({
      title: "Delete this entry?",
      children: <Text size="sm">It's removed from the log for everyone.</Text>,
      labels: { confirm: "Delete", cancel: "Cancel" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await api(`notes/${n.id}/`, { method: "DELETE" });
          await qc.invalidateQueries({ queryKey });
        } catch (e) {
          notifyError(e, "Couldn't delete");
        }
      },
    });
  }

  return (
    <Group align="flex-start" gap="sm" wrap="nowrap">
      <ThemeIcon variant="light" color={color} radius="xl" size="md"><Icon size={15} /></ThemeIcon>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Group justify="space-between" gap={4} wrap="nowrap">
          <Text size="xs" c="dimmed">
            {n.kind === "stage" ? "Stage" : kind?.label} · {formatDate(n.date, me)}{n.created_by ? ` · ${n.created_by.name}` : ""}
          </Text>
          {n.can_edit && !editing && (
            <Menu position="bottom-end">
              <Menu.Target><ActionIcon variant="subtle" color="gray" size="sm" aria-label="Entry actions"><IconDots size={14} /></ActionIcon></Menu.Target>
              <Menu.Dropdown>
                <Menu.Item onClick={() => setEditing(true)}>Edit</Menu.Item>
                <Menu.Item color="red" onClick={remove}>Delete</Menu.Item>
              </Menu.Dropdown>
            </Menu>
          )}
        </Group>
        {editing ? (
          <Stack gap={6} mt={4}>
            <Textarea autosize minRows={2} value={text} onChange={(e) => setText(e.currentTarget.value)} />
            <Group gap="xs">
              <Button size="xs" onClick={save} disabled={!text.trim()}>Save</Button>
              <Button size="xs" variant="subtle" onClick={() => { setEditing(false); setText(n.text); }}>Cancel</Button>
            </Group>
          </Stack>
        ) : (
          <Text size="sm" className="df-prewrap" fw={n.kind === "stage" ? 500 : undefined}>
            {n.text}
            {n.meta?.date ? <Text span size="sm" c="dimmed">{` · ${n.meta.date_label ?? "Date"} ${formatDate(n.meta.date, me)}`}</Text> : null}
          </Text>
        )}
      </div>
    </Group>
  );
}
