// Suggested investors for a deal: ranked by a simple fit score built from what's already recorded
// (sector, stage, ticket size vs the raise, geography, equity/debt, actively deploying).

import {
  Alert, Anchor, Badge, Button, CloseButton, Drawer, Group, Loader, Paper, Stack, Text, TextInput, ThemeIcon, Tooltip,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconCheck, IconMinus, IconPlus, IconQuestionMark, IconSearch, IconX } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, qs } from "../api";
import { errorMessage } from "../components/errors";
import type { LinkValue, RecordData } from "../types";
import { Skeleton } from "../components/ui";

type Reason = { factor: string; fit: "yes" | "partial" | "unknown" | "no"; text: string; points: number };
type Match = { investor: LinkValue & { subtitle: string }; score: number; reasons: Reason[] };
type Matches = {
  facts: { sector: string | null; stage: string | null; geography: string | null; raise: string | null; kind: string | null };
  weights: { factor: string; points: number }[];
  results: Match[];
};

const FIT = {
  yes: { color: "teal", icon: IconCheck, label: "Fits" },
  partial: { color: "yellow", icon: IconMinus, label: "Partly fits" },
  unknown: { color: "gray", icon: IconQuestionMark, label: "Not known" },
  no: { color: "red", icon: IconX, label: "Doesn't fit" },
} as const;

const FACT_LABELS: [keyof Matches["facts"], string, string][] = [
  ["sector", "Sector", "the company's sector"],
  ["stage", "Company stage", "the deal's company stage"],
  ["raise", "Target raise", "the target raise"],
  ["geography", "Geography", "the company's geography"],
  ["kind", "Equity / debt", "the instrument"],
];

export default function Suggestions({
  opened, onClose, deal, onAdd,
}: { opened: boolean; onClose: () => void; deal: RecordData; onAdd: (inv: LinkValue) => Promise<void> }) {
  const [q, setQ] = useState("");
  const [dq] = useDebouncedValue(q.trim(), 250);
  const [busy, setBusy] = useState<number | null>(null);
  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ["matches", deal.id, dq],
    queryFn: () => api<Matches>(`deals/${deal.id}/matches/${qs({ q: dq })}`),
    enabled: opened,
  });
  const missing = data ? FACT_LABELS.filter(([k]) => !data.facts[k]) : [];

  async function add(m: Match) {
    setBusy(m.investor.id);
    try {
      await onAdd(m.investor);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Drawer opened={opened} onClose={onClose} position="right" size="lg"
      title={<Text fw={600}>Suggested investors for {deal._title}</Text>}>
      <Stack gap="md">
        {data && (
          <div>
            <Text size="xs" c="dimmed" mb={6}>Matching on</Text>
            <Group gap={6}>
              {FACT_LABELS.map(([k, label]) => (
                <Badge key={k} variant={data.facts[k] ? "light" : "outline"} color={data.facts[k] ? "navy" : "gray"} tt="none" fw={500}>
                  {label}: {data.facts[k] ? (k === "kind" ? KIND[data.facts[k]!] ?? data.facts[k] : data.facts[k]) : "not set"}
                </Badge>
              ))}
            </Group>
          </div>
        )}
        {missing.length > 0 && (
          <Alert color="yellow" variant="light" p="sm">
            <Text size="sm">
              Fill in {missing.map(([, , phrase]) => phrase).join(", ").replace(/, ([^,]*)$/, " and $1")} for sharper
              suggestions. Until then those factors give every investor a few points.
            </Text>
          </Alert>
        )}
        <TextInput placeholder="Find an investor by name" leftSection={<IconSearch size={16} />} value={q}
          onChange={(e) => setQ(e.currentTarget.value)}
          rightSection={isFetching ? <Loader size={14} /> : q ? <CloseButton size="sm" onClick={() => setQ("")} /> : null} />
        {isLoading ? (
          <Stack gap={8}><Skeleton h={40} /><Skeleton h={40} /><Skeleton h={40} w="80%" /></Stack>
        ) : error ? (
          <Text c="red" size="sm">{errorMessage(error)}</Text>
        ) : data && data.results.length === 0 ? (
          <Text size="sm" c="dimmed">
            {dq ? `No investors match “${dq}”, or they're already on the tracker.` : "Every investor is already on this deal's tracker, or there are none yet."}
          </Text>
        ) : (
          <Stack gap="xs">
            {data?.results.map((m) => (
              <Paper key={m.investor.id} withBorder radius="md" p="sm">
                <Group justify="space-between" wrap="nowrap" align="flex-start">
                  <div style={{ minWidth: 0 }}>
                    <Group gap="xs" wrap="nowrap">
                      <Tooltip label="Fit score out of 100">
                        <Badge size="lg" radius="sm" variant="light" color={m.score >= 75 ? "teal" : m.score >= 50 ? "yellow" : "gray"}>{m.score}</Badge>
                      </Tooltip>
                      <div style={{ minWidth: 0 }}>
                        <Anchor component={Link} to={`/m/investors/${m.investor.id}`} fw={600} size="sm" onClick={onClose}>{m.investor.title}</Anchor>
                        {m.investor.subtitle && <Text size="xs" c="dimmed">{m.investor.subtitle}</Text>}
                      </div>
                    </Group>
                  </div>
                  <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} loading={busy === m.investor.id}
                    onClick={() => add(m)}>Add</Button>
                </Group>
                <Group gap={4} mt={8}>
                  {m.reasons.map((r) => {
                    const fit = FIT[r.fit];
                    return (
                      <Tooltip key={r.factor} label={`${fit.label} · ${r.points} of ${data.weights.find((w) => w.factor === r.factor)?.points ?? "?"} points`}>
                        <Group gap={4} wrap="nowrap" px={6} py={2}
                          style={{ borderRadius: 4, background: `var(--mantine-color-${fit.color}-0)` }}>
                          <ThemeIcon size={14} radius="xl" color={fit.color} variant={r.fit === "yes" ? "filled" : "light"}>
                            <fit.icon size={10} />
                          </ThemeIcon>
                          <Text size="xs">{r.text}</Text>
                        </Group>
                      </Tooltip>
                    );
                  })}
                </Group>
              </Paper>
            ))}
          </Stack>
        )}
        <Text size="xs" c="dimmed">
          Scores add up sector (25), ticket size against the raise (20), stage (15), geography (15), equity or debt (15)
          and whether the investor is actively deploying (10). Investors already on the tracker are left out.
        </Text>
      </Stack>
    </Drawer>
  );
}

const KIND: Record<string, string> = { equity: "Equity", debt: "Debt", both: "Equity and debt" };
