// Capture contacts (decision 64): check a person picked up by the browser add-on, from someone's email,
// or pasted from a signature, then add them (or update who's already here) and their firm in one step.

import {
  Alert, Anchor, Badge, Button, Checkbox, Group, Paper, Select, SimpleGrid, Stack, Text, Textarea, TextInput, Title,
} from "@mantine/core";
import { IconBrandLinkedin, IconMail, IconPuzzle, IconUserPlus } from "@tabler/icons-react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../api";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import { PageHeader } from "../components/ui";
import { useMe, useModuleSchema } from "../context";
import { formatDate } from "../format";

type Values = Record<string, string>;
type Firm = { module: "investors" | "companies"; id: number; title: string; why?: string };
type Check = {
  existing: null | { id: number; title: string; why: string; firm: { title: string } | null;
    changes: { field: string; label: string; from: string; to: string }[] };
  others: { id: number; title: string }[];
  firms: Firm[];
  company: string;
  website: string;
  can: { create: boolean; edit: boolean; new_firm: { investors: boolean; companies: boolean } };
};
type Suggestion = { id: number; email: string; name: string; count: number; last_seen: string; last_subject: string;
  wrote_to_them: boolean; firm: Firm[] };

const FIELDS: [string, string][] = [
  ["full_name", "Full name"], ["designation", "Designation"], ["work_email", "Work email"], ["mobile", "Mobile"],
  ["office_phone", "Office phone"], ["linkedin", "LinkedIn"], ["city", "City"],
];
const WHY: Record<string, string> = { email: "same email", mobile: "same mobile", linkedin: "same LinkedIn profile", name: "same name at the same firm" };

/** Data the browser add-on puts after # (never sent to the server as part of the address). */
function fromHash(hash: string): { values: Values; text?: string; source?: string } | null {
  const m = hash.match(/[#&]d=([^&]+)/);
  if (!m) return null;
  try {
    const b64 = m[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = new TextDecoder().decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)));
    const d = JSON.parse(json);
    const values: Values = {};
    for (const [k, v] of Object.entries(d.values ?? {})) if (typeof v === "string") values[k] = v.slice(0, 300);
    return { values, text: typeof d.text === "string" ? d.text.slice(0, 20000) : undefined, source: d.source };
  } catch {
    return null;
  }
}

export default function CapturePage() {
  const loc = useLocation();
  const [params, setParams] = useSearchParams();
  const [start, setStart] = useState<{ values: Values; source?: string } | null>(null);
  const [problem, setProblem] = useState("");
  const suggestion = params.get("suggestion");

  useEffect(() => {
    const h = fromHash(loc.hash);
    if (h) {
      window.history.replaceState(null, "", loc.pathname + loc.search); // drop the data from the address bar
      if (h.text) {
        api<Values>("capture/parse/", { body: { text: h.text, name: h.values.full_name ?? "", email: h.values.work_email ?? "" } })
          .then((v) => setStart({
            values: { ...v, ...Object.fromEntries(Object.entries(h.values).filter(([, x]) => x)) }, source: h.source,
          }))
          .catch((e) => setProblem(errorMessage(e)));
      } else {
        setStart({ values: h.values, source: h.source });
      }
    }
  }, [loc.hash, loc.pathname, loc.search]);

  useEffect(() => {
    if (!suggestion) return;
    setProblem("");
    api<Values>(`capture/suggestions/${suggestion}/prefill/`)
      .then((v) => setStart({ values: v, source: "email" }))
      .catch((e) => setProblem(errorMessage(e)));
  }, [suggestion]);

  return (
    <Stack gap="lg" maw={980}>
      <PageHeader icon={IconUserPlus} title="Add people" sub="From your email, a LinkedIn profile or a pasted signature. You check everything before it's saved." />
      {problem && <Alert color="red">{problem}</Alert>}
      {start ? (
        <Review key={JSON.stringify(start)} initial={start.values} source={start.source}
          onDone={() => { setStart(null); setParams({}); }} />
      ) : (
        <>
          <Suggestions />
          <Paste onRead={(v) => setStart({ values: v, source: "paste" })} />
          <AddOn />
        </>
      )}
    </Stack>
  );
}

