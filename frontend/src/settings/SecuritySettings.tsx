// Settings > Security (admins). Grows with each phase of the security programme (decision 63).

import {
  Alert, Anchor, Button, Checkbox, Code, CopyButton, Group, List, Modal, Paper, PasswordInput, Select, Stack, Text, TextInput,
  ThemeIcon, Title,
} from "@mantine/core";
import { IconAlertTriangle, IconCheck } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api";
import { errorMessage, fieldErrors, notifyOk } from "../components/errors";
import { Skeleton } from "../components/ui";

type Sec = { idle_minutes: number; absolute_hours: number };

const IDLE = [15, 30, 60, 120, 240, 480].map((m) => ({
  value: String(m), label: m < 60 ? `${m} minutes` : m === 60 ? "1 hour" : `${m / 60} hours`,
}));

const IN_PLACE = [
  "Passwords of at least 12 characters, checked against common ones, never one of your last 12, stored with Argon2id.",
  "Five wrong passwords lock an account for 30 minutes, from anywhere. Nobody can tell from outside which accounts exist.",
  "Everyone signs in again after 12 hours, and when the browser closes.",
  "Strict browser protections on every page: no outside scripts, no framing by other sites, nothing cached.",
  "Invite links work once, expire after 72 hours and are stored scrambled.",
  "Every action is written to an encrypted, tamper-evident audit log that only admins with the passphrase can read.",
  "Two-step login for everyone: passkeys or an authenticator app, with recovery codes.",
  "Fields can be hidden from or made read-only for each role.",
  "The database, Vault files, backups and files waiting to be emailed are encrypted, with the keys kept apart from them.",
];

type VaultKeys = {
  encrypted: boolean; files_waiting: number; people_with_access: number; people_waiting: number; has_recovery_key: boolean;
  key_since: string | null; holding_key: boolean;
  storage: { keys_dir: string; separate: boolean; db_encrypted: boolean; backup_key_id: string | null };
};

