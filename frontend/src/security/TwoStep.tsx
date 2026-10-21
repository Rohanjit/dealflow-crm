// Two-step login (decision 63, S2): setting it up (required before anything else), managing
// passkeys, the authenticator app and recovery codes, and the list of signed-in devices.

import {
  Alert, Anchor, Badge, Button, Checkbox, Code, CopyButton, Group, Modal, Paper, PasswordInput, PinInput, SimpleGrid,
  Stack, Table, Text, TextInput, ThemeIcon, Title, UnstyledButton,
} from "@mantine/core";
import { modals } from "@mantine/modals";
import { IconDeviceMobile, IconFingerprint, IconKey, IconLogout, IconShieldCheck } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api";
import AuthCard from "../components/AuthCard";
import { errorMessage, fieldErrors, notifyError, notifyOk } from "../components/errors";
import { resetSession, useApp } from "../context";
import { formatDateTime } from "../format";
import { createPasskey, passkeyError, passkeysSupported } from "./webauthn";

type Summary = {
  required: boolean; enrolled: boolean; app: boolean; app_since: string | null;
  passkeys: { id: number; name: string; created_at: string; last_used_at: string | null; synced: boolean }[];
  recovery_left: number;
};
type Device = { id: number; device: string; ip: string; method: string; created_at: string; last_seen: string; current: boolean; new_device: boolean };

const useSummary = () => useQuery({ queryKey: ["mfa"], queryFn: () => api<Summary>("auth/mfa/") });

// ---------------------------------------------------------------- pieces

/** Asks for the password once two-step login is set up (changes need it). */
function usePassword() {
  const [ask, setAsk] = useState<{ title: string; run: (pw: string) => Promise<void> } | null>(null);
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const modal = (
    <Modal opened={!!ask} onClose={() => setAsk(null)} title={ask?.title} size="sm">
      <form onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setErr("");
        try { await ask!.run(pw); setAsk(null); setPw(""); } catch (x) { setErr(fieldErrors(x).password ?? errorMessage(x)); } finally { setBusy(false); }
      }}>
        <Stack>
          <PasswordInput label="Your password" description="Needed to change two-step login." value={pw} autoFocus
            onChange={(e) => setPw(e.currentTarget.value)} error={err || undefined} />
          <Group justify="flex-end"><Button type="submit" loading={busy} disabled={!pw}>Continue</Button></Group>
        </Stack>
      </form>
    </Modal>
  );
  return { modal, ask: (title: string, run: (pw: string) => Promise<void>) => { setErr(""); setPw(""); setAsk({ title, run }); } };
}

export function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [saved, setSaved] = useState(false);
  const text = `Dealflow recovery codes\nEach works once, if your phone or passkey isn't available.\n\n${codes.join("\n")}\n`;
  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "dealflow-recovery-codes.txt";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }
  return (
    <Stack>
      <Text size="sm">
        If you lose your phone or passkey, each of these codes signs you in once. Keep them somewhere safe, like a password manager.
        They won't be shown again.
      </Text>
      <SimpleGrid cols={2} spacing={6} className="df-codes">
        {codes.map((c) => <Code key={c} fz="md" ta="center" py={6}>{c}</Code>)}
      </SimpleGrid>
      <Group gap="xs">
        <CopyButton value={codes.join("\n")}>
          {({ copied, copy }) => <Button variant="default" onClick={copy}>{copied ? "Copied" : "Copy"}</Button>}
        </CopyButton>
        <Button variant="default" onClick={download}>Download</Button>
      </Group>
      <Checkbox label="I've saved these codes" checked={saved} onChange={(e) => setSaved(e.currentTarget.checked)} />
      <Group justify="flex-end"><Button disabled={!saved} onClick={onDone}>Done</Button></Group>
    </Stack>
  );
}

