// Insights: how the firm wins mandates and closes deals, from its own records (decision 59). Win
// rates count decided records only and always say how many they rest on. Per-banker numbers only
// for people with "See team performance".

import {
  Anchor, Badge, Box, Group, Paper, ScrollArea, SegmentedControl, Select, SimpleGrid, Stack, Table, Text, Title, Tooltip, UnstyledButton,
} from "@mantine/core";
import { useState } from "react";
import { Link, Navigate } from "react-router-dom";

import { errorMessage } from "../components/errors";
import { CountUp, PageHeader, PageSkeleton, moduleIcon } from "../components/ui";
import { useMe } from "../context";
import { useAnalytics, type Analytics, type Breakdown, type FunnelStep, type StageRule } from "../insights/api";

const BAR = "var(--mantine-color-navy-6)"; // one series, one colour (as on the home page)
const PERIODS = [
  { value: "3", label: "3 months" }, { value: "6", label: "6 months" }, { value: "12", label: "12 months" },
  { value: "24", label: "2 years" }, { value: "all", label: "All time" },
];

const pct = (r: number | null | undefined) => (r == null ? "—" : `${Math.round(r * 100)}%`);
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export default function Insights() {
  const me = useMe();
  const [period, setPeriod] = useState("12");
  const { data, isLoading, isFetching, error } = useAnalytics(period);
  if (!me.perms.outreach?.view && !me.perms.deals?.view) return <Navigate to="/" replace />;

  return (
    <Stack gap="xl" maw={1100}>
      <PageHeader icon={moduleIcon("insights")} title="Insights"
        sub="How your firm wins mandates and closes deals, worked out from your own records."
        actions={<Choice value={period} onChange={setPeriod} data={PERIODS} label="Period" />} />
      {isLoading ? <PageSkeleton rows={8} /> : error || !data ? <Text c="red">{errorMessage(error)}</Text> : (
        <Stack gap={48} style={{ opacity: isFetching ? 0.7 : 1, transition: "opacity 150ms" }}>
          <Text size="sm" c="dimmed" mt={-24}>
            {data.since ? `Outreach started, and deals mandated, since ${new Date(data.since).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}.` : "Everything on record."}
            {" "}Win rates only count decided records, so ones still open don't pull them down.
          </Text>
          {data.outreach && <OutreachSection o={data.outreach} />}
          {data.deals && <DealsSection d={data.deals} />}
          {data.investors && <InvestorsSection inv={data.investors} />}
          <StagesSection stages={data.stages} canEdit={me.flags.manage_settings} />
          <BankersSection data={data} />
        </Stack>
      )}
    </Stack>
  );
}

/** A row of choices; a dropdown on phones, where the row wouldn't fit. */
function Choice({ value, onChange, data, label }: { value: string; onChange: (v: string) => void; data: { value: string; label: string }[]; label: string }) {
  return (
    <>
      <SegmentedControl visibleFrom="sm" value={value} onChange={onChange} data={data} aria-label={label} style={{ alignSelf: "flex-start" }} />
      <Select hiddenFrom="sm" value={value} onChange={(v) => v && onChange(v)} data={data} aria-label={label} allowDeselect={false} w={220} />
    </>
  );
}

function Section({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <Stack gap="md" className="df-fade">
      <div>
        <Title order={3}>{title}</Title>
        {sub && <Text size="sm" c="dimmed">{sub}</Text>}
      </div>
      {children}
    </Stack>
  );
}

function Tile({ label, value, note, to }: { label: string; value: number | string; note?: string; to?: string }) {
  const inner = (
    <>
      <Text size="xs" c="dimmed">{label}</Text>
      <Text fw={650} fz={28} lh={1.15}>{typeof value === "number" ? <CountUp value={value} /> : value}</Text>
      {note && <Text size="xs" c="dimmed">{note}</Text>}
    </>
  );
  return to
    ? <UnstyledButton component={Link} to={to} className="df-tile">{inner}</UnstyledButton>
    : <div className="df-tile">{inner}</div>;
}

