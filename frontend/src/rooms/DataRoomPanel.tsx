// A deal's investor data room (backend rooms/, decision 64): its files, who it's shared with, what
// they've read, their questions, and the room's settings. Everyone who can see the deal sees the panel;
// changing it needs "Share with investors" and being on the deal team.

import {
  Alert, Anchor, Badge, Button, Checkbox, FileInput, Group, Menu, Modal, Paper, Radio, ScrollArea, SegmentedControl,
  Select, Stack, Switch, Table, Text, TextInput, Textarea,
} from "@mantine/core";
import { modals } from "@mantine/modals";
import {
  IconChevronDown, IconCloudUpload, IconFileSearch, IconLink, IconPlus, IconSettings, IconShare,
} from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError, qs } from "../api";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import { Skeleton } from "../components/ui";
import { useMe } from "../context";
import { formatBytes, formatDate, formatDateTime } from "../format";
import type { Page, RecordData } from "../types";
import { isVaultLock, useVaultStatus } from "../vault/api";
import { UnlockForm } from "../vault/VaultGate";
import { readingTime, roomKey, useRoom, type Candidate, type RoomData, type RoomShare } from "./api";

const VIEWABLE = new Set(["pdf", "png", "jpg", "jpeg", "gif", "webp"]);
const ROOMABLE = new Set([...VIEWABLE, "xlsx", "xlsm", "docx", "pptx"]);
const ext = (name: string) => (name.includes(".") ? name.split(".").pop()!.toLowerCase() : "");
const STATUS_COLOR: Record<string, string> = {
  active: "teal", waiting: "orange", stopped: "gray", expired: "gray", locked: "red", closed: "gray",
};

