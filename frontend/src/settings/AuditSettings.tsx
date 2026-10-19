// Settings > Audit log (admins only). The log can't be browsed in the app: it's kept encrypted on
// the server and fetched here as an Excel file, with your password and the audit passphrase.

import { Alert, Anchor, Button, Divider, Group, Modal, Paper, PasswordInput, Select, Stack, Text, Title } from "@mantine/core";
import { DatePickerInput } from "@mantine/dates";
import { IconDownload, IconLock, IconShieldCheck } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useState } from "react";

import { api, ApiError, postDownload } from "../api";
import { errorMessage, fieldErrors, notifyOk } from "../components/errors";
import { useMe } from "../context";
import { dayjsFormat } from "../format";

type Status = {
  has_passphrase: boolean; since: string | null; months: number; size_bytes: number;
  keep_years: number | null; temporary_since: string | null; write_errors: boolean;
  min_passphrase: number; max_days: number;
};

const size = (b: number) => (b < 1024 * 1024 ? `${Math.max(1, Math.round(b / 1024))} KB` : `${(b / 1024 / 1024).toFixed(1)} MB`);

export default function AuditSettings() {
  const qc = useQueryClient();
  const { data, error } = useQuery({ queryKey: ["audit"], queryFn: () => api<Status>("audit/") });
  if (error) return <Paper withBorder p="lg"><Text c="red">{errorMessage(error)}</Text></Paper>;
  if (!data) return null;
  const refresh = (s: Status) => qc.setQueryData(["audit"], { ...data, ...s });
  return (
    <Stack>
      <Paper withBorder p="lg">
        <Stack maw={620}>
          <Group gap="xs"><IconShieldCheck size={20} /><Title order={4}>Audit log</Title></Group>
          <Text size="sm" c="dimmed">
            Every request to Dealflow is recorded: who, when (to the second), what they did and what changed, with the old and new
            values. The log is encrypted on the server and can't be browsed in the app. Admins fetch a date range here as an
            Excel file, with their password and the audit passphrase. Passwords, PINs and passphrases are never recorded.
          </Text>
          {!data.has_passphrase ? (
            <Alert color="orange" icon={<IconLock size={18} />} title="Set the audit passphrase">
              The log is already recording, sealed with a temporary key on the server. Set a passphrase to lock it so that only
              someone who knows the passphrase can read it. Keep the passphrase somewhere safe: if every admin forgets it, the log
              can never be read.
            </Alert>
          ) : (
            <Text size="sm">
              Recording since {data.since ? dayjs(`${data.since}-01`).format("MMMM YYYY") : "today"} · {data.months} {data.months === 1 ? "month" : "months"} · {size(data.size_bytes)} on the server
            </Text>
          )}
          {data.write_errors && <Alert color="red">Some entries couldn't be written. Check the server's disk space, and the errors.txt file in the audit folder.</Alert>}
        </Stack>
      </Paper>
      {!data.has_passphrase ? <SetPassphrase min={data.min_passphrase} onDone={refresh} /> : (
        <>
          <Fetch status={data} />
          <Keep status={data} onDone={refresh} />
          <ChangePassphrase min={data.min_passphrase} onDone={refresh} />
        </>
      )}
    </Stack>
  );
}

