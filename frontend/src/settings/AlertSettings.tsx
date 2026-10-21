// Settings > Alerts: when deals and outreach count as stuck, and which follow-up nudges people get.

import { Badge, Button, Group, NumberInput, Paper, ScrollArea, Stack, Switch, Table, Text, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api";
import { fieldErrors, notifyError, notifyOk } from "../components/errors";
import { PageSkeleton } from "../components/ui";
import type { StageRule } from "../insights/api";

type Settings = {
  stuck_alerts: boolean; nudge_meeting_task: boolean; nudge_tracker_days: number; nudge_quiet_days: number;
  stuck_overrides: Record<string, Record<string, number>>; rules: Record<string, StageRule[]>; min_samples: number;
};
const PIPELINES = [["outreach", "Outreach"], ["deals", "Active Deals"]] as const;

export default function AlertSettings() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["alert-settings"], queryFn: () => api<Settings>("settings/alerts/") });
  const [stuckOn, setStuckOn] = useState(true);
  const [meeting, setMeeting] = useState(true);
  const [tracker, setTracker] = useState<number | string>(10);
  const [quiet, setQuiet] = useState<number | string>(14);
  const [over, setOver] = useState<Record<string, Record<string, number | string>>>({});
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!data) return;
    setStuckOn(data.stuck_alerts);
    setMeeting(data.nudge_meeting_task);
    setTracker(data.nudge_tracker_days);
    setQuiet(data.nudge_quiet_days);
    setOver(data.stuck_overrides);
  }, [data]);
  if (!data) return <PageSkeleton rows={8} />;

  async function save() {
    setBusy(true);
    setErrs({});
    try {
      const clean = Object.fromEntries(PIPELINES.map(([m]) => [m, Object.fromEntries(
        Object.entries(over[m] ?? {}).filter(([, v]) => v !== "" && v != null))]));
      await api("settings/alerts/", { method: "PUT", body: {
        stuck_alerts: stuckOn, nudge_meeting_task: meeting, stuck_overrides: clean,
        nudge_tracker_days: tracker === "" ? 0 : tracker, nudge_quiet_days: quiet === "" ? 0 : quiet,
      } });
      await Promise.all(["alert-settings", "insight-alerts", "stage-rules", "analytics", "records"].map((k) => qc.invalidateQueries({ queryKey: [k] })));
      notifyOk("Alert settings saved.");
    } catch (e) {
      const fe = fieldErrors(e);
      setErrs(fe);
      if (!Object.keys(fe).length) notifyError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack gap="lg">
      <Paper withBorder p="lg">
        <Stack gap="md">
          <div>
            <Title order={4}>Stuck deals and outreach</Title>
            <Text size="sm" c="dimmed" maw={680}>
              A deal or outreach attempt is stuck when it has been in one stage longer than your firm usually takes there.
              Dealflow learns “usually” from your own history once a stage has {data.min_samples} finished stays, and flags
              records after half as long again. Until then it uses the starting points below. Type a number to set a stage
              yourself (0 = never flag it), or leave it blank to let Dealflow decide.
            </Text>
          </div>
          <Switch checked={stuckOn} onChange={(e) => setStuckOn(e.currentTarget.checked)}
            label="Remind the lead banker and deal team about stuck records"
            description="The Stuck button on the Outreach and Active Deals lists works either way." />
          {PIPELINES.map(([m, label]) => (
            <div key={m}>
              <Text fw={600} size="sm" mb={6}>{label}</Text>
              <ScrollArea type="auto">
                <Table className="df-db" miw={620} verticalSpacing={6}>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Stage</Table.Th>
                      <Table.Th>Usually at your firm</Table.Th>
                      <Table.Th>Flagged after</Table.Th>
                      <Table.Th w={170}>Set it yourself (days)</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {data.rules[m].map((r) => {
                      const v = over[m]?.[r.stage] ?? "";
                      const auto = r.learned ?? r.default;
                      return (
                        <Table.Tr key={r.stage}>
                          <Table.Td><Text size="sm">{r.label}</Text></Table.Td>
                          <Table.Td>
                            <Text size="sm" c={r.median == null ? "dimmed" : undefined}>
                              {r.samples >= data.min_samples && r.median != null
                                ? `${Math.round(r.median)} days (from ${r.samples} stays)`
                                : `Not enough history yet (${r.samples} of ${data.min_samples})`}
                            </Text>
                          </Table.Td>
                          <Table.Td>
                            <Group gap={6} wrap="nowrap">
                              <Text size="sm" style={{ fontVariantNumeric: "tabular-nums" }}>{r.days ? `${r.days} days` : "Never"}</Text>
                              <Badge size="xs" variant="light" color={r.source === "learned" ? "teal" : r.source === "set" ? "navy" : "gray"}>
                                {r.source === "learned" ? "Learned" : r.source === "set" ? "Set by you" : "Starting point"}
                              </Badge>
                            </Group>
                          </Table.Td>
                          <Table.Td>
                            <NumberInput size="xs" placeholder={`Automatic (${auto})`} min={0} max={730} allowDecimal={false} allowNegative={false}
                              clampBehavior="none" aria-label={`${label}, ${r.label}: days before flagged`} value={v}
                              error={errs[`${m}.${r.stage}`]}
                              onChange={(x) => setOver((o) => ({ ...o, [m]: { ...(o[m] ?? {}), [r.stage]: x } }))} />
                          </Table.Td>
                        </Table.Tr>
                      );
                    })}
                  </Table.Tbody>
                </Table>
              </ScrollArea>
            </div>
          ))}
        </Stack>
      </Paper>

      <Paper withBorder p="lg">
        <Stack gap="md" maw={560}>
          <div>
            <Title order={4}>Follow-up nudges</Title>
            <Text size="sm" c="dimmed">Shown under the notification bell to the people on the record. Each one can be snoozed for 7 days.</Text>
          </div>
          <Switch checked={meeting} onChange={(e) => setMeeting(e.currentTarget.checked)}
            label="A meeting or call was logged but no follow-up task was added"
            description="Shown to whoever logged it, two hours later, for up to two weeks." />
          <NumberInput label="Tracker investors not moving (days)" w={300} min={0} max={365} allowDecimal={false} allowNegative={false}
            clampBehavior="none" value={tracker} onChange={setTracker} error={errs.nudge_tracker_days}
            description="Investors on a deal's tracker that haven't changed stage or had a log entry for this long. 0 = off." />
          <NumberInput label="No activity on a deal or outreach attempt (days)" w={300} min={0} max={365} allowDecimal={false}
            allowNegative={false} clampBehavior="none" value={quiet} onChange={setQuiet} error={errs.nudge_quiet_days}
            description="No edits, log entries or tracker moves for this long. 0 = off." />
        </Stack>
      </Paper>
      <Group><Button onClick={save} loading={busy}>Save</Button></Group>
    </Stack>
  );
}
