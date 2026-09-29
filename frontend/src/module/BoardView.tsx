// Kanban board for pipeline modules: one column per stage plus the exits (On hold, Lost).
// Drag a card to move it; exits ask for a reason first.

import { Avatar, Badge, Box, Group, Loader, Paper, ScrollArea, Stack, Text, Tooltip } from "@mantine/core";
import { DragDropContext, Draggable, Droppable, type DropResult } from "@hello-pangea/dnd";
import { IconCalendarEvent } from "@tabler/icons-react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";
import { useMe, useSchema, useUsers } from "../context";
import { errorMessage } from "../components/errors";
import { formatDate } from "../format";
import type { ModuleSchema, Page, RecordData } from "../types";
import { FieldValue, textValue } from "./fields";
import { stageLabel, stageOf, usePipelineMove } from "./pipeline";

type Column = { id: string; label: string; kind: "stage" | "exit"; value: string };

export default function BoardView({ module, params }: { module: ModuleSchema; params: URLSearchParams }) {
  const p = module.pipeline!;
  const nav = useNavigate();
  const [overrides, setOverrides] = useState<Record<number, Partial<RecordData>>>({});
  const { move, modal } = usePipelineMove(module, () => setOverrides({}));

  const apiParams = new URLSearchParams(params);
  apiParams.delete("page");
  if (module.segments) apiParams.delete(module.segments.param); // the board always shows every column
  apiParams.set("page_size", "500");
  if (!apiParams.get("ordering")) apiParams.set("ordering", "next_follow_up,-updated_at");
  const qs = apiParams.toString();
  const { data, isLoading, error } = useQuery({
    queryKey: ["records", module.key, "board", qs],
    queryFn: () => api<Page<RecordData>>(`m/${module.key}/?${qs}`),
    placeholderData: keepPreviousData,
  });

  const columns: Column[] = useMemo(() => [
    ...p.stages.map((s) => ({ id: `stage:${s.value}`, label: s.label, kind: "stage" as const, value: s.value })),
    ...p.exits.map((e) => ({ id: `exit:${e.value}`, label: e.label, kind: "exit" as const, value: e.value })),
  ], [p]);

  const records = (data?.results ?? []).map((r) => ({ ...r, ...overrides[r.id] }) as RecordData);
  const byColumn = new Map<string, RecordData[]>(columns.map((c) => [c.id, []]));
  for (const r of records) {
    const { stage, status, isOpen } = stageOf(r, p);
    byColumn.get(isOpen ? `stage:${stage}` : `exit:${status}`)?.push(r);
  }

  function onDragEnd(result: DropResult) {
    const dest = result.destination?.droppableId;
    if (!dest || dest === result.source.droppableId) return;
    const record = records.find((r) => String(r.id) === result.draggableId);
    if (!record) return;
    const [kind, value] = dest.split(":");
    if (kind === "exit") {
      move(record, { kind: "exit", exit: value });
      return;
    }
    if (stageOf(record, p).isOpen) {
      // Show the card in its new column straight away; the refetch confirms it.
      setOverrides((o) => ({ ...o, [record.id]: { [p.stage_field]: value } }));
    }
    move(record, { kind: "stage", stage: value });
  }

  if (isLoading) return <Group p="xl"><Loader /></Group>;
  if (error) return <Paper withBorder p="lg"><Text c="red">{errorMessage(error)}</Text></Paper>;

  return (
    <>
      <ScrollArea type="auto" offsetScrollbars>
        <DragDropContext onDragEnd={onDragEnd}>
          <Group align="flex-start" gap="sm" wrap="nowrap" pb="sm">
            {columns.map((col) => (
              <BoardColumn key={col.id} column={col} records={byColumn.get(col.id) ?? []} module={module}
                canMove={module.perms.edit} onOpen={(r) => nav(`/m/${module.key}/${r.id}`)} wonLabel={p.won ? stageLabel(p, p.won) : ""} />
            ))}
          </Group>
        </DragDropContext>
      </ScrollArea>
      {data && data.count > data.results.length && (
        <Text size="xs" c="dimmed">Showing the first {data.results.length} of {data.count}. Narrow the list with search or filters.</Text>
      )}
      {modal}
    </>
  );
}

