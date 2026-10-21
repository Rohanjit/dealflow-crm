// Campaigns: one email to many people (a mail merge), sent from the person's own mailbox in the
// background, about N a minute and within the mailbox's daily limit. Plus email templates.

import {
  ActionIcon, Alert, Anchor, Badge, Button, Checkbox, FileButton, Grid, Group, Modal, Pagination, Paper, Progress,
  Radio, SegmentedControl, Select, Stack, Table, Text, TextInput, Textarea, Title, Tooltip, UnstyledButton,
} from "@mantine/core";
import { DateTimePicker } from "@mantine/dates";
import { useDebouncedValue } from "@mantine/hooks";
import { modals } from "@mantine/modals";
import {
  IconExternalLink, IconLock, IconPaperclip, IconPlayerPause, IconPlayerPlay, IconPlus, IconSend, IconTrash, IconX,
} from "@tabler/icons-react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { api, qs } from "../api";
import { useMe } from "../context";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import { moduleIcon, PageHeader, PageSkeleton, Skeleton } from "../components/ui";
import { formatDate, formatDateTime } from "../format";
import { MultiLinkInput } from "../module/LinkInput";
import type { FieldSchema, LinkValue, Page } from "../types";
import {
  sizeLabel, STATUS_COLOR, STATUS_LABEL, uploadAttachments, useMail, useTemplates,
  type Attachment, type Campaign, type Recipient, type Template,
} from "../mail/api";

const CAMPAIGN_COLOR: Record<string, string> = {
  draft: "gray", scheduled: "blue", sending: "navy", paused: "orange", done: "teal", cancelled: "gray",
};

export default function CampaignsPage() {
  return (
    <Routes>
      <Route index element={<CampaignList />} />
      <Route path=":id" element={<CampaignPage />} />
    </Routes>
  );
}

async function newCampaign(nav: ReturnType<typeof useNavigate>, recipients?: unknown, name = "Untitled campaign") {
  try {
    const c = await api<Campaign>("mail/campaigns/", { body: { name, recipients } });
    nav(`/campaigns/${c.id}`);
    return c;
  } catch (e) {
    notifyError(e, "Couldn't start a campaign");
  }
}
export { newCampaign };

