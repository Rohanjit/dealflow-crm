// Stage bar on a pipeline record: where it is, how long it's been there, and the next move.

import { Button, Group, Menu, Paper, Text, Tooltip, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconPlayerPause, IconPlayerPlay, IconX } from "@tabler/icons-react";

import { useApp, useSchema, useUsers } from "../context";
import { formatDate } from "../format";
import type { ModuleSchema, RecordData } from "../types";
import { textValue } from "./fields";
import { exitOf, stageLabel, stageOf, usePipelineMove } from "./pipeline";

export default function PipelineBar({ module, record }: { module: ModuleSchema; record: RecordData }) {
  const { info, me } = useApp();
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
  const dealsBuilt = info.built_modules.includes("deals");

  const tone = !isOpen ? (status === "lost" ? "red" : "orange") : isWon ? "teal" : "navy";
  let summary: string;
  if (!isOpen) {
    summary = `${exit?.label ?? status} at ${stageLabel(p, stage)}${reason ? ` · ${reason}` : ""}`;
    if (dateField && record[dateField.key]) summary += ` · ${dateField.label.toLowerCase()} ${formatDate(record[dateField.key] as string, me)}`;
  } else if (isWon) {
    summary = dealsBuilt ? "Mandate won." : "Mandate won. It moves to Active Deals once that module is added.";
  } else {
    summary = `${stageLabel(p, stage)}${since ? ` since ${formatDate(since, me)}` : ""}`;
  }

  return (
    <Paper withBorder radius="md" p="md">
      <Group gap={4} wrap="nowrap" style={{ overflowX: "auto" }}>
        {p.stages.map((s, i) => {
          const current = i === idx;
          const done = i < idx;
          const bg = current ? `var(--mantine-color-${tone}-${isOpen ? 7 : 6})` : done ? "var(--mantine-color-navy-1)" : "var(--mantine-color-gray-1)";
          const fg = current ? "white" : done ? "var(--mantine-color-navy-8)" : "var(--mantine-color-gray-6)";
          const cell = (
            <UnstyledButton key={s.value} disabled={!canEdit || (current && isOpen)}
              onClick={() => move(record, { kind: "stage", stage: s.value })}
              style={{
                flex: 1, minWidth: 110, padding: "8px 10px", background: bg, color: fg, textAlign: "center",
                fontSize: 13, fontWeight: current ? 600 : 500,
                clipPath: i === 0 ? "polygon(0 0, calc(100% - 10px) 0, 100% 50%, calc(100% - 10px) 100%, 0 100%)"
                  : "polygon(0 0, calc(100% - 10px) 0, 100% 50%, calc(100% - 10px) 100%, 0 100%, 10px 50%)",
                cursor: canEdit && !(current && isOpen) ? "pointer" : "default",
              }}>
              {s.label}
            </UnstyledButton>
          );
          return canEdit && !(current && isOpen) ? <Tooltip key={s.value} label={`Move to ${s.label}`}>{cell}</Tooltip> : cell;
        })}
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
                {status === "lost" ? "Reopen" : "Resume"} at {stageLabel(p, stage)}
              </Button>
            )}
            {!isWon && (
              <Menu position="bottom-end">
                <Menu.Target>
                  <Button size="xs" variant="default" rightSection={<IconChevronDown size={14} />}>More</Button>
                </Menu.Target>
                <Menu.Dropdown>
                  {p.exits.filter((e) => e.value !== status).map((e) => (
                    <Menu.Item key={e.value} color={e.value === "lost" ? "red" : undefined}
                      leftSection={e.value === "lost" ? <IconX size={14} /> : <IconPlayerPause size={14} />}
                      onClick={() => move(record, { kind: "exit", exit: e.value })}>
                      {e.value === "lost" ? "Mark as lost" : `Put ${e.label.toLowerCase()}`}
                    </Menu.Item>
                  ))}
                </Menu.Dropdown>
              </Menu>
            )}
          </Group>
        )}
      </Group>
      {modal}
    </Paper>
  );
}