function SetPassphrase({ min, onDone }: { min: number; onDone: (s: Status) => void }) {
  const [pw, setPw] = useState("");
  const [p1, setP1] = useState("");
  const [p2, setP2] = useState("");
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  async function save() {
    if (p1 !== p2) return setErrs({ repeat: "The two passphrases don't match." });
    setBusy(true); setErrs({});
    try {
      onDone(await api<Status>("audit/passphrase/", { body: { password: pw, passphrase: p1 } }));
      notifyOk("Audit passphrase set. The log is now locked with it.");
    } catch (e) { setErrs(fieldErrors(e)); if (!Object.keys(fieldErrors(e)).length) setErrs({ passphrase: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  return (
    <Paper withBorder p="lg">
      <Stack maw={420}>
        <Title order={5}>Set the audit passphrase</Title>
        <PasswordInput label="Your account password" value={pw} onChange={(e) => setPw(e.currentTarget.value)} error={errs.password} />
        <PasswordInput label="Audit passphrase" description={`At least ${min} characters. A few unrelated words work well.`}
          value={p1} onChange={(e) => setP1(e.currentTarget.value)} error={errs.passphrase} />
        <PasswordInput label="Type it again" value={p2} onChange={(e) => setP2(e.currentTarget.value)} error={errs.repeat} />
        <Group><Button onClick={save} loading={busy} disabled={!pw || !p1 || !p2}>Set passphrase</Button></Group>
      </Stack>
    </Paper>
  );
}

function Fetch({ status }: { status: Status }) {
  const me = useMe();
  const today = dayjs().startOf("day").toDate();
  const [range, setRange] = useState<[Date | null, Date | null]>([dayjs().subtract(7, "day").toDate(), today]);
  const [pw, setPw] = useState("");
  const [pass, setPass] = useState("");
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  async function fetchLog() {
    if (!range[0] || !range[1]) return;
    setBusy(true); setErrs({}); setResult("");
    try {
      const res = await postDownload("audit/fetch/", {
        from: dayjs(range[0]).format("YYYY-MM-DD"), to: dayjs(range[1]).format("YYYY-MM-DD"), password: pw, passphrase: pass,
      });
      const problems = Number(res.headers.get("X-Audit-Problems") ?? 0);
      setResult(`${res.headers.get("X-Audit-Entries")} entries downloaded. ${problems ? `The chain check found ${problems} problems: see the "About this file" sheet.` : "Chain check: nothing missing or changed."}`);
      setPass("");
    } catch (e) {
      const fe = fieldErrors(e);
      setErrs(Object.keys(fe).length ? fe : { passphrase: e instanceof ApiError ? e.message : errorMessage(e) });
    } finally { setBusy(false); }
  }
  return (
    <Paper withBorder p="lg">
      <Stack maw={420}>
        <Title order={5}>Fetch the log</Title>
        <Text size="sm" c="dimmed">Up to {status.max_days} days at a time. The download is itself recorded in the log.</Text>
        <DatePickerInput type="range" label="Days" value={range} onChange={setRange} maxDate={today} valueFormat={dayjsFormat(me)}
          error={errs.from || errs.to} allowSingleDateInRange />
        <PasswordInput label="Your account password" value={pw} onChange={(e) => setPw(e.currentTarget.value)} error={errs.password} />
        <PasswordInput label="Audit passphrase" value={pass} onChange={(e) => setPass(e.currentTarget.value)} error={errs.passphrase} />
        <Group><Button leftSection={<IconDownload size={16} />} onClick={fetchLog} loading={busy} disabled={!pw || !pass || !range[0] || !range[1]}>Download as Excel</Button></Group>
        {result && <Alert color="teal" variant="light">{result}</Alert>}
      </Stack>
    </Paper>
  );
}

function Keep({ status, onDone }: { status: Status; onDone: (s: Status) => void }) {
  const [years, setYears] = useState<string>(status.keep_years ? String(status.keep_years) : "forever");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const changed = years !== (status.keep_years ? String(status.keep_years) : "forever");
  async function save() {
    setBusy(true); setErr("");
    try {
      onDone(await api<Status>("audit/settings/", { method: "PATCH", body: { keep_years: years === "forever" ? null : Number(years), password: pw } }));
      notifyOk("Saved."); setPw("");
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  }
  return (
    <Paper withBorder p="lg">
      <Stack maw={420}>
        <Title order={5}>How long to keep it</Title>
        <Select label="Keep the log for" value={years} onChange={(v) => setYears(v ?? "forever")} allowDeselect={false}
          data={[{ value: "forever", label: "Forever" }, ...[1, 2, 3, 5, 7, 8, 10, 15, 20].map((n) => ({ value: String(n), label: `${n} ${n === 1 ? "year" : "years"}` }))]}
          description="Older months are deleted automatically each night. Months that have ended are compressed." />
        {changed && <PasswordInput label="Your account password" value={pw} onChange={(e) => setPw(e.currentTarget.value)} error={err} />}
        {changed && <Group><Button onClick={save} loading={busy} disabled={!pw}>Save</Button></Group>}
      </Stack>
    </Paper>
  );
}

function ChangePassphrase({ min, onDone }: { min: number; onDone: (s: Status) => void }) {
  const [open, setOpen] = useState<"change" | "lost" | null>(null);
  const [pw, setPw] = useState("");
  const [cur, setCur] = useState("");
  const [p1, setP1] = useState("");
  const [p2, setP2] = useState("");
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const close = () => { setOpen(null); setPw(""); setCur(""); setP1(""); setP2(""); setErrs({}); };
  async function save() {
    if (p1 !== p2) return setErrs({ repeat: "The two passphrases don't match." });
    setBusy(true); setErrs({});
    try {
      const s = open === "change"
        ? await api<Status>("audit/passphrase/", { body: { password: pw, current_passphrase: cur, passphrase: p1 } })
        : await api<Status>("audit/new-key/", { body: { password: pw, passphrase: p1, confirm: true } });
      onDone(s);
      notifyOk(open === "change" ? "Audit passphrase changed." : "New key started. Entries from now on use the new passphrase.");
      close();
    } catch (e) { const fe = fieldErrors(e); setErrs(Object.keys(fe).length ? fe : { passphrase: errorMessage(e) }); }
    finally { setBusy(false); }
  }
  return (
    <Paper withBorder p="lg">
      <Stack maw={620} gap="xs">
        <Title order={5}>Passphrase</Title>
        <Group>
          <Button variant="default" onClick={() => setOpen("change")}>Change the passphrase</Button>
          <Anchor size="sm" c="red" component="button" onClick={() => setOpen("lost")}>Forgot it? Start a new key</Anchor>
        </Group>
      </Stack>
      <Modal opened={open !== null} onClose={close} title={<Text fw={600}>{open === "change" ? "Change the audit passphrase" : "Start a new key"}</Text>}>
        <Stack>
          {open === "lost" && (
            <Alert color="red" variant="light">
              Only do this if nobody knows the passphrase. Everything recorded so far stays on the server but can never be read
              again. New entries are locked with the new passphrase.
            </Alert>
          )}
          <PasswordInput label="Your account password" value={pw} onChange={(e) => setPw(e.currentTarget.value)} error={errs.password} />
          {open === "change" && <PasswordInput label="Current audit passphrase" value={cur} onChange={(e) => setCur(e.currentTarget.value)} error={errs.current_passphrase} />}
          <Divider />
          <PasswordInput label="New audit passphrase" description={`At least ${min} characters.`} value={p1} onChange={(e) => setP1(e.currentTarget.value)} error={errs.passphrase} />
          <PasswordInput label="Type it again" value={p2} onChange={(e) => setP2(e.currentTarget.value)} error={errs.repeat} />
          <Group justify="flex-end">
            <Button variant="default" onClick={close}>Cancel</Button>
            <Button color={open === "lost" ? "red" : undefined} onClick={save} loading={busy} disabled={!pw || !p1 || !p2 || (open === "change" && !cur)}>
              {open === "change" ? "Change passphrase" : "Start a new key"}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Paper>
  );
}