function CampaignList() {
  const me = useMe();
  const nav = useNavigate();
  const [tab, setTab] = useState("campaigns");
  const { data, isLoading, error } = useQuery({ queryKey: ["mail", "campaigns"], queryFn: () => api<Campaign[]>("mail/campaigns/"),
    refetchInterval: (q) => (q.state.data?.some((c) => c.status === "sending") ? 15_000 : false) });

  return (
    <Stack gap="md" maw={1100}>
      <PageHeader icon={moduleIcon("campaigns")} title="Campaigns"
        sub="One email to many people, each with their own name in it, sent from your mailbox in the background."
        actions={me.flags.send_campaigns && <Button leftSection={<IconPlus size={16} />} onClick={() => newCampaign(nav)}>New campaign</Button>} />
      <SegmentedControl w={260} value={tab} onChange={setTab} data={[{ value: "campaigns", label: "Campaigns" }, { value: "templates", label: "Templates" }]} />
      {tab === "templates" ? <Templates /> : isLoading ? <Skeleton h={120} /> : error ? <Text c="red">{errorMessage(error)}</Text> : !data!.length ? (
        <Paper withBorder p="xl">
          <Stack gap={6} align="flex-start">
            <Text fw={600}>No campaigns yet</Text>
            <Text size="sm" c="dimmed">Start one here, or tick people on the Contacts or Investors list and choose “Email selected”.</Text>
            <Button mt="xs" leftSection={<IconPlus size={16} />} onClick={() => newCampaign(nav)}>New campaign</Button>
          </Stack>
        </Paper>
      ) : (
        <Paper withBorder radius="md">
          <Table className="df-table" highlightOnHover verticalSpacing="sm" horizontalSpacing="lg">
            <Table.Thead>
              <Table.Tr><Table.Th>Name</Table.Th><Table.Th>Status</Table.Th><Table.Th>Sent</Table.Th><Table.Th>Started</Table.Th>
                {me.role.is_admin && <Table.Th>Sender</Table.Th>}</Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data!.map((c) => {
                const due = c.counts.total - c.counts.skipped;
                return (
                  <Table.Tr key={c.id} style={{ cursor: "pointer" }} onClick={() => nav(`/campaigns/${c.id}`)} data-hero-row={`campaign:${c.id}`}>
                    <Table.Td><Anchor component={Link} to={`/campaigns/${c.id}`} fw={600} size="sm" onClick={(e) => e.stopPropagation()}>{c.name}</Anchor>
                      <Text size="xs" c="dimmed" truncate maw={380}>{c.subject || "No subject yet"}</Text></Table.Td>
                    <Table.Td><Badge variant="light" color={CAMPAIGN_COLOR[c.status]}>{c.status_label}</Badge></Table.Td>
                    <Table.Td><Text size="sm">{c.counts.sent} of {due}</Text></Table.Td>
                    <Table.Td><Text size="sm" c="dimmed">{c.started_at ? formatDate(c.started_at.slice(0, 10), me) : "—"}</Text></Table.Td>
                    {me.role.is_admin && <Table.Td><Text size="sm">{c.owner.name}</Text></Table.Td>}
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Paper>
      )}
      <Text size="xs" c="dimmed">Click a campaign to open it.</Text>
    </Stack>
  );
}

// ---------------------------------------------------------------- one campaign

function CampaignPage() {
  const { id } = useParams();
  const me = useMe();
  const nav = useNavigate();
  const qc = useQueryClient();
  const key = ["mail", "campaign", id];
  const { data: c, isLoading, error } = useQuery({
    queryKey: key, queryFn: () => api<Campaign & { per_minute: number; mailbox: { email: string; sent_today: number; daily_limit: number } | null }>(`mail/campaigns/${id}/`),
    refetchInterval: (q) => (q.state.data && ["sending", "scheduled"].includes(q.state.data.status) ? 8_000 : false),
  });
  const [draft, setDraft] = useState<{ name: string; subject: string; body: string; send_at: string | null } | null>(null);
  const [debounced] = useDebouncedValue(draft, 700);
  const [saveState, setSaveState] = useState<"" | "saving" | "saved">("");
  const [busy, setBusy] = useState("");
  const loadedFor = useRef<string | undefined>();

  useEffect(() => {
    if (c && loadedFor.current !== id) {
      loadedFor.current = id;
      setDraft({ name: c.name, subject: c.subject, body: c.body ?? "", send_at: c.send_at });
    }
  }, [c, id]);

  useEffect(() => {
    if (!debounced || !c?.editable) return;
    if (debounced.name === c.name && debounced.subject === c.subject && debounced.body === c.body && debounced.send_at === c.send_at) return;
    if (!debounced.name.trim()) return;
    setSaveState("saving");
    api<Campaign>(`mail/campaigns/${id}/`, { method: "PATCH", body: debounced })
      .then((x) => { qc.setQueryData(key, (old: object) => ({ ...old, ...x })); setSaveState("saved"); })
      .catch((e) => { setSaveState(""); notifyError(e); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  if (isLoading || !draft) return error ? <Text c="red">{errorMessage(error)}</Text> : <PageSkeleton />;
  if (!c) return <Text c="red">{errorMessage(error)}</Text>;

  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: key }), qc.invalidateQueries({ queryKey: ["mail", "people", id] }),
    qc.invalidateQueries({ queryKey: ["mail", "campaigns"] })]);

  async function act(action: string, done: string) {
    setBusy(action);
    try {
      await api(`mail/campaigns/${id}/${action}/`, { method: "POST" });
      await refresh();
      notifyOk(done);
    } catch (e) {
      notifyError(e, "Couldn't do that");
    } finally {
      setBusy("");
    }
  }

  function start() {
    const n = c!.counts.queued;
    const minutes = Math.ceil(n / Math.max(1, c!.per_minute));
    const left = c!.mailbox ? c!.mailbox.daily_limit - c!.mailbox.sent_today : 0;
    modals.openConfirmModal({
      title: `Send to ${n} ${n === 1 ? "person" : "people"}?`,
      children: (
        <Stack gap={6}>
          <Text size="sm">From {c!.mailbox?.email ?? "your mailbox"}, {c!.send_at && new Date(c!.send_at) > new Date()
            ? `starting ${formatDateTime(c!.send_at, me)}` : "starting now"}. About {c!.per_minute} a minute, so roughly {minutes} {minutes === 1 ? "minute" : "minutes"}.</Text>
          {n > left && <Text size="sm" c="orange">Your mailbox can send {left} more today; the rest go out after midnight.</Text>}
          <Text size="sm" c="dimmed">Each email is logged on the contact and their firm, and you can pause at any time.</Text>
        </Stack>
      ),
      labels: { confirm: "Start sending", cancel: "Not yet" },
      onConfirm: () => act("start", "Campaign started. It sends in the background; you can leave this page."),
    });
  }

  async function test() {
    setBusy("test");
    try {
      const r = await api<{ to: string }>(`mail/campaigns/${id}/test/`, { method: "POST" });
      notifyOk(`Test sent to ${r.to}, as the first person will get it.`);
    } catch (e) {
      notifyError(e, "Couldn't send the test");
    } finally {
      setBusy("");
    }
  }

  function remove() {
    modals.openConfirmModal({
      title: `Delete “${c!.name}”?`,
      children: <Text size="sm">The campaign and its list of people go. Emails already sent stay logged on each contact.</Text>,
      labels: { confirm: "Delete", cancel: "Cancel" }, confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await api(`mail/campaigns/${id}/`, { method: "DELETE" });
          await qc.invalidateQueries({ queryKey: ["mail", "campaigns"] });
          nav("/campaigns");
        } catch (e) {
          notifyError(e, "Couldn't delete");
        }
      },
    });
  }

  const editable = !!c.editable;
  const started = c.status !== "draft";
  const due = c.counts.total - c.counts.skipped;
  const actions = (
    <>
      {c.status === "draft" && c.mine && <>
        <Button variant="default" loading={busy === "test"} onClick={test}>Send a test to me</Button>
        <Button leftSection={<IconSend size={16} />} disabled={!!c.problems?.length} loading={busy === "start"} onClick={start}>Start sending</Button>
      </>}
      {["sending", "scheduled"].includes(c.status) && (
        <Button variant="default" leftSection={<IconPlayerPause size={16} />} loading={busy === "pause"} onClick={() => act("pause", "Paused.")}>Pause</Button>
      )}
      {c.status === "paused" && c.mine && (
        <Button leftSection={<IconPlayerPlay size={16} />} loading={busy === "resume"} onClick={() => act("resume", "Carrying on.")}>Resume</Button>
      )}
      {["sending", "scheduled", "paused"].includes(c.status) && (
        <Button variant="subtle" color="red" loading={busy === "cancel"} onClick={() => modals.openConfirmModal({
          title: "Cancel this campaign?", children: <Text size="sm">Nobody else gets it. Emails already sent stay sent.</Text>,
          labels: { confirm: "Cancel campaign", cancel: "Keep it" }, confirmProps: { color: "red" },
          onConfirm: () => act("cancel", "Campaign cancelled."),
        })}>Cancel campaign</Button>
      )}
      {["draft", "done", "cancelled"].includes(c.status) && (
        <Button variant="subtle" color="red" leftSection={<IconTrash size={16} />} onClick={remove}>Delete</Button>
      )}
    </>
  );

  return (
    <Stack gap="lg" maw={1280}>
      <Anchor component={Link} to="/campaigns" size="sm">Campaigns</Anchor>
      <PageHeader icon={moduleIcon("campaigns")} hero={`campaign:${c.id}`}
        title={editable ? (
          <TextInput variant="unstyled" className="df-title-input" value={draft.name} aria-label="Campaign name"
            onChange={(e) => setDraft({ ...draft, name: e.currentTarget.value })} />
        ) : c.name}
        sub={<Group gap={8}>
          <Badge variant="light" color={CAMPAIGN_COLOR[c.status]}>{c.status_label}</Badge>
          <Text size="sm" c="dimmed">From {c.mailbox?.email ?? "a mailbox not connected yet"}{!c.mine ? ` · ${c.owner.name}` : ""}</Text>
          {editable && saveState && <Text size="xs" c="dimmed">{saveState === "saving" ? "Saving…" : "Saved"}</Text>}
        </Group>}
        actions={actions} />

      {c.status === "paused" && c.paused_reason && <Alert color="orange" title="Paused">{c.paused_reason}</Alert>}
      {c.status === "draft" && !!c.problems?.length && (
        <Alert color="gray" title="Before you can start">{c.problems.join(" ")}{!c.mailbox && (
          <> Connect your mailbox in <Anchor component={Link} to="/settings/preferences">My preferences</Anchor>.</>)}</Alert>
      )}
      {started && (
        <Paper withBorder p="lg">
          <Group justify="space-between" mb={6}>
            <Text fw={600}>{c.counts.sent} of {due} sent</Text>
            <Text size="sm" c="dimmed">
              {c.confirmed ?? 0} confirmed in Sent · {c.counts.failed} failed · {c.counts.skipped} skipped
              {c.status === "scheduled" && c.send_at ? ` · starts ${formatDateTime(c.send_at, me)}` : ""}
            </Text>
          </Group>
          <Progress value={due ? (c.counts.sent / due) * 100 : 0} size="md" radius="xl" color={c.status === "paused" ? "orange" : "teal"} />
        </Paper>
      )}

      <Grid gutter={{ base: "md", lg: 40 }} align="flex-start">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Stack gap="lg">
            <EmailEditor c={c} draft={draft} setDraft={setDraft} editable={editable} onChanged={refresh} />
            <People c={c} editable={editable} onChanged={refresh} />
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }} className="df-side">
          <Preview id={c.id} version={`${c.subject}|${c.body}|${c.counts.total}`} />
        </Grid.Col>
      </Grid>
    </Stack>
  );
}