function Suggestions() {
  const me = useMe();
  const qc = useQueryClient();
  const nav = useNavigate();
  const { data } = useQuery({ queryKey: ["capture-suggestions"], queryFn: () => api<{ people: Suggestion[]; count: number }>("capture/suggestions/") });
  async function dismiss(id: number) {
    try {
      await api(`capture/suggestions/${id}/dismiss/`, { body: {} });
      await qc.invalidateQueries({ queryKey: ["capture-suggestions"] });
    } catch (e) {
      notifyError(e);
    }
  }
  return (
    <Paper withBorder p="lg">
      <Stack gap="sm">
        <div>
          <Title order={5}>People you've emailed who aren't in Dealflow</Title>
          <Text size="sm" c="dimmed">
            From your connected mailbox. Only you see this list. Newsletters, automated senders and colleagues are left out.
          </Text>
        </div>
        {!data ? null : data.people.length === 0 ? (
          <Text size="sm" c="dimmed">
            Nobody new right now. {" "}
            <Anchor component={Link} to="/settings/preferences" size="sm">Connect your email</Anchor> if you haven't yet; people you
            email from then on will show here.
          </Text>
        ) : (
          <Stack gap={0}>
            {data.people.map((p) => (
              <Group key={p.id} justify="space-between" wrap="nowrap" py={8} style={{ borderTop: "1px solid var(--mantine-color-gray-2)" }}>
                <div style={{ minWidth: 0 }}>
                  <Group gap={6} wrap="nowrap">
                    <Text size="sm" fw={500} truncate>{p.name || p.email}</Text>
                    {p.firm[0] && <Badge size="xs" variant="light">{p.firm[0].title}</Badge>}
                  </Group>
                  <Text size="xs" c="dimmed" truncate>
                    {p.email} · {p.count} {p.count === 1 ? "email" : "emails"}{p.wrote_to_them ? ", you've written to them" : ""} · last {formatDate(dayjs(p.last_seen).format("YYYY-MM-DD"), me)}
                    {p.last_subject ? ` · “${p.last_subject}”` : ""}
                  </Text>
                </div>
                <Group gap={6} wrap="nowrap">
                  <Button size="xs" variant="light" onClick={() => nav(`/capture?suggestion=${p.id}`)}>Add</Button>
                  <Button size="xs" variant="subtle" color="gray" onClick={() => dismiss(p.id)}>Not now</Button>
                </Group>
              </Group>
            ))}
          </Stack>
        )}
      </Stack>
    </Paper>
  );
}

