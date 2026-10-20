// "Stuck" on a record page and on board cards: in the same stage for longer than the firm usually takes.

import { Group, Text } from "@mantine/core";
import { IconHourglassHigh } from "@tabler/icons-react";

import type { ModuleSchema, RecordData } from "../types";
import { stuckInfo, useStageRules } from "./api";

export function useStuck(module: ModuleSchema, record: RecordData) {
  const { data: rules } = useStageRules(module.key);
  const p = module.pipeline;
  if (!p) return null;
  return stuckInfo(rules, record[p.stage_field], record[p.status_field] === p.open_value ? "open" : "other",
    record[p.entered_field], record._stamps?.created_at ?? undefined);
}

/** One line under the stage bar on a stuck record's page. */
export function StuckLine({ module, record }: { module: ModuleSchema; record: RecordData }) {
  const s = useStuck(module, record);
  if (!s) return null;
  const { days, rule } = s;
  const why = rule.source === "learned" && rule.median != null
    ? `At your firm this stage usually takes ${Math.round(rule.median)} ${Math.round(rule.median) === 1 ? "day" : "days"}, so it's flagged after ${rule.days}.`
    : rule.source === "set"
      ? `Your firm flags this stage after ${rule.days} days.`
      : `Flagged after ${rule.days} days, a starting point until your firm has more history of its own.`;
  return (
    <Group gap={8} wrap="nowrap" className="df-stuck-line" role="status">
      <IconHourglassHigh size={16} style={{ flexShrink: 0 }} />
      <Text size="sm"><b>Stuck: {days} days at {rule.label}.</b> {why}</Text>
    </Group>
  );
}

/** A small line on a board card. */
export function StuckTag({ module, record }: { module: ModuleSchema; record: RecordData }) {
  const s = useStuck(module, record);
  if (!s) return null;
  return (
    <Group gap={4} wrap="nowrap" mt={4}>
      <IconHourglassHigh size={13} color="var(--mantine-color-orange-8)" />
      <Text size="xs" c="orange.8" fw={600}>Stuck · {s.days} days in this stage</Text>
    </Group>
  );
}