function EmailEditor({ c, draft, setDraft, editable, onChanged }: {
  c: Campaign; draft: { name: string; subject: string; body: string; send_at: string | null };
  setDraft: (d: { name: string; subject: string; body: string; send_at: string | null }) => void; editable: boolean; onChanged: () => void;
}) {
  const mail = useMail();
  const templates = useTemplates();
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const [uploading, setUploading] = useState(false);
  const [when, setWhen] = useState(draft.send_at ? "later" : "now");

  function insert(key: string) {
    const token = `{${key}}`;
    const el = bodyRef.current;
    if (!el) return setDraft({ ...draft, body: draft.body + token });
    const at = el.selectionStart ?? draft.body.length;
    const end = el.selectionEnd ?? at;
    setDraft({ ...draft, body: draft.body.slice(0, at) + token + draft.body.slice(end) });
    requestAnimationFrame(() => { el.focus(); el.setSelectionRange(at + token.length, at + token.length); });
  }

  async function setAttachments(list: Attachment[]) {
    try {
      await api(`mail/campaigns/${c.id}/`, { method: "PATCH", body: { attachments: list.map((a) => (a.kind === "vault" ? { kind: "vault", id: a.id } : { kind: "upload", token: a.token })) } });
      onChanged();
    } catch (e) {
      notifyError(e, "Couldn't change the attachments");
    }
  }

  async function addFiles(files: File[]) {
    if (!files.length) return;
    setUploading(true);
    try {
      const added = await uploadAttachments(files);
      await setAttachments([...(c.attachments ?? []), ...added]);
    } catch (e) {
      notifyError(e, "Couldn't attach");
    } finally {
      setUploading(false);
    }
  }

  return (
    <Paper withBorder p="lg">
      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={5}>The email</Title>
          {editable && !!templates.data?.length && (
            <Select size="xs" w={240} placeholder="Start from a template" value={null} clearable searchable
              data={templates.data.map((t) => ({ value: String(t.id), label: t.name }))}
              onChange={(v) => { const t = templates.data?.find((x) => String(x.id) === v); if (t) setDraft({ ...draft, subject: t.subject, body: t.body }); }} />
          )}
        </Group>
        <TextInput label="Subject" value={draft.subject} disabled={!editable} maxLength={300}
          onChange={(e) => setDraft({ ...draft, subject: e.currentTarget.value })} />
        <Textarea ref={bodyRef} label="Message" autosize minRows={10} maxRows={28} value={draft.body} disabled={!editable}
          onChange={(e) => setDraft({ ...draft, body: e.currentTarget.value })} />
        {editable && (
          <div>
            <Text size="xs" c="dimmed" mb={4}>Put in each person's details (click to add where the cursor is). Write {"{first_name|there}"} to say “there” when someone has no first name.</Text>
            <Group gap={6}>
              {(mail.data?.merge_fields ?? []).map((f) => (
                <UnstyledButton key={f.key} className="df-chip" onClick={() => insert(f.key)}>{f.label}</UnstyledButton>
              ))}
            </Group>
          </div>
        )}
        <Text size="xs" c="dimmed">Every campaign email ends with: “{c.opt_out_text}”</Text>
        <div>
          <Text size="sm" fw={500} mb={4}>Attachments</Text>
          {(c.attachments ?? []).length === 0 && <Text size="sm" c="dimmed">None.</Text>}
          <Stack gap={4}>
            {(c.attachments ?? []).map((a, i) => (
              <Group key={`${a.kind}-${a.id ?? a.token}`} gap={6}>
                {a.kind === "vault" ? <IconLock size={14} /> : <IconPaperclip size={14} />}
                <Text size="sm">{a.name}</Text><Text size="xs" c="dimmed">{sizeLabel(a.size)}</Text>
                {editable && (
                  <Tooltip label="Remove"><ActionIcon size="sm" variant="subtle" color="gray" aria-label={`Remove ${a.name}`}
                    onClick={() => setAttachments((c.attachments ?? []).filter((_, j) => j !== i))}><IconX size={14} /></ActionIcon></Tooltip>
                )}
              </Group>
            ))}
          </Stack>
          {editable && (
            <Group gap="xs" mt="xs">
              <FileButton onChange={addFiles} multiple>
                {(props) => <Button {...props} size="xs" variant="default" loading={uploading} leftSection={<IconPaperclip size={14} />}>Attach from computer</Button>}
              </FileButton>
              <VaultAttach onPick={(a) => setAttachments([...(c.attachments ?? []), a])} />
            </Group>
          )}
        </div>
        {editable && (
          <Radio.Group label="When" value={when} onChange={(v) => { setWhen(v); if (v === "now") setDraft({ ...draft, send_at: null }); }}>
            <Stack gap={6} mt={4}>
              <Radio value="now" label="As soon as I start it" />
              <Radio value="later" label="At a set time" />
              {when === "later" && (
                <DateTimePicker maw={280} placeholder="Pick a date and time" minDate={new Date()} valueFormat="DD MMM YYYY, HH:mm"
                  value={draft.send_at ? new Date(draft.send_at) : null}
                  onChange={(d) => setDraft({ ...draft, send_at: d ? d.toISOString() : null })} />
              )}
            </Stack>
          </Radio.Group>
        )}
      </Stack>
    </Paper>
  );
}

