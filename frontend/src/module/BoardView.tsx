// Kanban board for pipeline modules: one column per stage plus the exits (On hold, Lost, Dropped…).
// Drag a card to move it; exits ask for what they need first (a reason, a date, a note).

import { Avatar, Badge, Box, Group, Loader, Paper, ScrollArea, Stack, Text, Tooltip } from "@mantine/core";
import { DragDropContext, Draggable, Droppable, type BeforeCapture, type DropResult } from "@hello-pangea/dnd";
import { IconCalendarEvent } from "@tabler/icons-react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";
import { useMe, useSchema, useUsers } from "../context";
import { errorMessage } from "../components/errors";
import { formatDate } from "../format";
import type { ModuleSchema, Page, RecordData } from "../types";
import { FieldValue, textValue } from "./fields";
import { stageLabel, stageOf, usePipelineMove } from "./pipeline";

type Column = { id: string; label: string; kind: "stage" | "exit"; value: string; tone: string };

// Where a card was picked up. The drag library decides the target column from the dragged card's
// box, and a wide card spans several narrow (folded) columns at once, so while dragging the card's
// box is narrowed to a slim strip under the pointer; the card itself looks unchanged.
type Grab = { id: string; x: number; width: number };
const HIT = 48; // a little narrower than a folded column (52), so it never covers two at once

// What each module's cards show: a line under the name, an amount, a person, and a date that turns
// red once it has passed (outreach: next follow-up; deals: target close).
const CARDS: Record<string, { sub: string; amount: string; person: string; date: string; order: string }> = {
  outreach: { sub: "transaction_type", amount: "expected_size", person: "lead_banker", date: "next_follow_up",
    order: "next_follow_up,-updated_at" },
  deals: { sub: "transaction_type", amount: "target_raise", person: "lead_banker", date: "target_close_date",
    order: "target_close_date,-updated_at" },
};
const DEFAULT_CARD = { sub: "", amount: "", person: "", date: "", order: "-updated_at" };