function AppSetup({ password, onDone }: { password?: string; onDone: (r: Summary & { recovery_codes?: string[] }) => void }) {
  const [data, setData] = useState<{ secret: string; uri: string; qr_svg: string } | null>(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<{ secret: string; uri: string; qr_svg: string }>("auth/mfa/app/start/", { body: { password } })
      .then(setData).catch((e) => setErr(errorMessage(e)));
  }, [password]);
  async function confirm(value = code) {
    setBusy(true);
    setErr("");
    try {
      onDone(await api("auth/mfa/app/confirm/", { body: { code: value } }));
    } catch (e) {
      setErr(fieldErrors(e).code ?? errorMessage(e));
      setCode("");
    } finally {
      setBusy(false);
    }
  }
  if (!data) return err ? <Alert color="red">{err}</Alert> : <Text size="sm" c="dimmed">Preparing…</Text>;
  return (
    <Stack>
      <Text size="sm">1. Open your authenticator app (Google or Microsoft Authenticator, 1Password, Authy…) and scan this.</Text>
      {/* The QR picture is SVG made on the server from the sign-up address; no outside service sees it. */}
      <div className="df-qr" dangerouslySetInnerHTML={{ __html: data.qr_svg }} aria-label="QR code to scan" />
      <Text size="xs" c="dimmed">Can't scan? Type this key into the app: <Code>{data.secret.replace(/(.{4})/g, "$1 ").trim()}</Code></Text>
      <Text size="sm">2. Enter the 6-digit code the app shows.</Text>
      <PinInput length={6} type="number" oneTimeCode value={code} onChange={setCode} onComplete={(v) => confirm(v)} aria-label="Code from the app" />
      {err && <Text size="sm" c="red">{err}</Text>}
      <Group><Button onClick={() => confirm()} loading={busy} disabled={code.length < 6}>Confirm</Button></Group>
    </Stack>
  );
}

async function addPasskey(password?: string, name?: string) {
  const options = await api("auth/mfa/passkeys/options/", { body: { password } });
  let credential;
  try {
    credential = await createPasskey(options);
  } catch (e) {
    throw new Error(passkeyError(e));
  }
  return api<Summary & { recovery_codes?: string[] }>("auth/mfa/passkeys/", { body: { credential, name } });
}

// ---------------------------------------------------------------- first set-up (required)

