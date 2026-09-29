import { Button, Group, NumberInput, Paper, Stack, Text, TextInput, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api";
import { fieldErrors, notifyError, notifyOk } from "../components/errors";

type Firm = { firm_name: string; dashboard_days: number };

export default function FirmSettings() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["firm"], queryFn: () => api<Firm>("settings/firm/") });
  const [name, setName] = useState("");
  const [days, setDays] = useState<number | string>(30);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (data) {
      setName(data.firm_name);
      setDays(data.dashboard_days);
    }
  }, [data]);

  async function save() {
    setBusy(true);
    setErrs({});
    try {
      await api("settings/firm/", { method: "PATCH", body: { firm_name: name, dashboard_days: days === "" ? null : days } });
      await qc.invalidateQueries({ queryKey: ["firm"] });
      await qc.invalidateQueries({ queryKey: ["app"] });
      await qc.invalidateQueries({ queryKey: ["dashboard"] });
      notifyOk("Firm settings saved.");
    } catch (e) {
      const fe = fieldErrors(e);
      setErrs(fe);
      if (!Object.keys(fe).length) notifyError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Paper withBorder p="lg">
      <Stack maw={440}>
        <Title order={4}>Firm</Title>
        <div>
          <TextInput label="Firm name" value={name} onChange={(e) => setName(e.currentTarget.value)} error={errs.firm_name} />
          <Text size="xs" c="dimmed" mt={4}>Shown on the sign-in page and in the header.</Text>
        </div>
        <NumberInput label="Home page look-ahead (days)" min={1} max={365} allowDecimal={false} w={220}
          description="Deals whose target close date, or mandates whose validity, ends within this many days show on the home page."
          value={days} onChange={setDays} error={errs.dashboard_days} />
        <Group><Button onClick={save} loading={busy} disabled={!data}>Save</Button></Group>
      </Stack>
    </Paper>
  );
}
