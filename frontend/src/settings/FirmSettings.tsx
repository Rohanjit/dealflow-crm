import { Button, Group, Paper, Stack, Text, TextInput, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api";
import { fieldErrors, notifyError, notifyOk } from "../components/errors";

export default function FirmSettings() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["firm"], queryFn: () => api<{ firm_name: string }>("settings/firm/") });
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setName(data.firm_name); }, [data]);

  async function save() {
    setBusy(true);
    setErr("");
    try {
      await api("settings/firm/", { method: "PATCH", body: { firm_name: name } });
      await qc.invalidateQueries({ queryKey: ["firm"] });
      await qc.invalidateQueries({ queryKey: ["app"] });
      notifyOk("Firm name saved.");
    } catch (e) {
      setErr(fieldErrors(e).firm_name ?? "");
      if (!fieldErrors(e).firm_name) notifyError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Paper withBorder p="lg">
      <Stack maw={440}>
        <Title order={4}>Firm</Title>
        <TextInput label="Firm name" value={name} onChange={(e) => setName(e.currentTarget.value)} error={err} />
        <Text size="xs" c="dimmed">Shown on the sign-in page and in the header.</Text>
        <Group><Button onClick={save} loading={busy} disabled={!data}>Save</Button></Group>
      </Stack>
    </Paper>
  );
}