export function MfaSetup() {
  const qc = useQueryClient();
  const { me } = useApp();
  const [mode, setMode] = useState<"choose" | "app">("choose");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const supported = passkeysSupported();

  const finish = async () => { await qc.invalidateQueries({ queryKey: ["app"] }); };

  async function passkey() {
    setBusy(true);
    setErr("");
    try {
      const r = await addPasskey();
      if (r.recovery_codes) setCodes(r.recovery_codes);
      else await finish();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    await api("auth/logout/", { method: "POST" });
    await resetSession(qc);
  }

  return (
    <AuthCard title={codes ? "Save your recovery codes" : "Protect your account"}
      subtitle={codes ? undefined : `${me.full_name.split(" ")[0] || "Hi"}, every Dealflow account signs in with two steps: your password, then your phone or a passkey.`}>
      {codes ? <RecoveryCodes codes={codes} onDone={finish} /> : mode === "app" ? (
        <Stack>
          <AppSetup onDone={(r) => (r.recovery_codes ? setCodes(r.recovery_codes) : finish())} />
          <Anchor component="button" size="sm" onClick={() => setMode("choose")}>Back</Anchor>
        </Stack>
      ) : (
        <Stack>
          <UnstyledButton className="df-choice" onClick={passkey} disabled={!supported || busy} data-disabled={!supported || undefined}>
            <Group wrap="nowrap" align="flex-start">
              <ThemeIcon size={38} radius="md" variant="light"><IconFingerprint size={22} /></ThemeIcon>
              <div>
                <Group gap={6}><Text fw={600}>Use a passkey</Text><Badge size="xs" variant="light" color="teal">Recommended</Badge></Group>
                <Text size="sm" c="dimmed">
                  {supported ? "Face ID, Touch ID, Windows Hello or a security key. Nothing to type, and it can't be phished."
                    : "Not available here: passkeys need Dealflow to be opened over https."}
                </Text>
              </div>
            </Group>
          </UnstyledButton>
          <UnstyledButton className="df-choice" onClick={() => setMode("app")}>
            <Group wrap="nowrap" align="flex-start">
              <ThemeIcon size={38} radius="md" variant="light" color="gray"><IconDeviceMobile size={22} /></ThemeIcon>
              <div>
                <Text fw={600}>Use an authenticator app</Text>
                <Text size="sm" c="dimmed">A 6-digit code from an app on your phone, each time you sign in.</Text>
              </div>
            </Group>
          </UnstyledButton>
          {busy && <Text size="sm" c="dimmed">Follow your browser's prompt…</Text>}
          {err && <Alert color="red">{err}</Alert>}
          <Anchor component="button" size="sm" c="dimmed" onClick={signOut}>Sign out</Anchor>
        </Stack>
      )}
    </AuthCard>
  );
}

// ---------------------------------------------------------------- My preferences

export function TwoStepCard() {
  const { me } = useApp();
  const qc = useQueryClient();
  const { data } = useSummary();
  const pw = usePassword();
  const [appOpen, setAppOpen] = useState<string | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [renaming, setRenaming] = useState<{ id: number; name: string } | null>(null);
  if (!data) return null;
  const refresh = () => qc.invalidateQueries({ queryKey: ["mfa"] });
  const fmt = (s: string | null) => (s ? formatDateTime(s, me) : "never");

  return (
    <Paper withBorder p="lg">
      <Stack>
        <div>
          <Group gap="xs"><Title order={4}>Two-step login</Title>
            <Badge variant="light" color={data.enrolled ? "teal" : "orange"} leftSection={<IconShieldCheck size={12} />}>{data.enrolled ? "On" : "Not set up"}</Badge></Group>
          <Text size="sm" c="dimmed">After your password, Dealflow asks for a passkey or a code from your phone. Keep two ways set up in case you lose one.</Text>
        </div>
        <div>
          <Text fw={600} size="sm" mb={4}>Passkeys</Text>
          {data.passkeys.length === 0 && <Text size="sm" c="dimmed">None yet.</Text>}
          <Stack gap={4}>
            {data.passkeys.map((p) => (
              <Group key={p.id} justify="space-between" className="df-row-plain">
                <Group gap="xs"><IconKey size={16} />
                  <div><Text size="sm" fw={500}>{p.name}{p.synced ? " · synced" : ""}</Text>
                    <Text size="xs" c="dimmed">Added {fmt(p.created_at)} · last used {fmt(p.last_used_at)}</Text></div>
                </Group>
                <Group gap={4}>
                  <Button size="xs" variant="subtle" onClick={() => setRenaming({ id: p.id, name: p.name })}>Rename</Button>
                  <Button size="xs" variant="subtle" color="red" onClick={() => pw.ask(`Remove “${p.name}”?`, async (password) => {
                    await api(`auth/mfa/passkeys/${p.id}/`, { method: "DELETE", body: { password } });
                    await refresh();
                    notifyOk("Passkey removed.");
                  })}>Remove</Button>
                </Group>
              </Group>
            ))}
          </Stack>
          <Button mt="xs" size="xs" variant="default" leftSection={<IconFingerprint size={14} />} disabled={!passkeysSupported()}
            onClick={() => pw.ask("Add a passkey", async (password) => {
              try { await addPasskey(password); } catch (e) { throw e; }
              await refresh();
              notifyOk("Passkey added.");
            })}>
            Add a passkey
          </Button>
          {!passkeysSupported() && <Text size="xs" c="dimmed" mt={4}>Passkeys need Dealflow to be opened over https.</Text>}
        </div>
        <div>
          <Text fw={600} size="sm" mb={4}>Authenticator app</Text>
          <Text size="sm" c="dimmed">{data.app ? `Set up on ${fmt(data.app_since)}.` : "Not set up."}</Text>
          <Group gap="xs" mt="xs">
            <Button size="xs" variant="default" leftSection={<IconDeviceMobile size={14} />}
              onClick={() => pw.ask(data.app ? "Move to a new phone" : "Set up an authenticator app", async (password) => { setAppOpen(password); })}>
              {data.app ? "Move to a new phone" : "Set up an app"}
            </Button>
            {data.app && (
              <Button size="xs" variant="subtle" color="red" onClick={() => pw.ask("Remove the authenticator app?", async (password) => {
                await api("auth/mfa/app/remove/", { body: { password } });
                await refresh();
                notifyOk("Authenticator app removed.");
              })}>Remove</Button>
            )}
          </Group>
        </div>
        <div>
          <Text fw={600} size="sm" mb={4}>Recovery codes</Text>
          <Text size="sm" c={data.recovery_left <= 2 ? "orange" : "dimmed"}>{data.recovery_left} of 10 left.</Text>
          <Button mt="xs" size="xs" variant="default" disabled={!data.enrolled} onClick={() => pw.ask("Make new recovery codes", async (password) => {
            const r = await api<{ recovery_codes: string[] }>("auth/mfa/recovery-codes/", { body: { password } });
            setCodes(r.recovery_codes);
            await refresh();
          })}>Make new codes</Button>
          <Text size="xs" c="dimmed" mt={4}>New codes replace the old ones.</Text>
        </div>
      </Stack>
      {pw.modal}
      <Modal opened={appOpen !== null} onClose={() => setAppOpen(null)} title="Authenticator app">
        {appOpen !== null && <AppSetup password={appOpen} onDone={async (r) => { setAppOpen(null); await refresh(); if (r.recovery_codes) setCodes(r.recovery_codes); notifyOk("Authenticator app set up."); }} />}
      </Modal>
      <Modal opened={!!codes} onClose={() => setCodes(null)} title="Your recovery codes" closeOnClickOutside={false}>
        {codes && <RecoveryCodes codes={codes} onDone={() => setCodes(null)} />}
      </Modal>
      <Modal opened={!!renaming} onClose={() => setRenaming(null)} title="Rename passkey" size="sm">
        {renaming && (
          <Stack>
            <TextInput label="Name" value={renaming.name} maxLength={80} onChange={(e) => setRenaming({ ...renaming, name: e.currentTarget.value })} autoFocus />
            <Group justify="flex-end"><Button disabled={!renaming.name.trim()} onClick={async () => {
              try { await api(`auth/mfa/passkeys/${renaming.id}/`, { method: "PATCH", body: { name: renaming.name } }); await refresh(); setRenaming(null); } catch (e) { notifyError(e); }
            }}>Save</Button></Group>
          </Stack>
        )}
      </Modal>
    </Paper>
  );
}

export function DevicesCard() {
  const { me } = useApp();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["sessions"], queryFn: () => api<Device[]>("auth/sessions/") });
  if (!data) return null;
  const others = data.filter((d) => !d.current).length;
  return (
    <Paper withBorder p="lg">
      <Stack>
        <div>
          <Title order={4}>Signed-in devices</Title>
          <Text size="sm" c="dimmed">Where your account is signed in right now. Don't recognise one? Sign it out and change your password.</Text>
        </div>
        <Table.ScrollContainer minWidth={520} type="native">
          <Table className="df-table" verticalSpacing="xs">
            <Table.Thead><Table.Tr><Table.Th>Device</Table.Th><Table.Th>Address</Table.Th><Table.Th>Signed in</Table.Th><Table.Th>Last active</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>
              {data.map((d) => (
                <Table.Tr key={d.id}>
                  <Table.Td><Text size="sm" fw={500}>{d.device}</Text>{d.current && <Badge size="xs" variant="light" color="teal">This browser</Badge>}</Table.Td>
                  <Table.Td><Text size="sm" c="dimmed">{d.ip}</Text></Table.Td>
                  <Table.Td><Text size="sm" c="dimmed">{formatDateTime(d.created_at, me)}{d.method ? ` · ${d.method}` : ""}</Text></Table.Td>
                  <Table.Td><Text size="sm" c="dimmed">{formatDateTime(d.last_seen, me)}</Text></Table.Td>
                  <Table.Td>{!d.current && (
                    <Button size="xs" variant="subtle" color="red" onClick={async () => {
                      try { await api(`auth/sessions/${d.id}/`, { method: "DELETE" }); await qc.invalidateQueries({ queryKey: ["sessions"] }); notifyOk(`${d.device} signed out.`); } catch (e) { notifyError(e); }
                    }}>Sign out</Button>
                  )}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {others > 0 && (
          <Group><Button variant="default" color="red" leftSection={<IconLogout size={16} />} onClick={() => modals.openConfirmModal({
            title: "Sign out everywhere else?", children: <Text size="sm">Every other browser signed in as you is signed out. This one stays.</Text>,
            labels: { confirm: "Sign out others", cancel: "Cancel" }, confirmProps: { color: "red" },
            onConfirm: async () => {
              const r = await api<{ ended: number }>("auth/sessions/end-others/", { method: "POST" });
              await qc.invalidateQueries({ queryKey: ["sessions"] });
              notifyOk(`${r.ended} signed out.`);
            },
          })}>Sign out everywhere else</Button></Group>
        )}
      </Stack>
    </Paper>
  );
}
