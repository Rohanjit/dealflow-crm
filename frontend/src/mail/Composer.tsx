// Write and send one email from a record page, through the person's own mailbox. It's logged on
// the record and on each contact it goes to once the mailbox confirms it's in the Sent folder.

import {
  ActionIcon, Alert, Anchor, Badge, Button, Divider, FileButton, Group, Loader, Modal, ScrollArea, Select, Stack,
  TagsInput, Text, TextInput, Textarea, Tooltip, UnstyledButton,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import {
  IconBrandGmail, IconBrandWindows, IconDeviceFloppy, IconLock, IconMail, IconPaperclip, IconPlus, IconSend, IconX,
} from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError, qs } from "../api";
import { useMe } from "../context";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import type { ModuleSchema, Page, RecordData } from "../types";
import { isVaultLock, useVaultStatus } from "../vault/api";
import { UnlockForm } from "../vault/VaultGate";
import { connectMailbox } from "./MailboxCard";
import { sizeLabel, uploadAttachments, useMail, useTemplates, type Attachment, type Person } from "./api";

type Suggestions = { about: { module: string; id: number; title: string }; can_log: boolean; people: Person[] };

const FIELD = /\{\s*([A-Za-z_]+)\s*(?:\|([^{}\n]{0,80}))?\}/g;
const HONORIFICS = new Set(["mr", "mrs", "ms", "miss", "dr", "prof", "shri", "smt", "sri", "ca", "cs", "adv"]);

function firstLast(name: string): [string, string] {
  let parts = name.trim().split(/\s+/).filter(Boolean);
  while (parts.length > 1 && HONORIFICS.has(parts[0].toLowerCase().replace(/\.$/, ""))) parts = parts.slice(1);
  return [parts[0] ?? "", parts.length > 1 ? parts[parts.length - 1] : ""];
}

/** Fill {first_name} and the rest the same way campaigns do. */
export function fillMerge(text: string, values: Record<string, string>) {
  return text.replace(FIELD, (all, key: string, fallback?: string) => {
    const k = key.toLowerCase();
    if (!(k in values)) return all;
    return values[k]?.trim() || (fallback ?? "").trim();
  });
}

