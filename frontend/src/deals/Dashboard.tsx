// Home page snapshot of Active Deals: how many deals sit at each stage, which are near their target
// close date, and which mandates are nearing expiry. The look-ahead window is set in Settings > Firm.

import {
  Anchor, Badge, Box, Button, Grid, Group, Paper, SimpleGrid, Stack, Text, Tooltip, UnstyledButton,
} from "@mantine/core";
import { IconAlertTriangle, IconBriefcase, IconClockHour4 } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { errorMessage } from "../components/errors";
import { CountUp, Skeleton } from "../components/ui";
import { useMe } from "../context";
import { formatDate, formatMoney } from "../format";
import type { Money } from "../types";

type DealRow = {
  id: number; title: string; subtitle: string; stage: string; status: string;
  target_close_date: string | null; mandate_expiry: string | null;
  lead_banker: { id: number; name: string } | null; target_raise: Money;
};
type Snapshot = {
  days: number;
  today: string;
  stages: { value: string; label: string; count: number }[];
  groups: { label: string; stages: string[] }[];
  exits: { value: string; label: string; count: number }[];
  active: number;
  near_close: DealRow[];
  expiring: DealRow[];
};

const BAR = "var(--mantine-color-navy-6)"; // one series, one colour (validated against the page surface)

export default function DealsDashboard() {
  const me = useMe();
  const nav = useNavigate();
  const { data, isLoading, error, isFetching } = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Snapshot>("dashboard/") });

  if (isLoading) {
    return (
      <Stack gap="sm" className="df-fade">
        <Skeleton w={160} h={20} />
        <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">{[0, 1, 2, 3].map((i) => <Skeleton key={i} h={70} r={8} />)}</SimpleGrid>
      </Stack>
    );
  }
  if (error || !data) return <Paper withBorder p="lg" radius="md"><Text c="red" size="sm">{errorMessage(error)}</Text></Paper>;

  const count = (v: string) => data.exits.find((e) => e.value === v)?.count ?? 0;
  const closed = data.stages[data.stages.length - 1];
  const tiles = [
    { label: "Active", value: data.active, to: "/m/deals" },
    { label: "On hold", value: count("on_hold"), to: "/m/deals?view=on_hold" },
    { label: "Closed", value: closed?.count ?? 0, to: "/m/deals?view=closed" },
    { label: "Withdrawn or dropped", value: count("withdrawn") + count("dropped"), to: "/m/deals?view=ended" },
  ];
  const open = data.stages.slice(0, -1); // Closed has its own tile
  const max = Math.max(1, ...open.map((s) => s.count));
  const phases = data.groups.length
    ? data.groups.map((g) => ({ label: g.label, stages: open.filter((s) => g.stages.includes(s.value)) }))
    : [{ label: "", stages: open }];

  return (
    <Box style={{ opacity: isFetching ? 0.7 : 1, transition: "opacity 150ms" }}>
      <Group justify="space-between" mb="md" align="flex-start">
        <Group gap="sm">
          <div>
            <Group gap={8}><IconBriefcase size={18} stroke={1.7} color="var(--mantine-color-gray-7)" /><Text fw={600}>Active deals</Text></Group>
            <Text size="xs" c="dimmed">
              Dates in the next {data.days} days
              {me.flags.manage_settings && <> · <Anchor component={Link} to="/settings/firm" size="xs">change</Anchor></>}
            </Text>
          </div>
        </Group>
        <Button size="compact-sm" variant="subtle" color="gray" component={Link} to="/m/deals">Open active deals →</Button>
      </Group>

      <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm" mb="lg">
        {tiles.map((t) => (
          <UnstyledButton key={t.label} component={Link} to={t.to} className="df-tile">
            <Text size="xs" c="dimmed">{t.label}</Text>
            <Text fw={650} fz={28} lh={1.15}><CountUp value={t.value} /></Text>
          </UnstyledButton>
        ))}
      </SimpleGrid>

      <Grid gutter="xl">
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Text fw={600} size="sm">Active deals by stage</Text>
          <Text size="xs" c="dimmed" mb="sm">Deals on hold are counted in their own tile. Click a stage to see its deals.</Text>
          <Stack gap="md">
            {phases.map((ph) => (
              <div key={ph.label || "all"}>
                {ph.label && <Text size="xs" fw={600} c="dimmed" mb={4}>{ph.label}</Text>}
                <Stack gap={2}>
                  {ph.stages.map((s) => (
                    <Tooltip key={s.value} label={`${s.count} ${s.count === 1 ? "deal" : "deals"} at ${s.label}`} position="top-start"
                      events={{ hover: true, focus: true, touch: false }}>
                      <UnstyledButton className="df-bar-row" onClick={() => nav(`/m/deals?stage=${s.value}`)}
                        aria-label={`${s.label}: ${s.count} ${s.count === 1 ? "deal" : "deals"}`}>
                        <Text size="sm" w={150} style={{ flexShrink: 0 }} truncate>{s.label}</Text>
                        <Box className="df-bar-track">
                          {s.count > 0 && <Box className="df-bar" style={{ width: `${(s.count / max) * 100}%`, background: BAR }} />}
                        </Box>
                        <Text size="sm" fw={600} w={28} ta="right" style={{ fontVariantNumeric: "tabular-nums" }}>{s.count}</Text>
                      </UnstyledButton>
                    </Tooltip>
                  ))}
                </Stack>
              </div>
            ))}
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Stack gap="lg">
            <DealList title="Near target close date" empty={`No open deals have a target close date in the next ${data.days} days.`}
              rows={data.near_close} today={data.today} dateKey="target_close_date" prefs={me} kind="close" />
            <DealList title="Mandates nearing expiry" empty={`No mandates expire in the next ${data.days} days.`}
              rows={data.expiring} today={data.today} dateKey="mandate_expiry" prefs={me} kind="expiry" />
          </Stack>
        </Grid.Col>
      </Grid>
    </Box>
  );
}