function Paste({ onRead }: { onRead: (v: Values) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  async function read() {
    setBusy(true);
    try {
      onRead(await api<Values>("capture/parse/", { body: { text } }));
    } catch (e) {
      notifyError(e, "Couldn't read it");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Paper withBorder p="lg">
      <Stack gap="sm">
        <Title order={5}>Paste a signature or business card text</Title>
        <Textarea autosize minRows={4} maxRows={12} placeholder={"Meera Rao\nPrincipal | Larkspur Capital\nM: +91 90000 00012\nmeera@larkspur.example"}
          value={text} onChange={(e) => setText(e.currentTarget.value)} />
        <Group><Button variant="default" onClick={read} loading={busy} disabled={!text.trim()}>Read the details</Button></Group>
      </Stack>
    </Paper>
  );
}

function AddOn() {
  return (
    <Paper withBorder p="lg">
      <Stack gap="sm">
        <Group gap="xs"><IconPuzzle size={18} /><Title order={5}>Browser add-on for LinkedIn and webmail</Title></Group>
        <Text size="sm" c="dimmed">
          Click the Dealflow button while looking at someone's LinkedIn profile, or at an email in Gmail or Outlook on the web,
          and they open here, ready to check and save. It reads only the page you're on when you click, one person at a time,
          and sends nothing anywhere except to this Dealflow.
        </Text>
        <Group gap="xs">
          <Button variant="default" leftSection={<IconBrandLinkedin size={16} />} component="a" href="/addon/dealflow-capture.zip" download>
            Download for Chrome and Edge
          </Button>
          <Button variant="default" component="a" href="/addon/dealflow-capture-firefox.zip" download>Download for Firefox</Button>
        </Group>
        <Text size="xs" c="dimmed">
          Chrome or Edge: unzip it, open chrome://extensions (or edge://extensions), turn on Developer mode, choose “Load unpacked” and pick the folder.
          Firefox: open about:debugging, “This Firefox”, “Load Temporary Add-on”. Then click the add-on's button once and enter this Dealflow's address: {window.location.origin}
        </Text>
      </Stack>
    </Paper>
  );
}

function Review({ initial, source, onDone }: { initial: Values; source?: string; onDone: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const { module } = useModuleSchema("contacts");
  const editable = useMemo(() => new Set((module?.fields ?? []).filter((f) => f.enabled && !f.readonly).map((f) => f.key)), [module]);
  const [v, setV] = useState<Values>({ ...initial });
  const [company, setCompany] = useState(initial.company ?? "");
  const [website] = useState(initial.website ?? "");
  const [firm, setFirm] = useState<string | null>(null);
  const [apply, setApply] = useState<Set<string> | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [probe, setProbe] = useState(v);
  useEffect(() => { const t = setTimeout(() => setProbe(v), 400); return () => clearTimeout(t); }, [v]);
  const { data: check } = useQuery({
    queryKey: ["capture-check", probe, company, website],
    queryFn: () => api<Check>("capture/check/", { body: { ...probe, company, website } }),
    placeholderData: keepPreviousData,
  });
  const existing = check?.existing;
  const chosen = apply ?? new Set(existing?.changes.map((c) => c.field) ?? []);

  const firmOptions = [
    ...(check?.firms ?? []).map((f) => ({ value: `${f.module}:${f.id}`, label: `${f.title} (${f.module === "investors" ? "investor" : "company"}${f.why ? `, ${f.why.toLowerCase()}` : ""})` })),
    ...(company && check?.can.new_firm.investors ? [{ value: "new:investors", label: `Add “${company}” as a new investor` }] : []),
    ...(company && check?.can.new_firm.companies ? [{ value: "new:companies", label: `Add “${company}” as a new company` }] : []),
    { value: "none", label: "No firm" },
  ];
  const firmValue = firm ?? firmOptions[0]?.value ?? "none";

  async function save() {
    setBusy(true);
    setProblem("");
    try {
      if (existing) {
        const body: Values = {};
        for (const c of existing.changes) if (chosen.has(c.field) && editable.has(c.field)) body[c.field] = c.to;
        await api(`m/contacts/${existing.id}/`, { method: "PATCH", body });
        notifyOk(`${existing.title} updated.`);
        await qc.invalidateQueries({ queryKey: ["records", "contacts"] });
        nav(`/m/contacts/${existing.id}`);
        return;
      }
      let firmRef: string | null = null;
      if (firmValue.startsWith("new:")) {
        const f = await api<Firm>("capture/firm/", { body: { kind: firmValue.slice(4), name: company, website } });
        firmRef = `${f.module}:${f.id}`;
      } else if (firmValue !== "none") {
        firmRef = firmValue;
      }
      const body: Record<string, string | null> = {};
      for (const [k] of FIELDS) if (v[k]?.trim() && editable.has(k)) body[k] = v[k].trim();
      if (firmRef && editable.has("firm")) body.firm = firmRef;
      const rec = await api<{ id: number }>("m/contacts/", { body });
      await qc.invalidateQueries({ queryKey: ["records", "contacts"] });
      await qc.invalidateQueries({ queryKey: ["capture-suggestions"] });
      notifyOk(`${v.full_name} added.`);
      nav(`/m/contacts/${rec.id}`);
    } catch (e) {
      setProblem(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const from = source === "linkedin" ? "LinkedIn" : source === "gmail" || source === "outlook" || source === "email" ? "an email" : "what you pasted";
  return (
    <Paper withBorder p="lg">
      <Stack>
        <Group gap="xs">
          {source === "linkedin" ? <IconBrandLinkedin size={18} /> : <IconMail size={18} />}
          <Title order={5}>Check before saving</Title>
          <Text size="sm" c="dimmed">Read from {from}. Change anything that's wrong.</Text>
        </Group>
        {existing && (
          <Alert color="blue" title={`${existing.title} is already in Dealflow (${WHY[existing.why] ?? "looks the same"})`}>
            {existing.changes.length ? (
              <Stack gap={4}>
                <Text size="sm">Tick what to update:</Text>
                {existing.changes.map((c) => (
                  <Checkbox key={c.field} checked={chosen.has(c.field)} disabled={!editable.has(c.field)}
                    onChange={(e) => { const n = new Set(chosen); if (e.currentTarget.checked) n.add(c.field); else n.delete(c.field); setApply(n); }}
                    label={<Text size="sm">{c.label}: <Text span c="dimmed" td="line-through">{c.from || "empty"}</Text> → <b>{c.to}</b></Text>} />
                ))}
              </Stack>
            ) : <Text size="sm">Nothing new to add. <Anchor component={Link} to={`/m/contacts/${existing.id}`} size="sm">Open {existing.title}</Anchor></Text>}
          </Alert>
        )}
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          {FIELDS.filter(([k]) => editable.has(k) || !module).map(([k, label]) => (
            <TextInput key={k} label={label} value={v[k] ?? ""} required={k === "full_name"}
              onChange={(e) => { const val = e.currentTarget.value; setV((o) => ({ ...o, [k]: val })); }} />
          ))}
        </SimpleGrid>
        {!existing && editable.has("firm") && (
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <TextInput label="Firm name (as read)" value={company} onChange={(e) => setCompany(e.currentTarget.value)} />
            <Select label="Firm in Dealflow" data={firmOptions} value={firmValue} onChange={setFirm} allowDeselect={false}
              description="Matched by website and name. Pick one, add it, or leave it out." />
          </SimpleGrid>
        )}
        {problem && <Text c="red" size="sm">{problem}</Text>}
        <Group justify="flex-end">
          <Button variant="default" onClick={onDone}>Cancel</Button>
          {existing ? (
            <Button onClick={save} loading={busy} disabled={!check?.can.edit || !existing.changes.some((c) => chosen.has(c.field))}>
              Update {existing.title}
            </Button>
          ) : (
            <Button leftSection={<IconUserPlus size={16} />} onClick={save} loading={busy} disabled={!check?.can.create || !v.full_name?.trim()}>
              Add contact
            </Button>
          )}
        </Group>
      </Stack>
    </Paper>
  );
}
