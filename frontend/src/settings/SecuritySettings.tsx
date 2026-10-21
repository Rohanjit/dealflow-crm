// Settings > Security (admins). Grows with each phase of the security programme (decision 63).

import { Button, Group, List, Paper, PasswordInput, Select, Stack, Text, ThemeIcon, Title } from "@mantine/core";
import { IconCheck } from "@tabler/icons-react";
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
];

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
      <Paper withBorder p="lg">
        <Title order={5} mb="sm">Already in place</Title>
        <List spacing={6} size="sm" icon={<ThemeIcon size={18} radius="xl" color="teal" variant="light"><IconCheck size={12} /></ThemeIcon>}>
          {IN_PLACE.map((t) => <List.Item key={t}>{t}</List.Item>)}
        </List>
      </Paper>
    </Stack>
  );
}
