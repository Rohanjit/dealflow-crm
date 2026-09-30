// The Vault's PIN screens. Everything in the Vault sits behind this: set a PIN the first time, enter
// it to open the Vault, and see when a lock-out ends.

import { Alert, Button, Center, Group, Loader, Paper, PasswordInput, Stack, Text, ThemeIcon, Title } from "@mantine/core";
import { IconLock, IconLockOpen } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { api } from "../api";
import { errorMessage, fieldErrors } from "../components/errors";
import { afterVaultChange, useVaultStatus, type VaultStatus } from "./api";

export default function VaultGate({ children }: { children: ReactNode }) {
  const { data, isLoading, error } = useVaultStatus();
  if (isLoading) return <Center py="xl"><Loader /></Center>;
  if (error || !data) return <Text c="red">{errorMessage(error)}</Text>;
  if (!data.can_view) return <Text c="dimmed">Your role can't open the Vault.</Text>;
  if (!data.has_pin) return <Card title="Set your Vault PIN" icon={<IconLock size={22} />}><SetPinForm /></Card>;
  if (data.locked_out_until) return <Card title="The Vault is locked" icon={<IconLock size={22} />}><LockedOut until={data.locked_out_until} /></Card>;
  if (!data.open) return <Card title="The Vault is locked" icon={<IconLock size={22} />}><UnlockForm /></Card>;
  return <>{children}</>;
}

function Card({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <Center py={48}>
      <Paper withBorder radius="md" p="xl" w="100%" maw={420}>
        <Stack gap="md">
          <Group gap="sm">
            <ThemeIcon variant="light" size="xl" radius="xl">{icon}</ThemeIcon>
            <Title order={3}>{title}</Title>
          </Group>
          {children}
        </Stack>
      </Paper>
    </Center>
  );
}

function LockedOut({ until }: { until: string }) {
  const at = new Date(until);
  return (
    <Text size="sm">
      Too many wrong PINs. Try again after {at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.
      If you've forgotten your PIN, ask someone who manages users to reset it.
    </Text>
  );
}

/** Enter the PIN to open the Vault. Also used inline on record pages. */
export function UnlockForm({ compact = false }: { compact?: boolean }) {
  const qc = useQueryClient();
  const [pin, setPin] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const s = await api<VaultStatus>("vault/unlock/", { body: { pin } });
      await afterVaultChange(qc, s);
    } catch (ex) {
      setErr(fieldErrors(ex).pin ?? errorMessage(ex));
      setPin("");
      await qc.invalidateQueries({ queryKey: ["vault-status"] });
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit}>
      <Stack gap="sm">
        {!compact && <Text size="sm" c="dimmed">Enter your Vault PIN. The Vault stays open until you've not used it for 15 minutes.</Text>}
        <Group gap="xs" align="flex-start" wrap="nowrap">
          <PasswordInput style={{ flex: 1 }} maw={compact ? 220 : undefined} placeholder="PIN" aria-label="Vault PIN" inputMode="numeric"
            autoComplete="off" autoFocus={!compact} value={pin} onChange={(e) => setPin(e.currentTarget.value.replace(/\D/g, "").slice(0, 8))}
            error={err || undefined} />
          <Button type="submit" loading={busy} disabled={pin.length < 4} leftSection={<IconLockOpen size={16} />}>Open</Button>
        </Group>
      </Stack>
    </form>
  );
}

/** First PIN (with the account password) or a new one (with the current PIN). */
export function SetPinForm({ changing = false, onDone }: { changing?: boolean; onDone?: () => void }) {
  const qc = useQueryClient();
  const [v, setV] = useState({ pin: "", repeat: "", password: "", current_pin: "" });
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const digits = (s: string) => s.replace(/\D/g, "").slice(0, 8);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (v.pin !== v.repeat) {
      setErrs({ repeat: "The two PINs don't match." });
      return;
    }
    setBusy(true);
    setErrs({});
    try {
      const body = changing ? { pin: v.pin, current_pin: v.current_pin } : { pin: v.pin, password: v.password };
      const s = await api<VaultStatus>("vault/pin/", { body });
      await afterVaultChange(qc, s);
      setV({ pin: "", repeat: "", password: "", current_pin: "" });
      onDone?.();
    } catch (ex) {
      const fe = fieldErrors(ex);
      setErrs(Object.keys(fe).length ? fe : { detail: errorMessage(ex) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit}>
      <Stack gap="sm">
        {!changing && (
          <Text size="sm" c="dimmed">
            The Vault has a second lock: a PIN of 4 to 8 digits that only you know. You'll enter it to open the Vault; it
            stays open until you've not used it for 15 minutes.
          </Text>
        )}
        {changing && (
          <PasswordInput label="Current PIN" inputMode="numeric" autoComplete="off" value={v.current_pin}
            onChange={(e) => setV({ ...v, current_pin: digits(e.currentTarget.value) })} error={errs.current_pin} maw={240} />
        )}
        <PasswordInput label={changing ? "New PIN" : "PIN"} inputMode="numeric" autoComplete="off" value={v.pin}
          onChange={(e) => setV({ ...v, pin: digits(e.currentTarget.value) })} error={errs.pin} maw={240} />
        <PasswordInput label="Repeat the PIN" inputMode="numeric" autoComplete="off" value={v.repeat}
          onChange={(e) => setV({ ...v, repeat: digits(e.currentTarget.value) })} error={errs.repeat} maw={240} />
        {!changing && (
          <PasswordInput label="Your account password" description="To confirm it's you." autoComplete="current-password"
            value={v.password} onChange={(e) => setV({ ...v, password: e.currentTarget.value })} error={errs.password} maw={320} />
        )}
        {errs.detail && <Alert color="red" p="xs">{errs.detail}</Alert>}
        <Group>
          <Button type="submit" loading={busy} disabled={v.pin.length < 4 || v.repeat.length < 4}>
            {changing ? "Change PIN" : "Set PIN and open the Vault"}
          </Button>
        </Group>
      </Stack>
    </form>
  );
}