function VaultAttach({ onPick }: { onPick: (a: Attachment) => void }) {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState<LinkValue[]>([]);
  if (!me.perms.vault?.view) return null;
  const f = { key: "doc", label: "Vault documents", targets: ["vault"], required: false, help: "" } as unknown as FieldSchema;
  async function add() {
    for (const v of value) {
      try {
        const d = await api<{ id: number; _title: string; file_name: string; file_size: number; file_type: string }>(`m/vault/${v.id}/`);
        onPick({ kind: "vault", id: d.id, name: d.file_name || d._title, size: Number(d.file_size), type: d.file_type });
      } catch (e) {
        notifyError(e, "Couldn't attach");
      }
    }
    setValue([]);
    setOpen(false);
  }
  return (
    <>
      <Button size="xs" variant="default" leftSection={<IconLock size={14} />} onClick={() => setOpen(true)}>Attach from the Vault</Button>
      <Modal opened={open} onClose={() => setOpen(false)} title="Attach from the Vault">
        <Stack>
          <MultiLinkInput f={f} value={value} onChange={setValue} />
          <Text size="xs" c="dimmed">If the Vault is locked, open it on the Vault page with your PIN first.</Text>
          <Group justify="flex-end"><Button onClick={add} disabled={!value.length}>Attach</Button></Group>
        </Stack>
      </Modal>
    </>
  );
}