function BoardColumn({
  column, records, module, canMove, onOpen, wonLabel,
}: {
  column: Column; records: RecordData[]; module: ModuleSchema; canMove: boolean; onOpen: (r: RecordData) => void; wonLabel: string;
}) {
  const color = column.kind === "exit" ? (column.value === "lost" ? "red" : "orange") : column.label === wonLabel ? "teal" : "navy";
  return (
    <Paper w={236} miw={236} radius="md" bg="gray.1" p={8} style={{ flexShrink: 0 }}>
      <Group justify="space-between" px={4} pb={8}>
        <Text size="sm" fw={600} c={column.kind === "exit" || column.label === wonLabel ? `${color}.8` : undefined}>{column.label}</Text>
        <Badge size="sm" variant="light" color={color}>{records.length}</Badge>
      </Group>
      <Droppable droppableId={column.id} isDropDisabled={!canMove}>
        {(provided, snapshot) => (
          <ScrollArea.Autosize mah="calc(100vh - 330px)" type="auto">
            <Stack gap={8} ref={provided.innerRef} {...provided.droppableProps} mih={80} p={2}
              style={{ borderRadius: 8, background: snapshot.isDraggingOver ? "var(--mantine-color-navy-0)" : undefined }}>
              {records.map((r, i) => (
                <Draggable key={r.id} draggableId={String(r.id)} index={i} isDragDisabled={!canMove}>
                  {(drag, dragSnap) => (
                    <div ref={drag.innerRef} {...drag.draggableProps} {...drag.dragHandleProps}>
                      <BoardCard record={r} module={module} dragging={dragSnap.isDragging} onOpen={() => onOpen(r)} />
                    </div>
                  )}
                </Draggable>
              ))}
              {provided.placeholder}
              {records.length === 0 && !snapshot.isDraggingOver && (
                <Text size="xs" c="dimmed" ta="center" py="md">Nothing here</Text>
              )}
            </Stack>
          </ScrollArea.Autosize>
        )}
      </Droppable>
    </Paper>
  );
}

function BoardCard({ record: r, module, dragging, onOpen }: { record: RecordData; module: ModuleSchema; dragging: boolean; onOpen: () => void }) {
  const me = useMe();
  const { data: schema } = useSchema();
  const { data: users } = useUsers();
  const p = module.pipeline!;
  const { stage, status, isOpen } = stageOf(r, p);
  const field = (k: string) => module.fields.find((f) => f.key === k && f.enabled);
  const ctx = { schema, users, prefs: me };
  const type = field("transaction_type");
  const size = field("expected_size");
  const banker = field("lead_banker");
  const follow = field("next_follow_up");
  const bankerName = banker ? textValue(banker, r[banker.key], ctx) : "";
  const exit = p.exits.find((e) => e.value === status);
  const reasonField = exit?.reason_field ? field(exit.reason_field) : undefined;
  const dateField = exit?.date_field ? field(exit.date_field) : undefined;
  const followDate = follow ? (r[follow.key] as string | null) : null;
  const today = new Date().toISOString().slice(0, 10);
  const overdue = !!followDate && followDate < today && isOpen;

  return (
    <Paper withBorder radius="md" p="sm" shadow={dragging ? "md" : undefined} onClick={onOpen}
      style={{ cursor: "pointer", borderLeft: overdue ? "3px solid var(--mantine-color-red-6)" : undefined }}>
      <Text size="sm" fw={600} lineClamp={2}>{r._title}</Text>
      {type && r[type.key] ? <Text size="xs" c="dimmed">{textValue(type, r[type.key], ctx)}</Text> : null}
      {size && r[size.key] && (r[size.key] as { amount: string | null }).amount ? (
        <Box mt={4}><FieldValue f={size} v={r[size.key]} compact /></Box>
      ) : null}
      {!isOpen && (
        <Stack gap={0} mt={6}>
          <Text size="xs" c={status === "lost" ? "red.8" : "orange.8"}>At {stageLabel(p, stage)}{reasonField && r[reasonField.key] ? ` · ${textValue(reasonField, r[reasonField.key], ctx)}` : ""}</Text>
          {dateField && r[dateField.key] ? <Text size="xs" c="dimmed">{dateField.label} {formatDate(r[dateField.key] as string, me)}</Text> : null}
        </Stack>
      )}
      <Group justify="space-between" mt={8} gap={4} wrap="nowrap">
        {isOpen && followDate ? (
          <Tooltip label={follow!.label}>
            <Group gap={4} wrap="nowrap">
              <IconCalendarEvent size={14} color={overdue ? "var(--mantine-color-red-7)" : "var(--mantine-color-gray-6)"} />
              <Text size="xs" c={overdue ? "red.7" : "dimmed"} fw={overdue ? 600 : undefined}>{formatDate(followDate, me)}</Text>
            </Group>
          </Tooltip>
        ) : <span />}
        {bankerName && (
          <Tooltip label={`${banker!.label}: ${bankerName}`}>
            <Avatar size={22} radius="xl" color="navy">{bankerName.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase()}</Avatar>
          </Tooltip>
        )}
      </Group>
    </Paper>
  );
}
