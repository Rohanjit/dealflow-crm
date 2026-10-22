// A record's interaction log: dated notes, calls, meetings and emails, plus automatic
// stage changes. Newest first.

import {
  ActionIcon, Anchor, Badge, Button, Group, Menu, Paper, SegmentedControl, Stack, Text, Textarea, ThemeIcon, Title,
} from "@mantine/core";
import { DateInput } from "@mantine/dates";
import { modals } from "@mantine/modals";
import {
  IconArrowsRightLeft, IconDots, IconExternalLink, IconMail, IconNote, IconPaperclip, IconPhone, IconUsersGroup,
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
import { STATUS_COLOR, STATUS_LABEL } from "../mail/api";
import LoggedEntry from "../sync/LoggedEntry";

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
  const { data, isLoading, error } = useQuery({ queryKey: key, queryFn: () => api<Note[]>(`m/${module.key}/${recordId}/notes/`),
    // An Outlook send can take a moment to reach Sent Items; look again until it's confirmed.
    refetchInterval: (q) => (q.state.data?.some((n) => n.meta?.status === "pending") ? 20_000 : false) });
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
          {(n.can_edit || n.can_delete) && !editing && (
            <Menu position="bottom-end">
              <Menu.Target><ActionIcon variant="subtle" color="gray" size="sm" aria-label="Entry actions"><IconDots size={14} /></ActionIcon></Menu.Target>
              <Menu.Dropdown>
                {n.can_edit && <Menu.Item onClick={() => setEditing(true)}>Edit</Menu.Item>}
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
        ) : n.meta?.synced ? (
          <LoggedEntry note={n} queryKey={queryKey} />
        ) : n.kind === "email" && n.meta?.email ? (
          <SentEmail note={n} />
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

/** An email sent from Dealflow: subject, who it went to, whether the mailbox confirmed it. */
function SentEmail({ note: n }: { note: Note }) {
  const [open, setOpen] = useState(false);
  const m = n.meta;
  const where = m.provider === "outlook" ? "Outlook" : "Gmail";
  const long = n.text.length > 280 || n.text.split("\n").length > 5;
  return (
    <Stack gap={4} mt={2}>
      <Text size="sm" fw={600}>{m.subject || "(no subject)"}</Text>
      <Text size="xs" c="dimmed">
        To {(m.to ?? []).join(", ")}{m.cc?.length ? ` · Cc ${m.cc.join(", ")}` : ""}{m.bcc ? ` · ${m.bcc} Bcc` : ""}
        {m.campaign_name ? ` · Campaign “${m.campaign_name}”` : ""}
      </Text>
      <Group gap={8}>
        <Badge size="sm" variant="light" color={STATUS_COLOR[m.status] ?? "gray"}>{STATUS_LABEL[m.status] ?? m.status}</Badge>
        {m.web_link && (
          <Anchor href={m.web_link} target="_blank" rel="noopener noreferrer" size="xs">
            <Group gap={2} component="span" wrap="nowrap">Open in {where} <IconExternalLink size={12} /></Group>
          </Anchor>
        )}
      </Group>
      {!!m.attachments?.length && (
        <Group gap={4}>
          <IconPaperclip size={12} color="var(--mantine-color-dimmed)" />
          <Text size="xs" c="dimmed">{m.attachments.join(", ")}</Text>
        </Group>
      )}
      <Text size="sm" className="df-prewrap" lineClamp={open ? undefined : 4}>{n.text}</Text>
      {long && (
        <Group><Button size="compact-xs" variant="subtle" onClick={() => setOpen((o) => !o)}>{open ? "Show less" : "Show the whole email"}</Button></Group>
      )}
    </Stack>
  );
}