function People({ c, editable, onChanged }: { c: Campaign; editable: boolean; onChanged: () => void }) {
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [debounced] = useDebouncedValue(q.trim(), 250);
  const [contacts, setContacts] = useState<LinkValue[]>([]);
  const [investors, setInvestors] = useState<LinkValue[]>([]);
  const [who, setWho] = useState("primary");
  const [busy, setBusy] = useState(false);
  const [ticked, setTicked] = useState<number[]>([]);
  const { data } = useQuery({
    queryKey: ["mail", "people", String(c.id), status, page, debounced],
    queryFn: () => api<Page<Recipient>>(`mail/campaigns/${c.id}/people/${qs({ status: status === "all" ? undefined : status, page, q: debounced || undefined })}`),
    placeholderData: keepPreviousData,
    refetchInterval: c.status === "sending" ? 8_000 : false,
  });

  async function add() {
    setBusy(true);
    try {
      const r = await api<Campaign>(`mail/campaigns/${c.id}/`, {
        method: "PATCH",
        body: { recipients: { contacts: contacts.map((x) => x.id), investors: investors.map((x) => x.id), who } },
      });
      notifyOk(`${r.result?.added ?? 0} added${r.result?.skipped ? `, ${r.result.skipped} skipped (see why below)` : ""}.`);
      setContacts([]);
      setInvestors([]);
      onChanged();
    } catch (e) {
      notifyError(e, "Couldn't add them");
    } finally {
      setBusy(false);
    }
  }

  async function takeOff() {
    try {
      await api(`mail/campaigns/${c.id}/people/`, { method: "DELETE", body: { ids: ticked } });
      setTicked([]);
      onChanged();
    } catch (e) {
      notifyError(e, "Couldn't take them off");
    }
  }

  const contactField = { key: "c", label: "Contacts", targets: ["contacts"], required: false, help: "" } as unknown as FieldSchema;
  const investorField = { key: "i", label: "Investors' contacts", targets: ["investors"], required: false, help: "" } as unknown as FieldSchema;
  const rows = data?.results ?? [];
  const counts = c.counts;

  return (
    <Paper withBorder p="lg">
      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={5}>People ({counts.total - counts.skipped} to get it{counts.skipped ? `, ${counts.skipped} skipped` : ""})</Title>
        </Group>
        {editable && (
          <Paper bg="var(--df-hover, var(--mantine-color-gray-0))" p="sm" radius="sm">
            <Stack gap="xs">
              <Text size="sm" fw={500}>Add people</Text>
              <MultiLinkInput f={contactField} value={contacts} onChange={setContacts} />
              <MultiLinkInput f={investorField} value={investors} onChange={setInvestors} />
              {investors.length > 0 && (
                <SegmentedControl size="xs" value={who} onChange={setWho} w={320}
                  data={[{ value: "primary", label: "Primary contacts" }, { value: "all", label: "Everyone there" }]} />
              )}
              <Group justify="space-between">
                <Text size="xs" c="dimmed">Or tick people on the Contacts or Investors list and choose “Email selected”. People marked “No campaign emails” or without an email are skipped.</Text>
                <Button size="xs" onClick={add} loading={busy} disabled={!contacts.length && !investors.length}>Add</Button>
              </Group>
            </Stack>
          </Paper>
        )}
        <Group justify="space-between" gap="xs">
          <SegmentedControl size="xs" value={status} onChange={(v) => { setStatus(v); setPage(1); }}
            data={[{ value: "all", label: `All ${counts.total}` }, { value: "queued", label: `Waiting ${counts.queued}` },
              { value: "sent", label: `Sent ${counts.sent}` }, { value: "failed", label: `Failed ${counts.failed}` },
              { value: "skipped", label: `Skipped ${counts.skipped}` }]} />
          <TextInput size="xs" placeholder="Search people" value={q} onChange={(e) => { setQ(e.currentTarget.value); setPage(1); }} w={200} />
        </Group>
        {editable && ticked.length > 0 && (
          <Group><Button size="xs" variant="default" color="red" leftSection={<IconTrash size={14} />} onClick={takeOff}>Take {ticked.length} off</Button></Group>
        )}
        {rows.length === 0 ? <Text size="sm" c="dimmed">{counts.total ? "Nobody here." : "Nobody added yet."}</Text> : (
          <Table className="df-table" verticalSpacing={6}>
            <Table.Tbody>
              {rows.map((r) => (
                <Table.Tr key={r.id}>
                  {editable && <Table.Td w={32}><Checkbox size="xs" aria-label={`Tick ${r.name}`} checked={ticked.includes(r.id)} disabled={r.status === "sent"}
                    onChange={(e) => setTicked((t) => (e.currentTarget.checked ? [...t, r.id] : t.filter((x) => x !== r.id)))} /></Table.Td>}
                  <Table.Td>
                    {r.contact_id ? <Anchor component={Link} to={`/m/contacts/${r.contact_id}`} size="sm" fw={500}>{r.name}</Anchor> : <Text size="sm">{r.name}</Text>}
                    <Text size="xs" c="dimmed">{r.email || "No email"}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={6}>
                      <Badge size="sm" variant="light" color={{ sent: "teal", failed: "red", skipped: "gray", queued: "blue", sending: "navy" }[r.status]}>{r.status_label}</Badge>
                      {r.confirm_status && r.status === "sent" && <Badge size="sm" variant="dot" color={STATUS_COLOR[r.confirm_status]}>{STATUS_LABEL[r.confirm_status]}</Badge>}
                    </Group>
                    {r.reason && <Text size="xs" c="dimmed">{r.reason}</Text>}
                  </Table.Td>
                  <Table.Td w={40}>{r.web_link && (
                    <Tooltip label="Open in your mailbox"><ActionIcon component="a" href={r.web_link} target="_blank" rel="noopener noreferrer" variant="subtle" color="gray" aria-label="Open in your mailbox"><IconExternalLink size={14} /></ActionIcon></Tooltip>
                  )}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        {data && data.count > 50 && <Pagination size="sm" total={Math.ceil(data.count / 50)} value={page} onChange={setPage} />}
      </Stack>
    </Paper>
  );
}

function Preview({ id, version }: { id: number; version: string }) {
  const [rid, setRid] = useState<string | null>(null);
  const people = useQuery({ queryKey: ["mail", "people", String(id), "preview-list", version],
    queryFn: () => api<Page<Recipient>>(`mail/campaigns/${id}/people/${qs({ status: "queued" })}`) });
  const { data, isFetching } = useQuery({
    queryKey: ["mail", "preview", id, rid, version],
    queryFn: () => api<{ recipient: { id: number; name: string; email: string } | null; subject: string; body: string; warnings: string[] }>(
      `mail/campaigns/${id}/preview/${qs({ recipient: rid ?? undefined })}`),
    placeholderData: keepPreviousData,
  });
  const options = (people.data?.results ?? []).map((r) => ({ value: String(r.id), label: `${r.name} · ${r.email}` }));
  return (
    <Paper withBorder p="lg" style={{ position: "sticky", top: 72 }}>
      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={5}>Preview</Title>
          {isFetching && <Text size="xs" c="dimmed">Updating…</Text>}
        </Group>
        {options.length > 0 && (
          <Select size="xs" label="As this person gets it" data={options} value={rid ?? String(data?.recipient?.id ?? "")} onChange={setRid} searchable />
        )}
        {!data ? <Skeleton h={160} /> : (
          <div className="df-mail-preview">
            <Text size="xs" c="dimmed">To {data.recipient ? `${data.recipient.name} <${data.recipient.email}>` : "the first person you add"}</Text>
            <Text fw={600} mt={4}>{data.subject || <Text span c="dimmed">No subject</Text>}</Text>
            <Text size="sm" className="df-prewrap" mt="xs">{data.body}</Text>
          </div>
        )}
        {data?.warnings.map((w) => <Text key={w} size="xs" c="orange">{w}</Text>)}
      </Stack>
    </Paper>
  );
}

// ---------------------------------------------------------------- templates

function Templates() {
  const qc = useQueryClient();
  const { data, isLoading } = useTemplates();
  const [editing, setEditing] = useState<Partial<Template> | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function save() {
    if (!editing) return;
    setBusy(true);
    setErr("");
    try {
      const body = { name: editing.name, subject: editing.subject ?? "", body: editing.body ?? "", shared: !!editing.shared };
      if (editing.id) await api(`mail/templates/${editing.id}/`, { method: "PATCH", body });
      else await api("mail/templates/", { body });
      await qc.invalidateQueries({ queryKey: ["mail", "templates"] });
      setEditing(null);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function remove(t: Template) {
    modals.openConfirmModal({
      title: `Delete “${t.name}”?`, labels: { confirm: "Delete", cancel: "Cancel" }, confirmProps: { color: "red" },
      children: <Text size="sm">{t.shared ? "Everyone it's shared with loses it too." : "Emails already sent aren't affected."}</Text>,
      onConfirm: async () => {
        try {
          await api(`mail/templates/${t.id}/`, { method: "DELETE" });
          await qc.invalidateQueries({ queryKey: ["mail", "templates"] });
        } catch (e) {
          notifyError(e, "Couldn't delete");
        }
      },
    });
  }

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Text size="sm" c="dimmed">Ready-made emails for single emails and campaigns. Yours unless you share them with everyone.</Text>
        <Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => setEditing({ name: "", subject: "", body: "", shared: false })}>New template</Button>
      </Group>
      {isLoading ? <Skeleton h={80} /> : !data?.length ? <Text size="sm" c="dimmed">No templates yet.</Text> : (
        <Paper withBorder radius="md">
          <Table className="df-table" verticalSpacing="sm" horizontalSpacing="lg">
            <Table.Tbody>
              {data.map((t) => (
                <Table.Tr key={t.id}>
                  <Table.Td><Text fw={600} size="sm">{t.name}</Text><Text size="xs" c="dimmed" truncate maw={420}>{t.subject}</Text></Table.Td>
                  <Table.Td><Text size="sm" c="dimmed">{t.shared ? (t.mine ? "Shared with everyone" : `Shared by ${t.owner}`) : "Only you"}</Text></Table.Td>
                  <Table.Td w={180}>
                    {t.mine && <Group gap={4} justify="flex-end">
                      <Button size="xs" variant="default" onClick={() => setEditing(t)}>Edit</Button>
                      <Button size="xs" variant="subtle" color="red" onClick={() => remove(t)}>Delete</Button>
                    </Group>}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      )}
      <Modal opened={!!editing} onClose={() => setEditing(null)} title={editing?.id ? "Edit template" : "New template"} size="lg">
        {editing && (
          <Stack>
            <TextInput label="Name" value={editing.name ?? ""} onChange={(e) => setEditing({ ...editing, name: e.currentTarget.value })} />
            <TextInput label="Subject" value={editing.subject ?? ""} onChange={(e) => setEditing({ ...editing, subject: e.currentTarget.value })} />
            <Textarea label="Message" autosize minRows={8} value={editing.body ?? ""} onChange={(e) => setEditing({ ...editing, body: e.currentTarget.value })}
              description="Use {first_name}, {firm}, {my_name} and the rest to fill in each person's details." />
            <Checkbox label="Share with everyone at the firm" checked={!!editing.shared} onChange={(e) => setEditing({ ...editing, shared: e.currentTarget.checked })} />
            {err && <Text c="red" size="sm">{err}</Text>}
            <Group justify="flex-end"><Button onClick={save} loading={busy} disabled={!editing.name?.trim()}>Save template</Button></Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}