export default function BoardView({ module, params }: { module: ModuleSchema; params: URLSearchParams }) {
  const p = module.pipeline!;
  const nav = useNavigate();
  const [overrides, setOverrides] = useState<Record<number, Partial<RecordData>>>({});
  const { move, modal } = usePipelineMove(module, () => setOverrides({}));

  const apiParams = new URLSearchParams(params);
  apiParams.delete("page");
  if (module.segments) apiParams.delete(module.segments.param); // the board always shows every column
  apiParams.set("page_size", "500");
  if (!apiParams.get("ordering")) apiParams.set("ordering", (CARDS[module.key] ?? DEFAULT_CARD).order);
  const qs = apiParams.toString();
  const { data, isLoading, error } = useQuery({
    queryKey: ["records", module.key, "board", qs],
    queryFn: () => api<Page<RecordData>>(`m/${module.key}/?${qs}`),
    placeholderData: keepPreviousData,
  });

  const columns: Column[] = useMemo(() => [
    ...p.stages.map((s) => ({ id: `stage:${s.value}`, label: s.label, kind: "stage" as const, value: s.value,
      tone: s.value === p.won ? "teal" : "navy" })),
    ...p.exits.map((e) => ({ id: `exit:${e.value}`, label: e.label, kind: "exit" as const, value: e.value, tone: e.tone })),
  ], [p]);
  // Phases (e.g. Preparation / Execution) label the stage columns they cover.
  const phases = p.groups.length
    ? [...p.groups.map((g) => ({ label: g.label, columns: columns.filter((c) => c.kind === "stage" && g.stages.includes(c.value)) })),
      { label: "Exits", columns: columns.filter((c) => c.kind === "exit") }]
    : [{ label: "", columns }];

  const records = (data?.results ?? []).map((r) => ({ ...r, ...overrides[r.id] }) as RecordData);
  const byColumn = new Map<string, RecordData[]>(columns.map((c) => [c.id, []]));
  for (const r of records) {
    const { stage, status, isOpen } = stageOf(r, p);
    byColumn.get(isOpen ? `stage:${stage}` : `exit:${status}`)?.push(r);
  }

  const grab = useRef<Grab | null>(null);
  const [capture, setCapture] = useState<Grab | null>(null);
  const onGrab = (g: Grab) => { grab.current = g; };
  function onBeforeCapture(b: BeforeCapture) {
    const g = grab.current?.id === b.draggableId ? grab.current : null;
    setCapture(g ?? { id: b.draggableId, x: 98, width: 196 }); // keyboard drags: the card's middle
  }

  function onDragEnd(result: DropResult) {
    setCapture(null);
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
        <DragDropContext onBeforeCapture={onBeforeCapture} onDragEnd={onDragEnd}>
          <Group align="flex-start" gap="lg" wrap="nowrap" pb="sm">
            {phases.map((ph) => (
              <Stack key={ph.label || "all"} gap={6}>
                {ph.label && (
                  <Text size="xs" fw={600} c="dimmed" tt="uppercase" px={4} pb={2}
                    style={{ borderBottom: "1px solid var(--mantine-color-gray-3)", letterSpacing: 0.4 }}>{ph.label}</Text>
                )}
                <Group align="flex-start" gap={8} wrap="nowrap">
                  {ph.columns.map((col) => (
                    <BoardColumn key={col.id} column={col} records={byColumn.get(col.id) ?? []} module={module}
                      canMove={module.perms.edit} onOpen={(r) => nav(`/m/${module.key}/${r.id}`)}
                      capture={capture} onGrab={onGrab} />
                  ))}
                </Group>
              </Stack>
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
  column, records, module, canMove, onOpen, capture, onGrab,
}: {
  column: Column; records: RecordData[]; module: ModuleSchema; canMove: boolean; onOpen: (r: RecordData) => void;
  capture: Grab | null; onGrab: (g: Grab) => void;
}) {
  const color = column.tone;
  const dense = module.pipeline!.stages.length > 8;
  const width = dense ? 216 : 236;
  if (dense && records.length === 0) {
    // Long pipelines (Active Deals) fold empty columns into narrow strips, so the whole board fits on
    // screen; a strip still takes a dropped card.
    return (
      <Droppable droppableId={column.id} isDropDisabled={!canMove}>
        {(provided, snapshot) => (
          <Paper ref={provided.innerRef} {...provided.droppableProps} w={52} miw={52} mih={260} radius="md" p={6}
            bg={snapshot.isDraggingOver ? "navy.0" : "gray.1"} aria-label={`${column.label}: empty`}
            style={{ flexShrink: 0, outline: snapshot.isDraggingOver ? "2px solid var(--mantine-color-navy-3)" : undefined }}>
            <Stack align="center" gap={8}>
              <Badge size="sm" variant="light" color={color} circle>0</Badge>
              <Text size="sm" fw={600} c={color !== "navy" ? `${color}.8` : "gray.7"}
                style={{ writingMode: "vertical-rl", whiteSpace: "nowrap" }}>{column.label}</Text>
            </Stack>
            <div style={{ height: 0, overflow: "hidden" }}>{provided.placeholder}</div>
          </Paper>
        )}
      </Droppable>
    );
  }
  return (
    <Paper w={width} miw={width} radius="md" bg="gray.1" p={8} style={{ flexShrink: 0 }}>
      <Group justify="space-between" px={4} pb={8} wrap="nowrap" align="flex-start">
        <Text size="sm" fw={600} lh={1.3} c={color !== "navy" ? `${color}.8` : undefined}>{column.label}</Text>
        <Badge size="sm" variant="light" color={color}>{records.length}</Badge>
      </Group>
      <Droppable droppableId={column.id} isDropDisabled={!canMove}>
        {(provided, snapshot) => (
          <ScrollArea.Autosize mah="calc(100vh - 330px)" type="auto">
            <Stack gap={8} ref={provided.innerRef} {...provided.droppableProps} mih={80} p={2}
              style={{ borderRadius: 8, background: snapshot.isDraggingOver ? "var(--mantine-color-navy-0)" : undefined }}>
              {records.map((r, i) => (
                <Draggable key={r.id} draggableId={String(r.id)} index={i} isDragDisabled={!canMove}>
                  {(drag, dragSnap) => {
                    const slim = capture?.id === String(r.id) ? capture : null;
                    const offset = slim ? Math.min(Math.max(slim.x - HIT / 2, 0), slim.width - HIT) : 0;
                    return (
                      <div ref={drag.innerRef} {...drag.draggableProps} {...drag.dragHandleProps}
                        onPointerDown={(e) => {
                          const box = e.currentTarget.getBoundingClientRect();
                          onGrab({ id: String(r.id), x: e.clientX - box.left, width: box.width });
                        }}
                        style={slim ? { width: HIT, marginLeft: offset, ...drag.draggableProps.style } : drag.draggableProps.style}>
                        <div style={slim ? { width: slim.width, marginLeft: -offset } : undefined}>
                          <BoardCard record={r} module={module} dragging={dragSnap.isDragging} onOpen={() => onOpen(r)} />
                        </div>
                      </div>
                    );
                  }}
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
  const field = (k: string) => (k ? module.fields.find((f) => f.key === k && f.enabled) : undefined);
  const ctx = { schema, users, prefs: me };
  const card = CARDS[module.key] ?? DEFAULT_CARD;
  const type = field(card.sub);
  const size = field(card.amount);
  const banker = field(card.person);
  const follow = field(card.date);
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
          <Text size="xs" c={`${exit?.tone ?? "red"}.8`}>At {stageLabel(p, stage)}{reasonField && r[reasonField.key] ? ` · ${textValue(reasonField, r[reasonField.key], ctx)}` : ""}</Text>
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