export default function SecuritySettings() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["security-settings"], queryFn: () => api<Sec>("settings/security/") });
  const [idle, setIdle] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setIdle(String(data.idle_minutes)); }, [data]);
  if (isLoading) return <Skeleton h={200} />;
  if (error || !data) return <Text c="red">{errorMessage(error)}</Text>;

  async function save() {
    setBusy(true);
    setErrors({});
    try {
      await api("settings/security/", { method: "PUT", body: { idle_minutes: Number(idle), password } });
      await qc.invalidateQueries({ queryKey: ["security-settings"] });
      await qc.invalidateQueries({ queryKey: ["app"] });
      setPassword("");
      notifyOk("Security settings saved.");
    } catch (e) {
      setErrors({ ...fieldErrors(e), ...(Object.keys(fieldErrors(e)).length ? {} : { password: errorMessage(e) }) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack maw={760} gap="lg">
      <div>
        <Title order={4}>Security</Title>
        <Text size="sm" c="dimmed">How Dealflow protects your firm's data, and the settings you control.</Text>
      </div>
      <Paper withBorder p="lg">
        <Stack gap="sm">
          <Title order={5}>Sign out when idle</Title>
          <Text size="sm" c="dimmed">
            Anyone who leaves Dealflow untouched for this long is signed out. A warning appears a minute before, and typing counts as activity.
          </Text>
          <Select label="Sign out after" data={IDLE} value={idle} onChange={setIdle} w={220} allowDeselect={false} error={errors.idle_minutes} />
          <Group align="flex-end" gap="sm">
            <PasswordInput label="Your password" value={password} onChange={(e) => setPassword(e.currentTarget.value)} w={260} error={errors.password} />
            <Button onClick={save} loading={busy} disabled={!password || idle === String(data.idle_minutes)}>Save</Button>
          </Group>
        </Stack>
      </Paper>
      <Encryption />
      <Paper withBorder p="lg">
        <Title order={5} mb="sm">Already in place</Title>
        <List spacing={6} size="sm" icon={<ThemeIcon size={18} radius="xl" color="teal" variant="light"><IconCheck size={12} /></ThemeIcon>}>
          {IN_PLACE.map((t) => <List.Item key={t}>{t}</List.Item>)}
        </List>
      </Paper>
    </Stack>
  );
}


function Encryption() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["vault-keys"], queryFn: () => api<VaultKeys>("vault/keys/") });
  const [ask, setAsk] = useState<null | { title: string; body: string; run: (pw: string) => Promise<void> }>(null);
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [recovery, setRecovery] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [useRec, setUseRec] = useState(false);
  if (!data) return null;
  const refresh = () => qc.invalidateQueries({ queryKey: ["vault-keys"] });
  const ok = (good: boolean, text: string) => (
    <Group gap={8} wrap="nowrap" align="flex-start">
      <ThemeIcon size={18} radius="xl" variant="light" color={good ? "teal" : "orange"}>{good ? <IconCheck size={12} /> : <IconAlertTriangle size={12} />}</ThemeIcon>
      <Text size="sm">{text}</Text>
    </Group>
  );
  const s = data.storage;
  return (
    <Paper withBorder p="lg">
      <Stack gap="sm">
        <Title order={5}>Encryption and keys</Title>
        {ok(s.db_encrypted, s.db_encrypted ? "The database is encrypted (SQLCipher, AES-256)." : "The database file isn't encrypted by Dealflow: on Postgres, encrypt the disk it lives on (see docs/postgres-encryption.md).")}
        {ok(true, `Backups are encrypted (AES-256-GCM)${s.backup_key_id ? `, key ${s.backup_key_id}` : ""}. Download the key from Backups and keep it apart.`)}
        {ok(s.separate, s.separate ? `Keys are kept apart from the data, in ${s.keys_dir}.` : `Keys are in ${s.keys_dir}, inside the data folder. Set DEALFLOW_KEYS_DIR to a separate disk or volume.`)}
        {ok(data.encrypted, data.encrypted ? `Vault files are encrypted with the firm's Vault key, which each person's PIN unlocks.` : "Vault files are encrypted when someone first opens the Vault with their PIN.")}
        {data.encrypted && (
          <>
            <Text size="sm" c="dimmed">
              {data.people_with_access} {data.people_with_access === 1 ? "person holds" : "people hold"} the Vault key
              {data.people_waiting ? `; ${data.people_waiting} waiting for it (they get it when anyone with access opens the Vault)` : ""}.
              {data.files_waiting ? ` ${data.files_waiting} older files are encrypted as the Vault is used.` : ""}
            </Text>
            {!data.has_recovery_key && (
              <Alert color="orange" variant="light" icon={<IconAlertTriangle size={16} />}>
                No recovery key yet. If everyone with Vault access forgets their PIN, or the server is lost, Vault files can't be opened.
                Make one and keep it offline.
              </Alert>
            )}
            {!data.holding_key && (
              <Text size="sm">To manage the Vault key, <Anchor component={Link} to="/m/vault" size="sm">open the Vault</Anchor> with your PIN first.</Text>
            )}
            <Group gap="xs">
              {data.people_waiting > 0 && (
                <Button size="xs" variant="default" disabled={!data.holding_key} onClick={async () => {
                  try { await api("vault/keys/grant/", { method: "POST" }); await refresh(); notifyOk("Access given."); } catch (e) { setErr(errorMessage(e)); }
                }}>Give access now</Button>
              )}
              <Button size="xs" variant="default" disabled={!data.holding_key} onClick={() => { setErr(""); setPw(""); setAsk({
                title: data.has_recovery_key ? "Make a new recovery key" : "Make a recovery key",
                body: "It's shown once. Print it or keep it in a password manager, away from the server. The old one stops working.",
                run: async (p) => { const r = await api<{ recovery_key: string }>("vault/keys/recovery-key/", { body: { password: p } }); setRecovery(r.recovery_key); setSaved(false); await refresh(); },
              }); }}>{data.has_recovery_key ? "Make a new recovery key" : "Make a recovery key"}</Button>
              <Button size="xs" variant="default" color="red" disabled={!data.holding_key} onClick={() => { setErr(""); setPw(""); setAsk({
                title: "Change the Vault key?",
                body: "Every Vault file is encrypted again with a new key, and everyone with access gets it. Do this if you think the key was exposed. The recovery key has to be made again afterwards.",
                run: async (p) => { const r = await api<{ files: number }>("vault/keys/rotate/", { body: { password: p } }); await refresh(); notifyOk(`Vault key changed; ${r.files} files encrypted again.`); },
              }); }}>Change the Vault key</Button>
              <Button size="xs" variant="subtle" onClick={() => setUseRec(true)}>Use a recovery key</Button>
            </Group>
            {err && <Text size="sm" c="red">{err}</Text>}
          </>
        )}
      </Stack>
      <Modal opened={!!ask} onClose={() => setAsk(null)} title={ask?.title}>
        {ask && (
          <form onSubmit={async (e) => { e.preventDefault(); try { await ask.run(pw); setAsk(null); } catch (x) { setErr(errorMessage(x)); } }}>
            <Stack>
              <Text size="sm">{ask.body}</Text>
              <PasswordInput label="Your password" value={pw} onChange={(e) => setPw(e.currentTarget.value)} autoFocus error={err || undefined} />
              <Group justify="flex-end"><Button type="submit" disabled={!pw}>Continue</Button></Group>
            </Stack>
          </form>
        )}
      </Modal>
      <Modal opened={!!recovery} onClose={() => saved && setRecovery(null)} withCloseButton={saved} closeOnClickOutside={false} title="Vault recovery key">
        {recovery && (
          <Stack>
            <Text size="sm">This opens the Vault if every PIN is forgotten or the server is lost. It won't be shown again.</Text>
            <Code block fz="md" ta="center">{recovery}</Code>
            <Group><CopyButton value={recovery}>{({ copied, copy }) => <Button variant="default" onClick={copy}>{copied ? "Copied" : "Copy"}</Button>}</CopyButton></Group>
            <Checkbox label="I've stored it somewhere safe, away from this server" checked={saved} onChange={(e) => setSaved(e.currentTarget.checked)} />
            <Group justify="flex-end"><Button disabled={!saved} onClick={() => setRecovery(null)}>Done</Button></Group>
          </Stack>
        )}
      </Modal>
      {useRec && <UseRecovery onClose={() => { setUseRec(false); refresh(); }} />}
    </Paper>
  );
}

function UseRecovery({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState("");
  const [pin, setPin] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal opened onClose={onClose} title="Use the Vault recovery key">
      <Stack>
        <Text size="sm">When nobody here can open Vault files (after moving to a new server, or if every PIN is lost). You'll hold the Vault key again, and colleagues get it as soon as you use the Vault.</Text>
        <TextInput label="Recovery key" placeholder="XXXX-XXXX-…" value={text} onChange={(e) => setText(e.currentTarget.value)} />
        <PasswordInput label="Your Vault PIN" value={pin} onChange={(e) => setPin(e.currentTarget.value.replace(/\D/g, "").slice(0, 8))} />
        {err && <Text size="sm" c="red">{err}</Text>}
        <Group justify="flex-end">
          <Button loading={busy} disabled={!text || pin.length < 4} onClick={async () => {
            setBusy(true); setErr("");
            try { await api("vault/keys/recover/", { body: { recovery_key: text, pin } }); notifyOk("Vault opened with the recovery key."); onClose(); }
            catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
          }}>Open the Vault</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
