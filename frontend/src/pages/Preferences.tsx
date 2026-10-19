import { Button, Group, Paper, Radio, Stack, Text, TextInput, Title } from "@mantine/core";
import { useState } from "react";

import { api } from "../api";
import { useApp } from "../context";
import { notifyError, notifyOk } from "../components/errors";
import { formatDate, groupDigits } from "../format";
import { useVaultStatus } from "../vault/api";
import { SetPinForm } from "../vault/VaultGate";
import ChangePassword from "./ChangePassword";
import { IconUserCircle } from "@tabler/icons-react";
import { PageHeader } from "../components/ui";

export default function Preferences() {
  const { info, me, refresh } = useApp();
  const [v, setV] = useState({ full_name: me.full_name, date_format: me.date_format, number_grouping: me.number_grouping });
  const [busy, setBusy] = useState(false);
  const today = new Date().toISOString().slice(0, 10);

  async function save() {
    setBusy(true);
    try {
      await api("auth/me/", { method: "PATCH", body: v });
      await refresh();
      notifyOk("Preferences saved.");
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack maw={720} gap="lg">
      <PageHeader icon={IconUserCircle} title="My preferences" sub="How dates and numbers look to you, and your Vault PIN." />
      <Paper withBorder p="lg">
        <Stack>
          <TextInput label="Name" value={v.full_name} onChange={(e) => setV({ ...v, full_name: e.currentTarget.value })} maw={360} />
          <Radio.Group label="Date format" value={v.date_format} onChange={(d) => setV({ ...v, date_format: d as typeof v.date_format })}>
            <Group mt="xs">
              {(["DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD"] as const).map((f) => (
                <Radio key={f} value={f} label={`${f} (${formatDate(today, { date_format: f, number_grouping: "" })})`} />
              ))}
            </Group>
          </Radio.Group>
          <Radio.Group label="Number grouping" value={v.number_grouping} onChange={(g) => setV({ ...v, number_grouping: g as typeof v.number_grouping })}>
            <Group mt="xs">
              <Radio value="indian" label={`Indian (${groupDigits("123456789", "indian")})`} />
              <Radio value="international" label={`International (${groupDigits("123456789", "international")})`} />
            </Group>
          </Radio.Group>
          <Text size="xs" c="dimmed">These only change how you see dates and numbers. Everyone else keeps their own settings.</Text>
          <Group><Button onClick={save} loading={busy}>Save preferences</Button></Group>
        </Stack>
      </Paper>
      {!info.is_guest && <ChangePassword />}
      {me.perms.vault?.view && !info.is_guest && <VaultPin />}
    </Stack>
  );
}

function VaultPin() {
  const { data } = useVaultStatus();
  const [done, setDone] = useState(false);
  if (!data) return null;
  return (
    <Paper withBorder p="lg">
      <Stack>
        <Title order={4}>Vault PIN</Title>
        {done ? (
          <Text size="sm">Your Vault PIN is {data.has_pin ? "changed" : "set"}.</Text>
        ) : (
          <SetPinForm changing={data.has_pin} onDone={() => setDone(true)} />
        )}
        {data.has_pin && !done && (
          <Text size="xs" c="dimmed">Forgotten it? Ask someone who manages users to reset it; you then set a new one.</Text>
        )}
      </Stack>
    </Paper>
  );
}