function Funnel({ steps, noun }: { steps: FunnelStep[]; noun: string }) {
  const max = Math.max(1, steps[0]?.reached ?? 1);
  return (
    <Stack gap={2}>
      {steps.map((s, i) => (
        <Tooltip key={s.stage} position="top-start" events={{ hover: true, focus: true, touch: false }}
          label={`${plural(s.reached, noun)} reached ${s.label}${s.from_previous != null ? ` · ${pct(s.from_previous)} of those at ${steps[i - 1].label}` : ""}`}>
          <div className="df-bar-row" tabIndex={0}>
            <Text size="sm" w={{ base: 112, sm: 170 }} style={{ flexShrink: 0 }} truncate>{s.label}</Text>
            <Box className="df-bar-track">
              {s.reached > 0 && <Box className="df-bar" style={{ width: `${(s.reached / max) * 100}%`, background: BAR }} />}
            </Box>
            <Text size="sm" fw={600} w={36} ta="right" className="df-count">{s.reached}</Text>
            <Text size="xs" c="dimmed" w={{ base: 40, sm: 64 }} ta="right" className="df-count">{i === 0 ? "" : pct(s.from_previous)}</Text>
          </div>
        </Tooltip>
      ))}
      <Text size="xs" c="dimmed" mt={4}>“Reached” means at that stage or past it, including ones that were lost or dropped there. The right-hand figure is the share that got there from the stage before.</Text>
    </Stack>
  );
}

function RateCell({ row, won, lost }: { row: Breakdown; won: string; lost: string }) {
  const decided = row.won + row.lost;
  return (
    <Tooltip label={decided ? `${row.won} ${won}, ${row.lost} ${lost}` : "None decided yet"} events={{ hover: true, focus: true, touch: false }}>
      <Group gap={8} wrap="nowrap" tabIndex={0} style={{ minWidth: 150 }}>
        <Box className="df-bar-track" style={{ maxWidth: 90 }}>
          {row.rate != null && row.rate > 0 && <Box className="df-bar" style={{ width: `${row.rate * 100}%`, background: BAR }} />}
        </Box>
        <Text size="sm" fw={600} w={44} ta="right" className="df-count">{pct(row.rate)}</Text>
      </Group>
    </Tooltip>
  );
}