export default function Composer({ module, record, onClose }: { module: ModuleSchema; record: RecordData; onClose: () => void }) {
  const me = useMe();
  const qc = useQueryClient();
  const mail = useMail();
  const templates = useTemplates();
  const about = `${module.key}:${record.id}`;
  const sugg = useQuery({ queryKey: ["mail", "recipients", about], queryFn: () => api<Suggestions>(`mail/recipients/${qs({ about })}`) });
  const people = sugg.data?.people ?? [];

  const [to, setTo] = useState<string[]>([]);
  const [cc, setCc] = useState<string[]>([]);
  const [bcc, setBcc] = useState<string[]>([]);
  const [showCc, setShowCc] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [files, setFiles] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [vaultOpen, setVaultOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [saving, setSaving] = useState(false);
  const [naming, setNaming] = useState<string | null>(null);

  const account = mail.data?.account;
  const picked = new Set([...to, ...cc, ...bcc].map((e) => e.toLowerCase()));
  const firstPerson = people.find((p) => p.email.toLowerCase() === to[0]?.toLowerCase());

  function useTemplate(id: string | null) {
    const t = templates.data?.find((x) => String(x.id) === id);
    if (!t) return;
    const [first, last] = firstLast(firstPerson?.name ?? "");
    const values = {
      first_name: first, last_name: last, full_name: firstPerson?.name ?? "", designation: firstPerson?.designation ?? "",
      firm: firstPerson?.firm ?? "", city: "", my_name: me.full_name, my_first_name: firstLast(me.full_name)[0],
      my_email: account?.email ?? me.email,
    };
    setSubject(fillMerge(t.subject, values));
    setBody(fillMerge(t.body, values));
  }

  async function addFiles(list: File[]) {
    if (!list.length) return;
    setUploading(true);
    try {
      const added = await uploadAttachments(list);
      setFiles((f) => [...f, ...added]);
    } catch (e) {
      notifyError(e, "Couldn't attach");
    } finally {
      setUploading(false);
    }
  }

  async function send() {
    setBusy(true);
    setProblem("");
    try {
      const r = await api<{ status: string; web_link: string; logged_on: number }>("mail/send/", {
        body: {
          about, to, cc, bcc, subject, body,
          attachments: files.map((f) => (f.kind === "vault" ? { kind: "vault", id: f.id } : { kind: "upload", token: f.token })),
        },
      });
      notifications.show({
        color: "teal", icon: <IconMail size={16} />,
        title: r.status === "confirmed" ? "Sent and confirmed" : "Sent",
        message: r.status === "confirmed"
          ? `It's in your Sent folder, and logged in ${r.logged_on} ${r.logged_on === 1 ? "place" : "places"}.`
          : `Waiting for ${account?.provider_label ?? "your mailbox"} to file it in Sent Items. The log updates when it does.`,
      });
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["notes"] }),
        qc.invalidateQueries({ queryKey: ["record"] }),
        qc.invalidateQueries({ queryKey: ["records"] }),
        qc.invalidateQueries({ queryKey: ["mail", "account"] }),
      ]);
      onClose();
    } catch (e) {
      setProblem(errorMessage(e));
      if (e instanceof ApiError && e.code === "mail_reconnect") qc.invalidateQueries({ queryKey: ["mail", "account"] });
    } finally {
      setBusy(false);
    }
  }

  async function saveTemplate() {
    const name = (naming ?? "").trim();
    if (!name) return;
    setSaving(true);
    try {
      await api("mail/templates/", { body: { name, subject, body } });
      await qc.invalidateQueries({ queryKey: ["mail", "templates"] });
      notifyOk(`Template “${name}” saved. Pick it from “Start from a template” next time.`);
      setNaming(null);
    } catch (e) {
      notifyError(e);
    } finally {
      setSaving(false);
    }
  }

  const emailList = people.map((p) => p.email);
  const ProviderIcon = account?.provider === "outlook" ? IconBrandWindows : IconBrandGmail;

  return (
    <Modal opened onClose={onClose} size="xl" title={<Text fw={600}>Email about {record._title}</Text>}
      closeOnClickOutside={false}>
      {!mail.data ? <Group justify="center" p="xl"><Loader size="sm" /></Group> : !account || account.needs_reconnect ? (
        <Stack>
          <Text size="sm">
            {account?.needs_reconnect
              ? `Your ${account.provider_label} needs to be connected again before you can send.`
              : "Connect your own Gmail or Outlook to send email from Dealflow. Emails go from your address and stay in your Sent folder."}
          </Text>
          <Group gap="xs">
            {mail.data.providers.map((p) => (
              <Button key={p.key} variant="default" disabled={!p.ready || mail.data!.in_sample}
                leftSection={p.key === "gmail" ? <IconBrandGmail size={16} /> : <IconBrandWindows size={16} />}
                onClick={() => connectMailbox(p.key).catch((e) => notifyError(e, "Couldn't connect"))}>
                Connect {p.label}
              </Button>
            ))}
          </Group>
          {mail.data.in_sample && (
            <Text size="xs" c="dimmed">This is the sample firm: mailboxes can be connected once Dealflow is set up for real.</Text>
          )}
          {!mail.data.in_sample && mail.data.providers.some((p) => !p.ready) && (
            <Text size="xs" c="dimmed">
              {mail.data.providers.filter((p) => !p.ready).map((p) => p.label).join(" and ")} isn't set up for this install yet.{" "}
              {me.role.is_admin ? <Anchor component={Link} to="/settings/email" size="xs" onClick={onClose}>Set it up</Anchor> : "Ask an admin."}
            </Text>
          )}
        </Stack>
      ) : (
        <Stack gap="sm">
          <Group gap={6}>
            <ProviderIcon size={16} />
            <Text size="sm">From <b>{me.full_name}</b> &lt;{account.email}&gt;</Text>
            <Text size="xs" c="dimmed">· {account.sent_today} of {account.daily_limit} sent today</Text>
          </Group>
          <TagsInput label="To" placeholder={to.length ? "" : "Type an email address and press Enter"} value={to} onChange={setTo}
            data={emailList} splitChars={[",", ";", " "]} clearable acceptValueOnBlur />
          {people.filter((p) => !picked.has(p.email.toLowerCase())).length > 0 && (
            <div>
              <Text size="xs" c="dimmed" mb={4}>People on this {module.singular.toLowerCase()} (click to add):</Text>
              <Group gap={6}>
                {people.filter((p) => !picked.has(p.email.toLowerCase())).slice(0, 12).map((p) => (
                  <UnstyledButton key={p.email} className="df-chip" onClick={() => setTo((t) => [...t, p.email])}
                    title={p.opted_out ? "Marked “No campaign emails” (single emails are fine)" : undefined}>
                    <IconPlus size={12} /> {p.name}
                    <Text span size="xs" c="dimmed"> {p.email}{p.primary ? " · primary" : ""}</Text>
                  </UnstyledButton>
                ))}
              </Group>
            </div>
          )}
          {showCc ? (
            <Group grow align="flex-start">
              <TagsInput label="Cc" value={cc} onChange={setCc} data={emailList} splitChars={[",", ";", " "]} acceptValueOnBlur />
              <TagsInput label="Bcc" value={bcc} onChange={setBcc} data={emailList} splitChars={[",", ";", " "]} acceptValueOnBlur />
            </Group>
          ) : (
            <Group><Button size="xs" variant="subtle" onClick={() => setShowCc(true)}>Add Cc or Bcc</Button></Group>
          )}
          {!!templates.data?.length && (
            <Select label="Start from a template" placeholder="Pick a template" clearable searchable value={null}
              data={templates.data.map((t) => ({ value: String(t.id), label: t.shared && !t.mine ? `${t.name} (shared by ${t.owner})` : t.name }))}
              onChange={useTemplate} description={to.length ? undefined : "Add who it's to first, so their name fills in."} />
          )}
          <TextInput label="Subject" value={subject} onChange={(e) => setSubject(e.currentTarget.value)} maxLength={300} />
          <Textarea label="Message" autosize minRows={8} maxRows={20} value={body} onChange={(e) => setBody(e.currentTarget.value)} />
          <div>
            <Group gap="xs">
              <FileButton onChange={addFiles} multiple>
                {(props) => <Button {...props} size="xs" variant="default" loading={uploading} leftSection={<IconPaperclip size={14} />}>Attach from computer</Button>}
              </FileButton>
              {me.perms.vault?.view && (
                <Button size="xs" variant="default" leftSection={<IconLock size={14} />} onClick={() => setVaultOpen((v) => !v)}>
                  {vaultOpen ? "Hide the Vault" : "Attach from the Vault"}
                </Button>
              )}
            </Group>
            {vaultOpen && <VaultPicker about={about} chosen={files} onPick={(a) => setFiles((f) => [...f, a])} />}
            {files.length > 0 && (
              <Stack gap={4} mt="xs">
                {files.map((f, i) => (
                  <Group key={`${f.kind}-${f.id ?? f.token}`} gap={6} className="df-attachment">
                    <IconPaperclip size={14} />
                    <Text size="sm">{f.name}</Text>
                    <Text size="xs" c="dimmed">{sizeLabel(f.size)}{f.kind === "vault" ? " · from the Vault" : ""}</Text>
                    <Tooltip label="Remove">
                      <ActionIcon size="sm" variant="subtle" color="gray" aria-label={`Remove ${f.name}`}
                        onClick={() => setFiles((all) => all.filter((_, j) => j !== i))}><IconX size={14} /></ActionIcon>
                    </Tooltip>
                  </Group>
                ))}
              </Stack>
            )}
          </div>
          {problem && <Alert color="red" py="xs">{problem}</Alert>}
          {sugg.data && !sugg.data.can_log && (
            <Text size="xs" c="orange">Your role can't edit this {module.singular.toLowerCase()}, so emails can't be sent from it.</Text>
          )}
          {naming !== null && (
            <Group gap="xs" align="flex-end">
              <TextInput size="xs" label="Template name" value={naming} onChange={(e) => setNaming(e.currentTarget.value)} autoFocus
                style={{ flex: 1 }} maw={320} onKeyDown={(e) => e.key === "Enter" && saveTemplate()} />
              <Button size="xs" onClick={saveTemplate} loading={saving} disabled={!naming.trim()}>Save template</Button>
              <Button size="xs" variant="subtle" color="gray" onClick={() => setNaming(null)}>Cancel</Button>
            </Group>
          )}
          <Divider />
          <Group justify="space-between">
            <Text size="xs" c="dimmed" maw={420}>
              Sent from your mailbox. Logged on this {module.singular.toLowerCase()} and on each contact it goes to, whose Last contacted moves to today.
            </Text>
            <Group gap="xs">
              <Button variant="subtle" leftSection={<IconDeviceFloppy size={16} />}
                disabled={(!subject.trim() && !body.trim()) || naming !== null} onClick={() => setNaming(subject)}>Save as template</Button>
              <Button leftSection={<IconSend size={16} />} loading={busy} onClick={send}
                disabled={!to.length || !subject.trim() || !body.trim() || uploading || sugg.data?.can_log === false}>
                Send
              </Button>
            </Group>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}

function VaultPicker({ about, chosen, onPick }: { about: string; chosen: Attachment[]; onPick: (a: Attachment) => void }) {
  const { data: status } = useVaultStatus();
  const [q, setQ] = useState("");
  const [debounced] = useDebouncedValue(q.trim(), 250);
  const tagged = ["companies", "outreach", "deals", "investors"].includes(about.split(":")[0]);
  const params = debounced ? { q: debounced, page_size: 20 } : tagged ? { about, page_size: 50, ordering: "-created_at" } : { page_size: 20, ordering: "-created_at" };
  const { data, isLoading, error } = useQuery({
    queryKey: ["records", "vault", "pick", params, status?.open],
    queryFn: () => api<Page<RecordData>>(`m/vault/${qs(params)}`),
    enabled: !!status?.open,
  });
  const have = useMemo(() => new Set(chosen.filter((c) => c.kind === "vault").map((c) => c.id)), [chosen]);
  if (!status) return null;
  if (!status.has_pin) return <Text size="sm" c="dimmed" mt="xs">Set your Vault PIN on the Vault page to attach its documents.</Text>;
  if (!status.open || isVaultLock(error)) {
    return (
      <div style={{ marginTop: 8 }}>
        <Text size="sm" c="dimmed" mb={6}>The Vault is locked. Enter your PIN to pick documents.</Text>
        <UnlockForm compact />
      </div>
    );
  }
  const rows = data?.results ?? [];
  return (
    <Stack gap={6} mt="xs" className="df-picker">
      <TextInput size="xs" placeholder="Search the Vault" value={q} onChange={(e) => setQ(e.currentTarget.value)} />
      <Text size="xs" c="dimmed">{debounced ? "Matching documents" : tagged ? "Documents about this record" : "Latest documents"} (click to attach):</Text>
      {isLoading ? <Loader size="xs" /> : error ? <Text size="sm" c="red">{errorMessage(error)}</Text> : rows.length === 0 ? (
        <Text size="sm" c="dimmed">Nothing here. Search the Vault for other documents.</Text>
      ) : (
        <ScrollArea.Autosize mah={180}>
          <Stack gap={2}>
            {rows.map((d) => (
              <UnstyledButton key={d.id} className="df-pick-row" disabled={have.has(d.id)}
                onClick={() => onPick({ kind: "vault", id: d.id, name: String(d.file_name ?? d._title), size: Number(d.file_size), type: String(d.file_type) })}>
                <Group gap={6} wrap="nowrap">
                  <Text size="sm" fw={500} truncate>{d._title}</Text>
                  <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>{String(d.file_type)} · {sizeLabel(Number(d.file_size))}</Text>
                  {have.has(d.id) && <Badge size="xs" variant="light">Attached</Badge>}
                </Group>
              </UnstyledButton>
            ))}
          </Stack>
        </ScrollArea.Autosize>
      )}
    </Stack>
  );
}
