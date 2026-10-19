// Stage bar on a pipeline record: where it is, how long it's been there, and the next move.

import { Button, Group, Menu, Stack, Text, Tooltip, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconPlayerPause, IconPlayerPlay, IconX } from "@tabler/icons-react";

import { useApp, useSchema, useUsers } from "../context";
import { formatDate } from "../format";
import type { ModuleSchema, RecordData } from "../types";
import { textValue } from "./fields";
import { exitOf, resumeVerb, stageLabel, stageOf, usePipelineMove } from "./pipeline";

/** Soft hyphens in long words so a narrow stage cell breaks "Documen-tation" rather than mid-word
 *  at random (browsers without hyphenation dictionaries otherwise split anywhere). */
function breakable(label: string): string {
  return label.split(" ").map((w) => {
    if (w.length < 11 || w.includes("-")) return w;
    const at = /(tion|ment|ship|ence|ance)$/.test(w) ? w.length - 4 : Math.ceil(w.length / 2);
    return `${w.slice(0, at)}\u00ad${w.slice(at)}`;
  }).join(" ");
}

export default function PipelineBar({ module, record }: { module: ModuleSchema; record: RecordData }) {
  const { me } = useApp();
  const { data: schema } = useSchema();
  const { data: users } = useUsers();
  const p = module.pipeline!;
  const { stage, status, isOpen, isWon } = stageOf(record, p);
  const { move, modal } = usePipelineMove(module);
  const idx = p.stages.findIndex((s) => s.value === stage);
  const next = p.stages[idx + 1];
  const exit = exitOf(p, status);
  const canEdit = module.perms.edit;
  const ctx = { schema, users, prefs: me };
  const since = record[p.entered_field] as string | null;
  const reasonField = exit?.reason_field ? module.fields.find((f) => f.key === exit.reason_field) : undefined;
  const dateField = exit?.date_field ? module.fields.find((f) => f.key === exit.date_field) : undefined;
  const reason = reasonField && record[reasonField.key] ? textValue(reasonField, record[reasonField.key], ctx) : "";

  const tone = !isOpen ? exit?.tone ?? "red" : isWon ? "teal" : "navy";
  let summary: string;
  if (!isOpen) {
    summary = `${exit?.label ?? status} at ${stageLabel(p, stage)}${reason ? ` · ${reason}` : ""}`;
    if (dateField && record[dateField.key]) summary += ` · ${dateField.label.toLowerCase()} ${formatDate(record[dateField.key] as string, me)}`;
  } else if (isWon) {
    summary = `${stageLabel(p, stage)}${since ? ` on ${formatDate(since, me)}` : ""}.`;
  } else {
    summary = `${stageLabel(p, stage)}${since ? ` since ${formatDate(since, me)}` : ""}`;
  }

  // Long pipelines (Active Deals has twelve stages in two phases) get narrower cells and phase labels.
  const dense = p.stages.length > 8;
  const phases = p.groups.length
    ? p.groups.map((g) => ({ label: g.label, stages: p.stages.filter((s) => g.stages.includes(s.value)) }))
    : [{ label: "", stages: p.stages }];

  const cell = (s: { value: string; label: string }) => {
    const i = p.stages.indexOf(s);
    const current = i === idx;
    const done = i < idx;
    const shade = tone === "gray" ? 6 : isOpen ? 7 : 6;
    const bg = current ? `var(--mantine-color-${tone}-${shade})` : done ? "var(--mantine-color-navy-1)" : "var(--mantine-color-gray-2)";
    const fg = current ? "white" : done ? "var(--mantine-color-navy-8)" : "var(--mantine-color-gray-6)";
    const clickable = canEdit && !(current && isOpen);
    const button = (
      <UnstyledButton key={s.value} disabled={!clickable} className="df-stage-cell" lang="en"
        onClick={() => move(record, { kind: "stage", stage: s.value })}
        style={{
          flex: 1, minWidth: dense ? 76 : 110, padding: dense ? "6px 6px 6px 14px" : "8px 10px", background: bg, color: fg,
          textAlign: "center", fontSize: dense ? 12 : 13, fontWeight: current ? 600 : 500, lineHeight: 1.25,
          clipPath: i === 0 ? "polygon(0 0, calc(100% - 10px) 0, 100% 50%, calc(100% - 10px) 100%, 0 100%)"
            : "polygon(0 0, calc(100% - 10px) 0, 100% 50%, calc(100% - 10px) 100%, 0 100%, 10px 50%)",
          cursor: clickable ? "pointer" : "default",
        }}>
        {dense ? breakable(s.label) : s.label}
      </UnstyledButton>
    );
    return clickable ? <Tooltip key={s.value} label={`Move to ${s.label}`}>{button}</Tooltip> : button;
  };

  return (
    <div className="df-stagebar">
      <Group gap={4} wrap="nowrap" align="stretch" style={{ overflowX: "auto" }}>
        {phases.map((ph) => (
          <Stack key={ph.label || "all"} gap={4} style={{ flex: ph.stages.length, minWidth: 0 }}>
            {ph.label && (
              <Text size="xs" fw={600} c="dimmed" tt="uppercase" pb={2}
                style={{ borderBottom: "1px solid var(--mantine-color-gray-3)", letterSpacing: 0.4 }}>{ph.label}</Text>
            )}
            <Group gap={4} wrap="nowrap" align="stretch">{ph.stages.map(cell)}</Group>
          </Stack>
        ))}
      </Group>
      <Group justify="space-between" mt="sm" gap="xs">
        <Text size="sm" c={`${tone}.8`} fw={500}>{summary}</Text>
        {canEdit && (
          <Group gap="xs">
            {isOpen && !isWon && next && (
              <Button size="xs" onClick={() => move(record, { kind: "stage", stage: next.value })}>Move to {next.label}</Button>
            )}
            {!isOpen && (
              <Button size="xs" leftSection={<IconPlayerPlay size={14} />} onClick={() => move(record, { kind: "resume" })}>
                {resumeVerb(p, status)} at {stageLabel(p, stage)}
              </Button>
            )}
            {!isWon && (
              <Menu position="bottom-end">
                <Menu.Target>
                  <Button size="xs" variant="default" rightSection={<IconChevronDown size={14} />}>More</Button>
                </Menu.Target>
                <Menu.Dropdown>
                  {p.exits.filter((e) => e.value !== status).map((e) => (
                    <Menu.Item key={e.value} color={e.tone === "red" ? "red" : undefined}
                      leftSection={e.paused ? <IconPlayerPause size={14} /> : <IconX size={14} />}
                      onClick={() => move(record, { kind: "exit", exit: e.value })}>
                      {e.action}
                    </Menu.Item>
                  ))}
                </Menu.Dropdown>
              </Menu>
            )}
          </Group>
        )}
      </Group>
      {modal}
    </div>
  );
}
