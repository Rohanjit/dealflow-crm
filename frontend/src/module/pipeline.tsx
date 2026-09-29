// Stage moves for pipeline modules (Outreach, Active Deals, a deal's investor tracker): the modal
// that asks what's needed when a record leaves the pipeline (a reason, a revisit date, a note), and
// a hook the board, stage bar and tracker share.

import { Button, Group, Modal, Select, Stack, Text, Textarea } from "@mantine/core";
import { DateInput } from "@mantine/dates";
import { modals } from "@mantine/modals";
import { useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useState } from "react";

import { api } from "../api";
import { useMe, useSchema } from "../context";
import { errorMessage, fieldErrors, notifyError, notifyOk } from "../components/errors";
import type { ModuleSchema, Pipeline, PipelineExit, RecordData } from "../types";
import { dateParser } from "./fields";

export type Move = { kind: "stage"; stage: string } | { kind: "exit"; exit: string } | { kind: "resume" };

export function stageOf(r: RecordData, p: Pipeline) {
  const stage = String(r[p.stage_field] ?? p.stages[0].value);
  const status = String(r[p.status_field] ?? p.open_value);
  return { stage, status, isOpen: status === p.open_value, isWon: !!p.won && stage === p.won && status === p.open_value };
}

export function stageLabel(p: Pipeline, value: string) {
  return p.stages.find((s) => s.value === value)?.label ?? value;
}

export function exitOf(p: Pipeline, status: string): PipelineExit | undefined {
  return p.exits.find((e) => e.value === status);
}

/** The exit a reason or date field belongs to (e.g. Lost reason → Lost). */
export function exitForField(p: Pipeline, key: string): PipelineExit | undefined {
  return p.exits.find((e) => e.reason_field === key || e.date_field === key);
}

/** "Resume" for a pause, "Reopen" for the rest. */
export function resumeVerb(p: Pipeline, status: string): "Resume" | "Reopen" {
  return exitOf(p, status)?.paused ? "Resume" : "Reopen";
}

const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/** Moves a record through the pipeline, asking for whatever each move needs. */
export function usePipelineMove(module: ModuleSchema, onMoved?: () => void) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<{ record: RecordData; exit: PipelineExit } | null>(null);
  const p = module.pipeline!;

  async function save(record: RecordData, body: Record<string, unknown>, done: string) {
    try {
      await api(`m/${module.key}/${record.id}/`, { method: "PATCH", body });
      notifyOk(done);
      return true;
    } catch (e) {
      notifyError(e, "Couldn't move it");
      return false;
    } finally {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["records"] }), // a move can add records elsewhere (a won mandate's deal)
        qc.invalidateQueries({ queryKey: ["record", module.key, String(record.id)] }),
        qc.invalidateQueries({ queryKey: ["notes", module.key, record.id] }),
        qc.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
      onMoved?.();
    }
  }

  function move(record: RecordData, m: Move) {
    const { stage, status, isOpen } = stageOf(record, p);
    if (m.kind === "exit") {
      const exit = p.exits.find((e) => e.value === m.exit);
      if (exit && status !== exit.value) setPending({ record, exit });
      return;
    }
    if (m.kind === "resume") {
      const done = resumeVerb(p, status) === "Resume" ? "Resumed" : "Reopened";
      save(record, { [p.status_field]: p.open_value }, `${done} at ${stageLabel(p, stage)}.`);
      return;
    }
    if (m.stage === stage && isOpen) return;
    if (!isOpen) {
      const verb = resumeVerb(p, status);
      modals.openConfirmModal({
        title: `${verb} ${record._title}?`,
        children: (
          <Text size="sm">
            {record._title} is marked “{exitOf(p, status)?.label ?? status}” at {stageLabel(p, stage)}. {verb} it and
            move it to {stageLabel(p, m.stage)}?
          </Text>
        ),
        labels: { confirm: `${verb} and move`, cancel: "Cancel" },
        onConfirm: () => save(record, { [p.stage_field]: m.stage, [p.status_field]: p.open_value }, `Moved to ${stageLabel(p, m.stage)}.`),
      });
      return;
    }
    save(record, { [p.stage_field]: m.stage }, `Moved to ${stageLabel(p, m.stage)}.`);
  }

  const modal = pending && (
    <ExitModal module={module} record={pending.record} exit={pending.exit} onClose={() => setPending(null)}
      onSaved={async () => {
        setPending(null);
        await Promise.all([
          qc.invalidateQueries({ queryKey: ["records"] }),
          qc.invalidateQueries({ queryKey: ["record", module.key, String(pending.record.id)] }),
          qc.invalidateQueries({ queryKey: ["notes", module.key, pending.record.id] }),
          qc.invalidateQueries({ queryKey: ["dashboard"] }),
        ]);
        onMoved?.();
      }} />
  );
  return { move, modal };
}

function ExitModal({
  module, record, exit, onClose, onSaved,
}: { module: ModuleSchema; record: RecordData; exit: PipelineExit; onClose: () => void; onSaved: () => void }) {
  const me = useMe();
  const { data: schema } = useSchema();
  const p = module.pipeline!;
  const reasonField = module.fields.find((f) => f.key === exit.reason_field);
  const dateField = module.fields.find((f) => f.key === exit.date_field);
  const reasons = (reasonField?.choice_list ? schema?.choice_lists[reasonField.choice_list]?.options ?? [] : [])
    .filter((o) => o.is_active);
  const [reason, setReason] = useState<string | null>(null);
  const [date, setDate] = useState<Date | null>(null);
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const { stage } = stageOf(record, p);

  async function submit() {
    setBusy(true);
    setErrors({});
    const body: Record<string, unknown> = { [p.status_field]: exit.value };
    if (exit.reason_field) body[exit.reason_field] = reason ? Number(reason) : null;
    if (exit.date_field) body[exit.date_field] = date ? dayjs(date).format("YYYY-MM-DD") : null;
    try {
      await api(`m/${module.key}/${record.id}/`, { method: "PATCH", body });
      if (note.trim()) await api(`m/${module.key}/${record.id}/notes/`, { body: { kind: "note", text: note } });
      notifyOk(`${record._title}: ${lowerFirst(exit.done)}.`);
      onSaved();
    } catch (e) {
      const fe = fieldErrors(e);
      setErrors(Object.keys(fe).length ? fe : { detail: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  const color = exit.tone === "gray" ? "gray" : exit.tone;
  return (
    <Modal opened onClose={onClose} title={<Text fw={600}>{exit.action}: {record._title}</Text>}>
      <Stack>
        <Text size="sm" c="dimmed">
          It stays recorded at {stageLabel(p, stage)}
          {exit.paused ? ", and resumes there when you pick it up again." : ", and can be reopened later."}
        </Text>
        {reasonField && (
          <Select label={reasonField.label} required searchable data={reasons.map((o) => ({ value: String(o.id), label: o.label }))}
            value={reason} onChange={setReason} error={errors[reasonField.key]} />
        )}
        {dateField && (
          <DateInput label={dateField.label} required valueFormat={me.date_format} minDate={new Date()}
            dateParser={dateParser(me.date_format)} placeholder={me.date_format.toLowerCase()}
            value={date} onChange={setDate} error={errors[dateField.key]} />
        )}
        <Textarea label="Note (optional)" autosize minRows={2} placeholder="What happened?" value={note}
          onChange={(e) => setNote(e.currentTarget.value)} />
        {errors.detail && <Text c="red" size="sm">{errors.detail}</Text>}
        {errors[p.status_field] && <Text c="red" size="sm">{errors[p.status_field]}</Text>}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button color={color} onClick={submit} loading={busy}>{exit.action}</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