function DealList({
  title, empty, rows, today, dateKey, prefs, kind,
}: {
  title: string; empty: string; rows: DealRow[]; today: string; dateKey: "target_close_date" | "mandate_expiry";
  prefs: { date_format: string; number_grouping: string }; kind: "close" | "expiry";
}) {
  return (
    <div>
      <Group gap="xs" mb={6}>
        <Text fw={600} size="sm">{title}</Text>
        <Badge size="sm" variant="light" color="gray">{rows.length}</Badge>
      </Group>
      {rows.length === 0 ? (
        <Text size="sm" c="dimmed">{empty}</Text>
      ) : (
        <Stack gap={0}>
          {rows.map((r) => {
            const date = r[dateKey]!;
            const days = dayjs(date).diff(dayjs(today), "day");
            const late = days < 0;
            const when = kind === "close"
              ? (late ? `${-days} ${-days === 1 ? "day" : "days"} overdue` : days === 0 ? "today" : `in ${days} ${days === 1 ? "day" : "days"}`)
              : (late ? `expired ${-days} ${-days === 1 ? "day" : "days"} ago` : days === 0 ? "expires today" : `expires in ${days} ${days === 1 ? "day" : "days"}`);
            const tone = late ? "red" : days <= 7 ? "orange" : "gray";
            return (
              <Group key={r.id} justify="space-between" wrap="nowrap" py={8} gap="sm"
                style={{ borderTop: "1px solid var(--mantine-color-gray-2)" }}>
                <div style={{ minWidth: 0 }}>
                  <Anchor component={Link} to={`/m/deals/${r.id}`} size="sm" fw={600}>{r.title}</Anchor>
                  <Text size="xs" c="dimmed" truncate>
                    {[r.subtitle, formatMoney(r.target_raise, prefs), r.lead_banker?.name].filter(Boolean).join(" · ")}
                  </Text>
                </div>
                <Stack gap={2} align="flex-end" style={{ flexShrink: 0 }}>
                  <Text size="sm" style={{ fontVariantNumeric: "tabular-nums" }}>{formatDate(date, prefs)}</Text>
                  <Badge size="sm" variant="light" color={tone} tt="none" fw={500}
                    leftSection={late ? <IconAlertTriangle size={11} /> : <IconClockHour4 size={11} />}>{when}</Badge>
                </Stack>
              </Group>
            );
          })}
        </Stack>
      )}
    </div>
  );
}
