// A logged email or meeting in a record's interaction log (decision 64): subject, who it was with,
// attachment names; the owner can open the text, save an attachment to the Vault and pick the deal.

import { Anchor, Badge, Button, Group, Modal, ScrollArea, Stack, Text } from "@mantine/core";
import { IconDeviceFloppy, IconLock, IconPaperclip } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import { useMe } from "../context";
import { formatDateTime } from "../format";
import type { Note } from "../types";
import { isVaultLock } from "../vault/api";
import type { Choice, Item, Participant } from "./api";

function people(list: Participant[], roles: string[]) {
  return list.filter((p) => roles.includes(p.role)).map((p) => p.name || p.email).join(", ");
}

export default function LoggedEntry({ note, queryKey }: { note: Note; queryKey: unknown[] }) {
  const m = note.meta;
  const [open, setOpen] = useState(false);
  const parts: Participant[] = m.participants ?? [];
  const email = note.kind === "email";
  const choices: Choice[] = (note as Note & { choices?: Choice[] }).choices ?? [];
  const canOpen = (note as Note & { can_open?: boolean }).can_open;

  if (m.restricted) {
    return (
      <Group gap={6} mt={2}>
        <IconLock size={14} color="var(--mantine-color-dimmed)" />
        <Text size="sm" c="dimmed" fs="italic">{email ? "An email" : "A meeting"} about a restricted deal</Text>
      </Group>
    );
  }
  return (
    <Stack gap={4} mt={2}>
      <Text size="sm" fw={600}>{m.subject || "(no subject)"}</Text>
      <Text size="xs" c="dimmed">
        {email
          ? <>From {people(parts, ["from"]) || "unknown"} to {people(parts, ["to"]) || "unknown"}{people(parts, ["cc"]) ? ` · Cc ${people(parts, ["cc"])}` : ""}</>
          : <>With {parts.filter((p) => p.role !== "organizer" || !p.colleague).map((p) => p.name || p.email).join(", ")}</>}
      </Text>
      {!!m.attachments?.length && (
        <Group gap={4} wrap="nowrap" align="flex-start">
          <IconPaperclip size={12} color="var(--mantine-color-dimmed)" style={{ marginTop: 3, flexShrink: 0 }} />
          <Text size="xs" c="dimmed">{m.attachments.join(", ")}</Text>
        </Group>
      )}
      {canOpen && (
        <Group gap={6}>
          <Button size="compact-xs" variant="light" onClick={() => setOpen(true)}>{email ? "Open email" : "Open meeting details"}</Button>
          <Text size="xs" c="dimmed">Only you can open this: it's in your {email ? "mailbox" : "calendar"}.</Text>
        </Group>
      )}
      {choices.length > 0 && <PickDeal itemId={m.synced} choices={choices} queryKey={queryKey} />}
      {open && <ItemModal id={m.synced} onClose={() => setOpen(false)} />}
    </Stack>
  );
}

export function PickDeal({ itemId, choices, queryKey, compact }: { itemId: number; choices: Choice[]; queryKey?: unknown[]; compact?: boolean }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  async function pick(ref: string | null) {
    setBusy(ref ?? "none");
    try {
      await api(`sync/items/${itemId}/file/`, { body: { ref } });
      if (queryKey) await qc.invalidateQueries({ queryKey });
      await qc.invalidateQueries({ queryKey: ["notes"] });
      await qc.invalidateQueries({ queryKey: ["sync-agenda"] });
      notifyOk(ref ? "Added to that deal's log." : "Kept off the deals.");
    } catch (e) {
      notifyError(e, "Couldn't file it");
    } finally {
      setBusy(null);
    }
  }
  return (
    <Stack gap={4} mt={compact ? 2 : 4}>
      {!compact && <Text size="xs" fw={500}>Which deal is this about? Only you're asked.</Text>}
      <Group gap={6}>
        {choices.map((c) => (
          <Button key={c.ref} size="compact-xs" variant="default" loading={busy === c.ref} onClick={() => pick(c.ref)}>
            {c.kind}: {c.title}
          </Button>
        ))}
        <Button size="compact-xs" variant="subtle" color="gray" loading={busy === "none"} onClick={() => pick(null)}>None of these</Button>
      </Group>
    </Stack>
  );
}

export function ItemModal({ id, onClose }: { id: number; onClose: () => void }) {
  const me = useMe();
  const [item, setItem] = useState<Item | null>(null);
  const [problem, setProblem] = useState("");
  const [saving, setSaving] = useState<number | null>(null);
  useEffect(() => {
    api<Item>(`sync/items/${id}/`).then(setItem).catch((e) => setProblem(errorMessage(e)));
  }, [id]);

  async function save(index: number) {
    setSaving(index);
    try {
      const doc = await api<{ id: number; _title: string }>(`sync/items/${id}/save/`, { body: { index } });
      notifyOk(`${doc._title} is in the Vault, tagged to this email's company and deal.`);
    } catch (e) {
      if (isVaultLock(e)) setProblem("Open the Vault with your PIN first, then save the attachment again.");
      else notifyError(e, "Couldn't save it");
    } finally {
      setSaving(null);
    }
  }

  const when = item ? (item.kind === "meeting" && item.end
    ? `${formatDateTime(item.at, me)} to ${formatDateTime(item.end, me).split(" ").slice(-2).join(" ")}`
    : formatDateTime(item.at, me)) : "";
  return (
    <Modal opened onClose={onClose} size="lg" title={item ? item.subject || "(no subject)" : "Opening…"}>
      {problem && (
        <Text c="red" size="sm" mb="sm">
          {problem}{problem.startsWith("Open the Vault") && <> <Anchor component={Link} to="/m/vault" size="sm" onClick={onClose}>Open the Vault</Anchor></>}
        </Text>
      )}
      {!item && !problem && <Text size="sm" c="dimmed">Fetching it…</Text>}
      {item && (
        <Stack gap="sm">
          <Text size="xs" c="dimmed">{when}{item.location ? ` · ${item.location}` : ""}</Text>
          <Group gap={4}>
            {item.participants.map((p) => (
              <Badge key={p.email} variant="light" color={p.contact ? "navy" : "gray"} tt="none">{p.name || p.email}</Badge>
            ))}
          </Group>
          <ScrollArea.Autosize mah={420}>
            <Text size="sm" className="df-prewrap">{(item.kind === "email" ? item.text : item.description) || "(nothing written)"}</Text>
          </ScrollArea.Autosize>
          {item.attachments.length > 0 && (
            <Stack gap={4}>
              <Text size="xs" fw={600}>Attachments</Text>
              {item.attachments.map((name, i) => (
                <Group key={i} gap="xs" justify="space-between" wrap="nowrap">
                  <Group gap={4} wrap="nowrap"><IconPaperclip size={14} /><Text size="sm" truncate>{name}</Text></Group>
                  <Button size="compact-xs" variant="light" leftSection={<IconDeviceFloppy size={12} />} loading={saving === i}
                    onClick={() => save(i)}>Save to Vault</Button>
                </Group>
              ))}
              <Text size="xs" c="dimmed">Saved files are checked for viruses, encrypted, and tagged to this email's company and deal.</Text>
            </Stack>
          )}
          <Text size="xs" c="dimmed">This was fetched from your {item.kind === "email" ? "mailbox" : "calendar"} just now. Dealflow doesn't keep a copy.</Text>
        </Stack>
      )}
    </Modal>
  );
}