export default function DataRoomPanel({ deal }: { deal: RecordData }) {
  const me = useMe();
  const qc = useQueryClient();
  const { data, isLoading, error } = useRoom(deal.id);
  const [open, setOpen] = useState<null | "settings" | "files" | "share" | "trace">(null);
  const [allActivity, setAllActivity] = useState(false);
  if (error instanceof ApiError && error.status === 403) return null;
  const refresh = (d?: RoomData) => (d ? qc.setQueryData(roomKey(deal.id), d) : qc.invalidateQueries({ queryKey: roomKey(deal.id) }));
  const can = data?.can.share;
  const room = data?.room;

  async function act(path: string, body: unknown, ok: string, method = "PATCH") {
    try {
      refresh(await api<RoomData>(path, { method, body }));
      if (ok) notifyOk(ok);
    } catch (e) {
      notifyError(e, "Couldn't do that");
    }
  }

  async function copyLink(s: RoomShare) {
    try {
      const { link } = await api<{ link: string }>(`rooms/shares/${s.id}/link/`);
      await navigator.clipboard.writeText(link);
      notifyOk(`${s.name}'s link is copied. It only opens with a code emailed to ${s.email}.`);
    } catch (e) {
      notifyError(e, "Couldn't copy the link");
    }
  }

  function stop(s: RoomShare) {
    modals.openConfirmModal({
      title: `Stop ${s.name}'s link?`,
      children: <Text size="sm">They're signed out at once and the link stops working. You can share again later with a new link.</Text>,
      labels: { confirm: "Stop link", cancel: "Cancel" },
      confirmProps: { color: "red" },
      onConfirm: () => act(`rooms/shares/${s.id}/`, { action: "stop" }, `${s.name}'s link is stopped.`),
    });
  }

  const status = !room ? { label: "Not set up", color: "gray" }
    : !room.is_open ? { label: "Closed", color: "gray" }
      : room.pending ? { label: "Waiting for approval", color: "orange" }
        : { label: "Open", color: "teal" };
  const openQuestions = (data?.questions ?? []).filter((q) => !q.answered_at);
  const answered = (data?.questions ?? []).filter((q) => q.answered_at);
  const activity = (data?.activity ?? []).filter((a) => a.kind !== "code_sent");

  return (
    <Paper withBorder radius="md">
      <Group justify="space-between" px="lg" pt="md" pb="sm" wrap="wrap" gap="xs">
        <Group gap="xs">
          <Text fw={600}>Data room</Text>
          {data && <Badge variant="light" color={status.color} size="sm" tt="none">{status.label}</Badge>}
        </Group>
        {data && (
          <Group gap={6}>
            {can && <Button size="xs" leftSection={<IconShare size={14} />} onClick={() => setOpen("share")}>Share with an investor</Button>}
            {can && <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} onClick={() => setOpen("files")}>Add files</Button>}
            {can && <Button size="xs" variant="default" leftSection={<IconSettings size={14} />} onClick={() => setOpen("settings")}>Room settings</Button>}
            {data.exists && <Button size="xs" variant="default" leftSection={<IconFileSearch size={14} />} onClick={() => setOpen("trace")}>Trace a file</Button>}
          </Group>
        )}
      </Group>

      {isLoading || !data ? (
        <Stack gap={8} px="lg" pb="lg"><Skeleton h={26} /><Skeleton h={26} w="70%" /></Stack>
      ) : error ? (
        <Text c="red" size="sm" px="lg" pb="lg">{errorMessage(error)}</Text>
      ) : (
        <Stack gap="md" pb="md">
          {!data.exists && (
            <Text size="sm" c="dimmed" px="lg">
              {can
                ? "Share deal documents with investors on the tracker. Each investor gets their own link, every opening needs a code emailed to them, and every page they see carries their name."
                : data.can.flag
                  ? "Only people on the deal team can share this deal with investors."
                  : "No data room yet. People on the deal team whose role can share with investors can set one up."}
            </Text>
          )}
          {can && !data.mailbox && (
            <Alert color="orange" mx="lg" p="xs">
              <Text size="sm">Connect your Gmail or Outlook in <Anchor component={Link} to="/settings/preferences" size="sm">My preferences</Anchor> first: investors' links and codes are sent from your mailbox.</Text>
            </Alert>
          )}
          {room && !room.approved && (
            <Alert color="orange" mx="lg" p="xs">
              <Text size="sm">
                {room.pending
                  ? `The first share needs approval. ${room.pending.requested_by} asked on ${formatDate(room.pending.at.slice(0, 10), me)}; waiting for ${room.approvers.join(" or ")}. Links go out as soon as it's approved.`
                  : `The first share needs approval from ${room.approvers.join(" or ")}. Links go out as soon as it's approved.`}
              </Text>
            </Alert>
          )}

          {data.exists && (
            <>
              <Section title="Files" count={data.files.length}>
                {data.files.length === 0 ? (
                  <Text size="sm" c="dimmed" px="lg">No files yet.{can && " Add them from the deal's Vault documents."}</Text>
                ) : (
                  <ScrollArea>
                    <Table className="df-table" verticalSpacing="xs" horizontalSpacing="lg" miw={640}>
                      <Table.Thead><Table.Tr><Table.Th>File</Table.Th><Table.Th w={230}>Investors can</Table.Th><Table.Th>Read by</Table.Th><Table.Th w={90} /></Table.Tr></Table.Thead>
                      <Table.Tbody>
                        {data.files.map((f) => (
                          <Table.Tr key={f.id}>
                            <Table.Td>
                              <Text size="sm" fw={600}>{f.title}</Text>
                              <Text size="xs" c="dimmed">{f.file_type}, {formatBytes(f.size)}{f.pages > 1 ? `, ${f.pages} pages` : ""}</Text>
                            </Table.Td>
                            <Table.Td>
                              <Select size="xs" disabled={!can} aria-label={`What investors can do with ${f.title}`} value={f.mode}
                                allowDeselect={false} comboboxProps={{ withinPortal: true }}
                                data={[{ value: "view", label: "View only (watermarked)", disabled: !f.can_view_only },
                                  { value: "download", label: "Download with watermark" }]}
                                onChange={(v) => v && v !== f.mode && act(`rooms/files/${f.id}/`, { mode: v }, "Saved.")} />
                            </Table.Td>
                            <Table.Td>
                              <Text size="sm">{f.readers ? `${f.readers} investor${f.readers > 1 ? "s" : ""}, ${readingTime(f.seconds)}` : "Nobody yet"}</Text>
                              {f.downloads > 0 && <Text size="xs" c="dimmed">{f.downloads} download{f.downloads > 1 ? "s" : ""}</Text>}
                            </Table.Td>
                            <Table.Td>
                              {can && (
                                <Button size="compact-xs" variant="subtle" color="red" onClick={() => modals.openConfirmModal({
                                  title: `Remove ${f.title} from the data room?`,
                                  children: <Text size="sm">Investors stop seeing it at once. It stays in the Vault.</Text>,
                                  labels: { confirm: "Remove", cancel: "Cancel" }, confirmProps: { color: "red" },
                                  onConfirm: () => act(`rooms/files/${f.id}/`, undefined, `${f.title} removed.`, "DELETE"),
                                })}>Remove</Button>
                              )}
                            </Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                  </ScrollArea>
                )}
              </Section>

              <Section title="Shared with" count={data.shares.length}>
                {data.shares.length === 0 ? (
                  <Text size="sm" c="dimmed" px="lg">Nobody yet.</Text>
                ) : (
                  <ScrollArea>
                    <Table className="df-table" verticalSpacing="xs" horizontalSpacing="md" miw={600}>
                      <Table.Thead><Table.Tr><Table.Th>Investor</Table.Th><Table.Th>Link and NDA</Table.Th><Table.Th>Activity</Table.Th><Table.Th w={110} /></Table.Tr></Table.Thead>
                      <Table.Tbody>
                        {data.shares.map((s) => (
                          <Table.Tr key={s.id}>
                            <Table.Td>
                              <Text size="sm" fw={600}>{s.investor.title}</Text>
                              <Text size="xs" c="dimmed">{s.name}, {s.email}</Text>
                            </Table.Td>
                            <Table.Td>
                              <Badge variant="light" tt="none" color={STATUS_COLOR[s.status]}>{s.status_label}</Badge>
                              {s.status === "active" && <Text size="xs" c="dimmed">Until {formatDate(s.expires_at.slice(0, 10), me)}</Text>}
                              {s.invite_error && (
                                <Text size="xs" c="red">Email not sent ({s.invite_error.split(".")[0]}). Use Actions, Copy link to send it yourself.</Text>
                              )}
                              <Text size="xs" mt={2} c={s.nda.state === "waiting" ? "orange" : "dimmed"}>{s.nda.state === "none" ? "No NDA" : `NDA: ${s.nda.label}`}</Text>
                            </Table.Td>
                            <Table.Td>
                              {s.opens ? (
                                <>
                                  <Text size="sm">Opened {s.opens}×, read {readingTime(s.seconds)}</Text>
                                  <Text size="xs" c="dimmed">Last {formatDateTime(s.last_opened_at, me)}{s.downloads ? `, ${s.downloads} download${s.downloads > 1 ? "s" : ""}` : ""}</Text>
                                </>
                              ) : <Text size="sm" c="dimmed">Not opened yet</Text>}
                              {s.open_questions > 0 && <Badge size="xs" color="orange" variant="light" tt="none">{s.open_questions} question{s.open_questions > 1 ? "s" : ""} to answer</Badge>}
                            </Table.Td>
                            <Table.Td>
                              {can && s.status !== "stopped" && (
                                <Menu position="bottom-end" withinPortal>
                                  <Menu.Target>
                                    <Button size="compact-xs" variant="default" rightSection={<IconChevronDown size={12} />}>Actions</Button>
                                  </Menu.Target>
                                  <Menu.Dropdown>
                                    {s.status === "active" && <Menu.Item leftSection={<IconLink size={14} />} onClick={() => copyLink(s)}>Copy link</Menu.Item>}
                                    {s.status === "active" && <Menu.Item onClick={() => act(`rooms/shares/${s.id}/`, { action: "resend" }, `Link sent to ${s.email} again.`)}>Send the link again</Menu.Item>}
                                    {s.nda.state === "waiting" && room?.nda_mode === "signed" && (
                                      <Menu.Item onClick={() => act(`rooms/shares/${s.id}/`, { action: "nda_received" }, `${s.name}'s room is open.`)}>Signed NDA received: open their room</Menu.Item>
                                    )}
                                    <Menu.Item onClick={() => act(`rooms/shares/${s.id}/`, { action: "extend", days: 30 }, "30 more days added.")}>Give 30 more days</Menu.Item>
                                    <Menu.Item color="red" onClick={() => stop(s)}>Stop link</Menu.Item>
                                  </Menu.Dropdown>
                                </Menu>
                              )}
                            </Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                  </ScrollArea>
                )}
              </Section>

              {(openQuestions.length > 0 || answered.length > 0) && (
                <Section title="Questions from investors" count={openQuestions.length}>
                  <Stack gap="sm" px="lg">
                    {[...openQuestions, ...answered].map((q) => (
                      <QuestionRow key={q.id} q={q} canAnswer={!!can} onDone={refresh} />
                    ))}
                  </Stack>
                </Section>
              )}

              <Section title="Activity" count={null}>
                {activity.length === 0 ? (
                  <Text size="sm" c="dimmed" px="lg">Nothing yet. Every opening, page read, download and question shows here.</Text>
                ) : (
                  <Stack gap={4} px="lg">
                    {(allActivity ? activity : activity.slice(0, 8)).map((a) => (
                      <Text key={a.id} size="sm">
                        <Text span c="dimmed" size="xs">{formatDateTime(a.at, me)}</Text>{"  "}
                        <b>{a.who}</b> {a.label}
                        {a.file && <>{a.kind === "question" ? " about" : ""} “{a.file}”</>}
                        {a.kind === "viewed" && a.seconds > 0 && <>, {readingTime(a.seconds)}{a.pages.length ? `, pages ${a.pages.join(", ")}` : ""}</>}
                      </Text>
                    ))}
                    {activity.length > 8 && (
                      <Button size="compact-xs" variant="subtle" w="fit-content" onClick={() => setAllActivity((v) => !v)}>
                        {allActivity ? "Show less" : `Show all ${activity.length}`}
                      </Button>
                    )}
                  </Stack>
                )}
              </Section>
              <Text size="xs" c="dimmed" px="lg">
                Investors open rooms at {data.rooms_domain ?? data.base_url}. Every page they see carries their name, email, the time and a trace code.
              </Text>
            </>
          )}
        </Stack>
      )}

      {data && open === "settings" && <SettingsModal data={data} onClose={() => setOpen(null)} onSaved={refresh} />}
      {data && open === "files" && <FilesModal deal={deal} data={data} onClose={() => setOpen(null)} onSaved={refresh} />}
      {data && open === "share" && <ShareModal deal={deal} data={data} onClose={() => setOpen(null)} onSaved={refresh} />}
      {data && open === "trace" && <TraceModal deal={deal} onClose={() => setOpen(null)} />}
    </Paper>
  );
}

function Section({ title, count, children }: { title: string; count: number | null; children: React.ReactNode }) {
  return (
    <div>
      <Group gap={6} px="lg" mb={6}>
        <Text size="sm" fw={600} c="dimmed">{title}</Text>
        {count !== null && count > 0 && <Badge size="xs" variant="light" color="gray">{count}</Badge>}
      </Group>
      {children}
    </div>
  );
}

function QuestionRow({ q, canAnswer, onDone }: { q: RoomData["questions"][number]; canAnswer: boolean; onDone: (d: RoomData) => void }) {
  const me = useMe();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  async function send() {
    setBusy(true);
    try {
      onDone(await api<RoomData>(`rooms/questions/${q.id}/answer/`, { body: { answer: text } }));
      notifyOk("Answer sent. Only this investor sees it.");
      setText("");
    } catch (e) {
      notifyError(e, "Couldn't send the answer");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Paper withBorder radius="sm" p="sm">
      <Text size="xs" c="dimmed">{q.who}, {formatDateTime(q.asked_at, me)}{q.file ? `, about ${q.file}` : ""}{q.page ? `, page ${q.page}` : ""}</Text>
      <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>{q.text}</Text>
      {q.answered_at ? (
        <Text size="sm" mt={6} pl="sm" style={{ borderLeft: "3px solid var(--mantine-color-gray-3)", whiteSpace: "pre-wrap" }}>
          {q.answer}
          <Text span size="xs" c="dimmed" display="block">{q.answered_by}, {formatDateTime(q.answered_at, me)}</Text>
        </Text>
      ) : canAnswer ? (
        <Group align="flex-end" mt={6} gap="xs">
          <Textarea size="xs" style={{ flex: 1 }} autosize minRows={1} placeholder="Answer, seen only by this investor"
            aria-label={`Answer ${q.who}`} value={text} onChange={(e) => setText(e.currentTarget.value)} />
          <Button size="xs" loading={busy} disabled={text.trim().length < 2} onClick={send}>Send answer</Button>
        </Group>
      ) : <Text size="xs" c="orange" mt={4}>Waiting for an answer from the deal team</Text>}
    </Paper>
  );
}

function SettingsModal({ data, onClose, onSaved }: { data: RoomData; onClose: () => void; onSaved: (d: RoomData) => void }) {
  const r = data.room;
  const [name, setName] = useState(r?.name ?? "");
  const [welcome, setWelcome] = useState(r?.welcome ?? "");
  const [mode, setMode] = useState<string>(r?.nda_mode ?? "click");
  const [nda, setNda] = useState(r?.nda_text ?? "");
  const [isOpen, setIsOpen] = useState(r?.is_open ?? true);
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      const body: Record<string, unknown> = { welcome, nda_mode: mode, is_open: isOpen };
      if (name.trim()) body.name = name;
      if (nda.trim() || mode === "none") body.nda_text = nda;
      onSaved(await api<RoomData>(`deals/${data.deal}/room/`, { method: "PATCH", body }));
      notifyOk("Data room saved.");
      onClose();
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal opened onClose={onClose} title="Room settings" size="lg">
      <Stack>
        {r && <TextInput label="Name investors see" value={name} onChange={(e) => setName(e.currentTarget.value)} maxLength={200} />}
        <Textarea label="Welcome note" description="Shown at the top of the room." autosize minRows={2} maxLength={5000}
          value={welcome} onChange={(e) => setWelcome(e.currentTarget.value)} />
        <div>
          <Text size="sm" fw={500} mb={4}>NDA</Text>
          <SegmentedControl fullWidth value={mode} onChange={setMode}
            data={data.nda_modes.map((m) => ({ value: m.value, label: m.label }))} />
          <Text size="xs" c="dimmed" mt={4}>
            {mode === "none" ? "Investors go straight to the files after their code."
              : mode === "click" ? "Investors type their name and tick to agree before seeing any file. Their name, the time and their address are kept."
                : "The room stays shut until you mark each investor's signed NDA as received (Actions, next to their name)."}
          </Text>
        </div>
        {mode !== "none" && (
          <Textarea label="NDA text" autosize minRows={6} maxRows={14} maxLength={20000}
            value={nda} onChange={(e) => setNda(e.currentTarget.value)} />
        )}
        {r && (
          <Switch label="Room open" description="Closing the room stops every link at once. Opening it again brings them back."
            checked={isOpen} onChange={(e) => setIsOpen(e.currentTarget.checked)} />
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button onClick={save} loading={busy}>Save</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function FilesModal({ deal, data, onClose, onSaved }: { deal: RecordData; data: RoomData; onClose: () => void; onSaved: (d: RoomData) => void }) {
  const { data: vault } = useVaultStatus();
  const about = `deals:${deal.id}`;
  const docs = useQuery({
    queryKey: ["records", "vault", "about", about, vault?.open],
    queryFn: () => api<Page<RecordData>>(`m/vault/${qs({ about, ordering: "-created_at", page_size: 200 })}`),
    enabled: !!vault?.open,
    retry: false,
  });
  const inRoom = new Set(data.files.map((f) => f.document));
  const [picked, setPicked] = useState<Record<number, "view" | "download">>({});
  const [busy, setBusy] = useState(false);
  const back = `/m/deals/${deal.id}`;

  async function add() {
    setBusy(true);
    let last: RoomData | null = null;
    let n = 0;
    for (const [doc, mode] of Object.entries(picked)) {
      try {
        last = await api<RoomData>(`deals/${deal.id}/room/files/`, { body: { document: Number(doc), mode } });
        n += 1;
      } catch (e) {
        notifyError(e, "Couldn't add a file");
      }
    }
    if (last) onSaved(last);
    if (n) notifyOk(`${n} file${n > 1 ? "s" : ""} added. Investors see ${n > 1 ? "them" : "it"} straight away.`);
    setBusy(false);
    if (n === Object.keys(picked).length) onClose();
  }

  const locked = vault && (!vault.open || isVaultLock(docs.error));
  return (
    <Modal opened onClose={onClose} title="Add files to the data room" size="lg">
      <Stack>
        <Text size="sm" c="dimmed">
          Files come from this deal's Vault documents. Investors get a copy with their name across every page. PDFs and
          pictures can be view only; Word, Excel and PowerPoint files are downloads with a hidden trace code (Excel also
          gets their name in the header and footer).
        </Text>
        {!vault ? <Skeleton h={60} /> : !vault.has_pin ? (
          <Text size="sm">Set your Vault PIN first, in the <Anchor component={Link} to="/m/vault">Vault</Anchor>.</Text>
        ) : locked ? (
          <><Text size="sm">The Vault is locked. Enter your PIN to pick files.</Text><UnlockForm compact /></>
        ) : docs.isLoading ? <Skeleton h={60} /> : docs.error ? (
          <Text c="red" size="sm">{errorMessage(docs.error)}</Text>
        ) : (docs.data?.results ?? []).length === 0 ? (
          <Text size="sm">This deal has no Vault documents yet.</Text>
        ) : (
          <Stack gap={6}>
            {(docs.data?.results ?? []).map((d) => {
              const e = ext(String(d.file_name ?? ""));
              const ok = ROOMABLE.has(e);
              const already = inRoom.has(d.id);
              const mode = picked[d.id];
              return (
                <Group key={d.id} justify="space-between" wrap="nowrap" gap="sm">
                  <Checkbox disabled={!ok || already} checked={!!mode || already}
                    label={<><Text size="sm" span fw={500}>{d._title}</Text>{" "}
                      <Text size="xs" c="dimmed" span>{already ? "already in the room" : !ok ? "can't be watermarked" : `${d.file_type}`}</Text></>}
                    onChange={(ev) => setPicked((p) => {
                      const n = { ...p };
                      if (ev.currentTarget.checked) n[d.id] = VIEWABLE.has(e) ? "view" : "download";
                      else delete n[d.id];
                      return n;
                    })} />
                  {mode && (
                    <Select size="xs" w={210} value={mode} allowDeselect={false} aria-label={`What investors can do with ${d._title}`}
                      comboboxProps={{ withinPortal: true }}
                      data={[{ value: "view", label: "View only", disabled: !VIEWABLE.has(e) }, { value: "download", label: "Download with watermark" }]}
                      onChange={(v) => v && setPicked((p) => ({ ...p, [d.id]: v as "view" | "download" }))} />
                  )}
                </Group>
              );
            })}
          </Stack>
        )}
        <Group justify="space-between">
          <Button variant="subtle" component={Link} leftSection={<IconCloudUpload size={14} />}
            to={`/m/vault/new${qs({ tagged: about, return: back })}`}>Upload to the Vault first</Button>
          <Group>
            <Button variant="default" onClick={onClose}>Cancel</Button>
            <Button onClick={add} loading={busy} disabled={!Object.keys(picked).length}>Add to the room</Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}

function ShareModal({ deal, data, onClose, onSaved }: { deal: RecordData; data: RoomData; onClose: () => void; onSaved: (d: RoomData) => void }) {
  const cands = useQuery({
    queryKey: ["room-candidates", deal.id],
    queryFn: () => api<RoomData>(`deals/${deal.id}/room/?candidates=1`).then((d) => d.candidates ?? []),
  });
  const [entry, setEntry] = useState<string | null>(null);
  const [who, setWho] = useState<string>("");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [days, setDays] = useState("30");
  const [busy, setBusy] = useState(false);
  const c: Candidate | undefined = cands.data?.find((x) => String(x.entry) === entry);
  const contact = c?.contacts.find((x) => String(x.id) === who);

  async function share() {
    setBusy(true);
    try {
      const body = contact ? { entry: c!.entry, contact: contact.id, email: contact.email, name: contact.name, days: Number(days) }
        : { entry: Number(entry), email, name, days: Number(days) };
      const d = await api<RoomData>(`deals/${deal.id}/room/shares/`, { body });
      onSaved(d);
      const s = d.shares.find((x) => x.email === body.email);
      notifyOk(s?.status === "waiting" ? "Shared. The link goes out once the first share is approved."
        : s?.invite_error ? `Shared, but the email didn't go: ${s.invite_error}` : `Link sent to ${body.email}.`);
      onClose();
    } catch (e) {
      notifyError(e, "Couldn't share");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal opened onClose={onClose} title="Share with an investor" size="lg">
      <Stack>
        <Text size="sm" c="dimmed">
          They get an email from your mailbox with their own link. Each time they open it, a code is emailed to them,
          so a forwarded link doesn't open for anyone else.
        </Text>
        {!data.mailbox && <Alert color="orange" p="xs"><Text size="sm">Connect your Gmail or Outlook in My preferences first.</Text></Alert>}
        <Select label="Investor on this deal's tracker" placeholder={cands.isLoading ? "Loading…" : "Pick an investor"} searchable
          data={(cands.data ?? []).filter((x) => x.status === "open").map((x) => ({ value: String(x.entry), label: `${x.investor} (${x.stage})` }))}
          value={entry} onChange={(v) => { setEntry(v); setWho(""); }} comboboxProps={{ withinPortal: true }}
          nothingFoundMessage="Add the investor to the tracker first" />
        {c && (
          <Radio.Group label="Person" value={who} onChange={setWho}>
            <Stack gap={6} mt={6}>
              {c.contacts.map((p) => <Radio key={p.id} value={String(p.id)} label={`${p.name}, ${p.email}`} />)}
              <Radio value="other" label="Someone else" />
            </Stack>
          </Radio.Group>
        )}
        {c && who === "other" && (
          <Group grow>
            <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} maxLength={200} />
            <TextInput label="Email" type="email" value={email} onChange={(e) => setEmail(e.currentTarget.value)} maxLength={255} />
          </Group>
        )}
        <Select label="Link works for" data={data.days.map((d) => ({ value: String(d), label: `${d} days` }))} value={days}
          onChange={(v) => v && setDays(v)} allowDeselect={false} w={200} comboboxProps={{ withinPortal: true }} />
        {data.room && !data.room.approved && (
          <Text size="sm" c="orange">The first share on this deal needs approval from {data.room.approvers.join(" or ")}.</Text>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button onClick={share} loading={busy} disabled={!entry || !who || (who === "other" && (!email || !name))}>Share</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function TraceModal({ deal, onClose }: { deal: RecordData; onClose: () => void }) {
  const me = useMe();
  const [file, setFile] = useState<File | null>(null);
  const [code, setCode] = useState("");
  const [found, setFound] = useState<RoomData["activity"] | null>(null);
  const [busy, setBusy] = useState(false);
  async function check() {
    if (!file && !code.trim()) return;
    setBusy(true);
    try {
      const form = new FormData();
      if (file) form.append("file", file);
      else form.append("code", code.trim());
      setFound((await api<{ found: RoomData["activity"] }>(`deals/${deal.id}/room/trace/`, { form })).found);
    } catch (e) {
      notifyError(e, "Couldn't check the file");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal opened onClose={onClose} title="Trace a file" size="md">
      <Stack>
        <Text size="sm" c="dimmed">
          Found one of this deal's documents somewhere it shouldn't be? Choose it here to see whose copy it was. Downloads
          carry a hidden trace code. For a screenshot or photo, type the code printed at the foot of the page.
        </Text>
        <FileInput label="File" placeholder="Choose a file" value={file} onChange={(f) => { setFile(f); setFound(null); }} clearable />
        <TextInput label="Or the trace code" placeholder="DF-…" value={code} disabled={!!file}
          onChange={(e) => { setCode(e.currentTarget.value); setFound(null); }} />
        {found && (found.length === 0 ? (
          <Text size="sm">No trace code from this deal's data room was found in that file.</Text>
        ) : found.map((v) => (
          <Alert key={v.id} color="red" p="xs">
            <Text size="sm"><b>{v.who}</b> {v.label} “{v.file}” on {formatDateTime(v.at, me)} (trace {v.trace}, from {v.ip}).</Text>
          </Alert>
        )))}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Close</Button>
          <Button onClick={check} loading={busy} disabled={!file && !code.trim()}>Check</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