function Breakdowns({ by, options, labels }: {
  by: Record<string, Breakdown[]>; options: { value: string; label: string }[];
  labels: { started: string; won: string; lost: string; rate: string };
}) {
  const [dim, setDim] = useState(options[0].value);
  const rows = by[dim] ?? [];
  return (
    <Stack gap="sm">
      <Choice value={dim} onChange={setDim} data={options} label="Break down by" />
      <ScrollArea type="auto">
        <Table className="df-db" miw={560} verticalSpacing={6}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{(options.find((o) => o.value === dim)?.label ?? "").replace(/^By (.)/, (_, c: string) => c.toUpperCase())}</Table.Th>
              <Table.Th ta="right">{labels.started}</Table.Th>
              <Table.Th ta="right">{labels.won}</Table.Th>
              <Table.Th ta="right">{labels.lost}</Table.Th>
              <Table.Th ta="right">Still open</Table.Th>
              <Table.Th>{labels.rate}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.length === 0 && (
              <Table.Tr><Table.Td colSpan={6}><Text size="sm" c="dimmed">Nothing in this period.</Text></Table.Td></Table.Tr>
            )}
            {rows.map((r) => (
              <Table.Tr key={r.label}>
                <Table.Td><Text size="sm" c={r.label === "Not set" ? "dimmed" : undefined}>{r.label}</Text></Table.Td>
                <Table.Td ta="right" className="df-count">{r.total}</Table.Td>
                <Table.Td ta="right" className="df-count">{r.won}</Table.Td>
                <Table.Td ta="right" className="df-count">{r.lost}</Table.Td>
                <Table.Td ta="right" className="df-count">{r.open}</Table.Td>
                <Table.Td><RateCell row={r} won={labels.won.toLowerCase()} lost={labels.lost.toLowerCase()} /></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </ScrollArea>
    </Stack>
  );
}

function OutreachSection({ o }: { o: NonNullable<Analytics["outreach"]> }) {
  const decided = o.won + o.lost;
  const maxLost = Math.max(1, ...o.lost_reasons.map((r) => r.count));
  return (
    <Section title="Winning mandates" sub="Outreach attempts started in this period, and how they turned out.">
      <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
        <Tile label="Outreach started" value={o.total} to="/m/outreach?view=all" />
        <Tile label="Mandates won" value={o.won} to="/m/outreach?view=won" />
        <Tile label="Lost" value={o.lost} to="/m/outreach?view=lost" note={o.paused ? `${o.paused} on hold` : undefined} />
        <Tile label="Win rate" value={pct(o.win_rate)} note={decided ? `${o.won} won of ${decided} decided` : "None decided yet"} />
      </SimpleGrid>
      {o.median_days_to_mandate != null && (
        <Text size="sm">
          Winning a mandate takes <b>{Math.round(o.median_days_to_mandate)} days</b> from first being added (the middle value of {plural(o.mandates_timed, "mandate")}).
        </Text>
      )}
      <Paper withBorder p="md"><Text fw={600} size="sm" mb="xs">How far outreach gets</Text><Funnel steps={o.funnel} noun="attempt" /></Paper>
      <Breakdowns by={o.by} labels={{ started: "Started", won: "Won", lost: "Lost", rate: "Win rate" }}
        options={[{ value: "source", label: "By source" }, { value: "sector", label: "By sector" },
          { value: "transaction_type", label: "By transaction type" }, { value: "size", label: "By size" }]} />
      {o.size_currency && o.size_other_currencies > 0 && (
        <Text size="xs" c="dimmed" mt={-8}>Sizes compare {o.size_currency} amounts only; {plural(o.size_other_currencies, "attempt")} in other currencies are left out of “By size”.</Text>
      )}
      {o.lost_reasons.length > 0 && (
        <Paper withBorder p="md">
          <Text fw={600} size="sm" mb="xs">Why mandates were lost</Text>
          <Stack gap={2}>
            {o.lost_reasons.map((r) => (
              <div key={r.label} className="df-bar-row">
                <Text size="sm" w={200} style={{ flexShrink: 0 }} truncate>{r.label}</Text>
                <Box className="df-bar-track"><Box className="df-bar" style={{ width: `${(r.count / maxLost) * 100}%`, background: BAR }} /></Box>
                <Text size="sm" fw={600} w={36} ta="right" className="df-count">{r.count}</Text>
              </div>
            ))}
          </Stack>
        </Paper>
      )}
    </Section>
  );
}

function DealsSection({ d }: { d: NonNullable<Analytics["deals"]> }) {
  const ended = d.withdrawn + d.dropped;
  return (
    <Section title="Closing deals" sub="Deals mandated in this period, and how they turned out.">
      <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
        <Tile label="Deals" value={d.total} to="/m/deals?view=all" />
        <Tile label="Closed" value={d.closed} />
        <Tile label="Withdrawn or dropped" value={ended} note={d.on_hold ? `${d.on_hold} on hold` : undefined} />
        <Tile label="Close rate" value={pct(d.close_rate)} note={d.closed + ended ? `${d.closed} closed of ${d.closed + ended} finished` : "None finished yet"} />
      </SimpleGrid>
      {d.median_days_to_close != null && (
        <Text size="sm">
          From mandate to close takes <b>{Math.round(d.median_days_to_close)} days</b> (the middle value of {plural(d.closes_timed, "closed deal")}).
        </Text>
      )}
      <Paper withBorder p="md"><Text fw={600} size="sm" mb="xs">How far deals get</Text><Funnel steps={d.funnel} noun="deal" /></Paper>
      <Breakdowns by={d.by} labels={{ started: "Deals", won: "Closed", lost: "Ended", rate: "Close rate" }}
        options={[{ value: "transaction_type", label: "By transaction type" }, { value: "sector", label: "By sector" }, { value: "source", label: "By source" }]} />
    </Section>
  );
}

function InvestorsSection({ inv }: { inv: NonNullable<Analytics["investors"]> }) {
  return (
    <Section title="Investors who engage"
      sub={inv.entries ? `Of ${plural(inv.entries, "investor")} added to deal trackers, ${pct(inv.rate)} signed an NDA or got further.` : "No investors on deal trackers in this period."}>
      {inv.top.length === 0 ? (
        inv.entries > 0 && <Text size="sm" c="dimmed">Investors show here once they've been on at least two deals' trackers.</Text>
      ) : (
        <ScrollArea type="auto">
          <Table className="df-db" miw={560} verticalSpacing={6}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Investor</Table.Th><Table.Th ta="right">Deals</Table.Th><Table.Th ta="right">NDA or further</Table.Th>
                <Table.Th ta="right">Term sheets</Table.Th><Table.Th ta="right">Selected</Table.Th><Table.Th>Engagement</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {inv.top.map((r) => (
                <Table.Tr key={r.id}>
                  <Table.Td><Anchor component={Link} to={`/m/investors/${r.id}`} size="sm">{r.name}</Anchor></Table.Td>
                  <Table.Td ta="right" className="df-count">{r.deals}</Table.Td>
                  <Table.Td ta="right" className="df-count">{r.engaged}</Table.Td>
                  <Table.Td ta="right" className="df-count">{r.term_sheets}</Table.Td>
                  <Table.Td ta="right" className="df-count">{r.selected}</Table.Td>
                  <Table.Td>
                    <Group gap={8} wrap="nowrap" style={{ minWidth: 150 }}>
                      <Box className="df-bar-track" style={{ maxWidth: 90 }}>
                        {r.rate > 0 && <Box className="df-bar" style={{ width: `${r.rate * 100}%`, background: BAR }} />}
                      </Box>
                      <Text size="sm" fw={600} w={44} ta="right" className="df-count">{pct(r.rate)}</Text>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      )}
    </Section>
  );
}

function StagesSection({ stages, canEdit }: { stages: Record<string, StageRule[]>; canEdit: boolean }) {
  const names: Record<string, string> = { outreach: "Outreach", deals: "Active Deals" };
  return (
    <Section title="How long each stage takes"
      sub="The usual time is the middle value of past stays in that stage. A record counts as stuck after the time in the last column.">
      <SimpleGrid cols={{ base: 1, md: Object.keys(stages).length > 1 ? 2 : 1 }} spacing="lg">
        {Object.entries(stages).map(([m, rules]) => (
          <div key={m}>
            <Text fw={600} size="sm" mb={6}>{names[m]}</Text>
            <Table className="df-db" verticalSpacing={5}>
              <Table.Thead>
                <Table.Tr><Table.Th>Stage</Table.Th><Table.Th ta="right">Usually</Table.Th><Table.Th ta="right">Stuck after</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rules.map((r) => (
                  <Table.Tr key={r.stage}>
                    <Table.Td><Text size="sm">{r.label}</Text></Table.Td>
                    <Table.Td ta="right">
                      <Tooltip label={r.samples ? `From ${plural(r.samples, "finished stay")}` : "No finished stays yet"}>
                        <Text size="sm" c={r.median == null ? "dimmed" : undefined} className="df-count" tabIndex={0}>
                          {r.median == null ? "—" : r.median < 1 ? "Under a day" : `${Math.round(r.median)} d`}
                        </Text>
                      </Tooltip>
                    </Table.Td>
                    <Table.Td ta="right">
                      <Group gap={6} justify="flex-end" wrap="nowrap">
                        {r.source !== "learned" && (
                          <Badge size="xs" variant="light" color={r.source === "set" ? "navy" : "gray"} visibleFrom="sm">{r.source === "set" ? "Set" : "Starting point"}</Badge>
                        )}
                        <Text size="sm" className="df-count" style={{ whiteSpace: "nowrap" }}>{r.days ? `${r.days} d` : "Never"}</Text>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </div>
        ))}
      </SimpleGrid>
      {canEdit && <Anchor component={Link} to="/settings/alerts" size="sm">Change when records count as stuck (Settings &gt; Alerts)</Anchor>}
    </Section>
  );
}

function BankersSection({ data }: { data: Analytics }) {
  if (!data.can_see_team || !data.bankers) {
    return <Text size="sm" c="dimmed">Numbers by banker are shown to admins and to roles with “See team performance”.</Text>;
  }
  return (
    <Section title="By banker" sub="Outreach and deals by lead banker. Only you and others with “See team performance” see this.">
      <ScrollArea type="auto">
        <Table className="df-db" miw={720} verticalSpacing={6}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Banker</Table.Th><Table.Th ta="right">Outreach</Table.Th><Table.Th ta="right">Won</Table.Th><Table.Th ta="right">Lost</Table.Th>
              <Table.Th ta="right">Win rate</Table.Th><Table.Th ta="right">Deals led</Table.Th><Table.Th ta="right">Closed</Table.Th><Table.Th ta="right">Close rate</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.bankers.length === 0 && <Table.Tr><Table.Td colSpan={8}><Text size="sm" c="dimmed">Nothing in this period.</Text></Table.Td></Table.Tr>}
            {data.bankers.map((b) => (
              <Table.Tr key={b.id ?? "none"}>
                <Table.Td><Text size="sm" c={b.id == null ? "dimmed" : undefined}>{b.name}</Text></Table.Td>
                <Table.Td ta="right" className="df-count">{b.outreach}</Table.Td>
                <Table.Td ta="right" className="df-count">{b.won}</Table.Td>
                <Table.Td ta="right" className="df-count">{b.lost}</Table.Td>
                <Table.Td ta="right" className="df-count" fw={600}>{pct(b.win_rate)}</Table.Td>
                <Table.Td ta="right" className="df-count">{b.deals}</Table.Td>
                <Table.Td ta="right" className="df-count">{b.closed}</Table.Td>
                <Table.Td ta="right" className="df-count" fw={600}>{pct(b.close_rate)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </ScrollArea>
    </Section>
  );
}
