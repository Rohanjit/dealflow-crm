import {
  Alert, Anchor, Badge, Breadcrumbs, Button, Center, Checkbox, FileInput, Group, List, Loader, Paper, ScrollArea,
  SegmentedControl, Select, Stack, Stepper, Table, Text, Title,
} from "@mantine/core";
import { IconAlertTriangle, IconCheck, IconCopy, IconFileSpreadsheet, IconX } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import { api } from "../api";
import { useModuleSchema } from "../context";
import { errorMessage } from "../components/errors";
import type { ModuleSchema, Schema } from "../types";

type MapEntry = { field: string | null; currency: string; unit: string };
type Upload = {
  token: string; filename: string; headers: string[]; sample: string[][]; row_count: number;
  mapping: MapEntry[]; fields: { key: string; label: string; type: string; required: boolean }[]; can_add_options: boolean;
};
type RowResult = {
  row: number; title: string; errors: Record<string, string>; warnings: string[];
  duplicate_of: { id: number; title: string }[]; duplicate_of_row: number | null;
};
type Check = {
  summary: { total: number; ok: number; with_errors: number; duplicates: number; new_options: Record<string, string[]> };
  rows: RowResult[];
};
type Result = { created: number; skipped: number; failed: { row: number; errors: Record<string, string> }[] };

export default function ImportPage() {
  const { module: key } = useParams();
  const { module, schema, isLoading } = useModuleSchema(key);
  if (isLoading) return <Center py="xl"><Loader /></Center>;
  if (!module || !schema || !module.perms.create) return <Navigate to="/" replace />;
  return <Wizard module={module} schema={schema} />;
}

function Wizard({ module, schema }: { module: ModuleSchema; schema: Schema }) {
  const qc = useQueryClient();
  const [step, setStep] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<Upload | null>(null);
  const [mapping, setMapping] = useState<MapEntry[]>([]);
  const [addMissing, setAddMissing] = useState(false);
  const [check, setCheck] = useState<Check | null>(null);
  const [include, setInclude] = useState<Set<number>>(new Set());
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError("");
    try {
      return await fn();
    } catch (e) {
      setError(errorMessage(e));
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  async function doUpload() {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    const up = await run(() => api<Upload>(`m/${module.key}/import/upload/`, { form }));
    if (up) {
      setUpload(up);
      setMapping(up.mapping);
      setStep(1);
    }
  }

  async function doCheck() {
    const c = await run(() => api<Check>(`m/${module.key}/import/check/`, {
      body: { token: upload!.token, mapping, add_missing_options: addMissing },
    }));
    if (c) {
      setCheck(c);
      setInclude(new Set(c.rows.filter((r) => !Object.keys(r.errors).length && !r.duplicate_of.length && !r.duplicate_of_row).map((r) => r.row)));
      setStep(2);
    }
  }

  async function doCommit() {
    const r = await run(() => api<Result>(`m/${module.key}/import/commit/`, {
      body: { token: upload!.token, mapping, add_missing_options: addMissing, include_rows: [...include] },
    }));
    if (r) {
      setResult(r);
      setStep(3);
      await qc.invalidateQueries({ queryKey: ["records", module.key] });
      if (addMissing) await qc.invalidateQueries({ queryKey: ["schema"] });
    }
  }

  function restart() {
    setStep(0); setFile(null); setUpload(null); setCheck(null); setResult(null); setError(""); setAddMissing(false);
  }

  return (
    <Stack gap="lg" maw={1200}>
      <Breadcrumbs>
        <Anchor component={Link} to={`/m/${module.key}`} size="sm">{module.label}</Anchor>
        <Text size="sm">Import</Text>
      </Breadcrumbs>
      <Title order={2}>Import {module.label.toLowerCase()}</Title>
      <Stepper active={step} size="sm" allowNextStepsSelect={false} onStepClick={(s) => s < step && step < 3 && setStep(s)}>
        <Stepper.Step label="Upload" description="CSV or Excel" />
        <Stepper.Step label="Match columns" />
        <Stepper.Step label="Review" description="Errors and duplicates" />
        <Stepper.Step label="Done" />
      </Stepper>
      {error && <Alert color="red" icon={<IconAlertTriangle size={18} />}>{error}</Alert>}

      {step === 0 && (
        <Paper withBorder p="xl" radius="md">
          <Stack maw={520}>
            <Text size="sm">
              Upload a sheet with one {module.singular.toLowerCase()} per row and column names in the first row. You'll match
              the columns to Dealflow fields next, and see errors and possible duplicates before anything is saved.
            </Text>
            <FileInput label="File" placeholder="Choose a .xlsx or .csv file" accept=".xlsx,.csv" value={file} onChange={setFile}
              leftSection={<IconFileSpreadsheet size={18} />} clearable />
            <Group><Button onClick={doUpload} disabled={!file} loading={busy}>Upload and continue</Button></Group>
          </Stack>
        </Paper>
      )}

      {step === 1 && upload && (
        <MatchStep upload={upload} mapping={mapping} setMapping={setMapping} schema={schema} module={module}
          addMissing={addMissing} setAddMissing={setAddMissing} busy={busy} onBack={restart} onNext={doCheck} />
      )}

      {step === 2 && check && (
        <ReviewStep check={check} include={include} setInclude={setInclude} module={module} busy={busy}
          onBack={() => setStep(1)} onNext={doCommit} />
      )}

      {step === 3 && result && (
        <Paper withBorder p="xl" radius="md">
          <Stack>
            <Group gap="xs"><IconCheck color="var(--mantine-color-teal-6)" /><Title order={3}>{result.created} {result.created === 1 ? module.singular.toLowerCase() : module.label.toLowerCase()} imported</Title></Group>
            {result.skipped > 0 && <Text size="sm" c="dimmed">{result.skipped} rows were left out.</Text>}
            {result.failed.length > 0 && (
              <Alert color="orange" title={`${result.failed.length} rows couldn't be imported`}>
                <List size="sm">
                  {result.failed.slice(0, 20).map((f) => <List.Item key={f.row}>Row {f.row}: {Object.values(f.errors).join("; ")}</List.Item>)}
                </List>
              </Alert>
            )}
            <Group>
              <Button component={Link} to={`/m/${module.key}`}>Go to {module.label}</Button>
              <Button variant="default" onClick={restart}>Import another file</Button>
            </Group>
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}

function MatchStep({
  upload, mapping, setMapping, schema, module, addMissing, setAddMissing, busy, onBack, onNext,
}: {
  upload: Upload; mapping: MapEntry[]; setMapping: (m: MapEntry[]) => void; schema: Schema; module: ModuleSchema;
  addMissing: boolean; setAddMissing: (b: boolean) => void; busy: boolean; onBack: () => void; onNext: () => void;
}) {
  const used = new Set(mapping.map((m) => m.field).filter(Boolean));
  const fieldType = Object.fromEntries(upload.fields.map((f) => [f.key, f.type]));
  const titleMapped = used.has(module.title_field);
  const hasDropdown = mapping.some((m) => m.field && module.fields.find((f) => f.key === m.field)?.type === "dropdown");
  const update = (i: number, patch: Partial<MapEntry>) => setMapping(mapping.map((m, j) => (j === i ? { ...m, ...patch } : m)));
  const currencies = schema.currencies.map((c) => c.code);
  const units = schema.units.map((u) => ({ value: u.value || "none", label: u.value || "—" }));
  const titleLabel = module.fields.find((f) => f.key === module.title_field)?.label;

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        <b>{upload.filename}</b> · {upload.row_count} rows. We matched what we could; check each column. Columns set to “Don't import” are ignored.
      </Text>
      <Paper withBorder radius="md">
        <ScrollArea>
          <Table className="df-table" verticalSpacing="sm" miw={860}>
            <Table.Thead>
              <Table.Tr><Table.Th>Column in your file</Table.Th><Table.Th>Sample values</Table.Th><Table.Th w={300}>Dealflow field</Table.Th><Table.Th w={230}>Currency / unit if not in the cell</Table.Th></Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {upload.headers.map((h, i) => {
                const m = mapping[i];
                const samples = upload.sample.map((r) => r[i]).filter(Boolean).slice(0, 3);
                return (
                  <Table.Tr key={i}>
                    <Table.Td><Text size="sm" fw={500}>{h || <i>(no header)</i>}</Text></Table.Td>
                    <Table.Td maw={280}><Text size="xs" c="dimmed" lineClamp={2}>{samples.join(" · ") || "—"}</Text></Table.Td>
                    <Table.Td>
                      <Select size="sm" searchable
                        data={[{ value: "", label: "Don't import" }, ...upload.fields.map((f) => ({
                          value: f.key, label: f.label + (f.required ? " *" : ""), disabled: used.has(f.key) && m.field !== f.key,
                        }))]}
                        value={m.field ?? ""} onChange={(v) => update(i, { field: v || null })} />
                    </Table.Td>
                    <Table.Td>
                      {m.field && fieldType[m.field] === "money" ? (
                        <Group gap={4} wrap="nowrap">
                          <Select size="sm" w={100} placeholder="Cur." data={currencies} value={m.currency || null} onChange={(v) => update(i, { currency: v ?? "" })} clearable />
                          <Select size="sm" w={90} data={units} value={m.unit || "none"} onChange={(v) => update(i, { unit: !v || v === "none" ? "" : v })} />
                        </Group>
                      ) : null}
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      </Paper>
      {hasDropdown && upload.can_add_options && (
        <Checkbox checked={addMissing} onChange={(e) => setAddMissing(e.currentTarget.checked)}
          label="Add values that aren't in the dropdown lists yet (for example a new sector)"
          description="Otherwise those values are left blank and listed as warnings." />
      )}
      {!titleMapped && <Alert color="orange">Match a column to {titleLabel}; it's required.</Alert>}
      <Group justify="space-between">
        <Button variant="default" onClick={onBack}>Choose another file</Button>
        <Button onClick={onNext} loading={busy} disabled={!titleMapped}>Check rows</Button>
      </Group>
    </Stack>
  );
}

type Filter = "all" | "ready" | "dups" | "errors";

function ReviewStep({
  check, include, setInclude, module, busy, onBack, onNext,
}: {
  check: Check; include: Set<number>; setInclude: (s: Set<number>) => void; module: ModuleSchema; busy: boolean;
  onBack: () => void; onNext: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const s = check.summary;
  const isDup = (r: RowResult) => r.duplicate_of.length > 0 || !!r.duplicate_of_row;
  const hasErr = (r: RowResult) => Object.keys(r.errors).length > 0;
  const rows = useMemo(() => check.rows.filter((r) =>
    filter === "all" ? true : filter === "errors" ? hasErr(r) : filter === "dups" ? !hasErr(r) && isDup(r) : !hasErr(r) && !isDup(r),
  ), [check, filter]);
  const toggle = (n: number) => {
    const next = new Set(include);
    if (next.has(n)) next.delete(n); else next.add(n);
    setInclude(next);
  };
  const newOpts = Object.entries(s.new_options);

  return (
    <Stack>
      <Group gap="sm">
        <Badge size="lg" color="teal" variant="light" leftSection={<IconCheck size={14} />}>{s.ok} ready</Badge>
        <Badge size="lg" color="orange" variant="light" leftSection={<IconCopy size={14} />}>{s.duplicates} possible duplicates</Badge>
        <Badge size="lg" color="red" variant="light" leftSection={<IconX size={14} />}>{s.with_errors} with errors</Badge>
      </Group>
      <Text size="sm" c="dimmed">
        Possible duplicates are left out unless you tick them. Rows with errors can't be imported; fix them in your file and upload again.
      </Text>
      {newOpts.length > 0 && (
        <Alert color="blue" title="These will be added to the dropdown lists">
          {newOpts.map(([list, vals]) => <Text key={list} size="sm"><b>{list}:</b> {vals.join(", ")}</Text>)}
        </Alert>
      )}
      <SegmentedControl w="fit-content" value={filter} onChange={(v) => setFilter(v as Filter)} data={[
        { value: "all", label: `All (${s.total})` }, { value: "ready", label: "Ready" },
        { value: "dups", label: "Duplicates" }, { value: "errors", label: "Errors" },
      ]} />
      <Paper withBorder radius="md">
        <ScrollArea.Autosize mah={560}>
          <Table className="df-table" verticalSpacing="xs" stickyHeader>
            <Table.Thead>
              <Table.Tr><Table.Th w={40} /><Table.Th w={60}>Row</Table.Th><Table.Th>{module.fields.find((f) => f.key === module.title_field)?.label}</Table.Th><Table.Th>Notes</Table.Th></Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((r) => (
                <Table.Tr key={r.row}>
                  <Table.Td><Checkbox disabled={hasErr(r)} checked={include.has(r.row)} onChange={() => toggle(r.row)} aria-label={`Include row ${r.row}`} /></Table.Td>
                  <Table.Td><Text size="sm" c="dimmed">{r.row}</Text></Table.Td>
                  <Table.Td><Text size="sm" fw={500}>{r.title || <i>(blank)</i>}</Text></Table.Td>
                  <Table.Td>
                    <Stack gap={2}>
                      {Object.entries(r.errors).map(([k, v]) => <Text key={k} size="xs" c="red">{k}: {v}</Text>)}
                      {r.duplicate_of.map((d) => (
                        <Text key={d.id} size="xs" c="orange.8">Looks like <Anchor size="xs" component={Link} to={`/m/${module.key}/${d.id}`} target="_blank">{d.title}</Anchor>, already in Dealflow</Text>
                      ))}
                      {r.duplicate_of_row && <Text size="xs" c="orange.8">Same as row {r.duplicate_of_row} in this file</Text>}
                      {r.warnings.map((w, i) => <Text key={i} size="xs" c="dimmed">{w}</Text>)}
                      {!hasErr(r) && !isDup(r) && !r.warnings.length && <Text size="xs" c="teal">Ready</Text>}
                    </Stack>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea.Autosize>
      </Paper>
      <Group justify="space-between">
        <Button variant="default" onClick={onBack}>Back to matching</Button>
        <Button onClick={onNext} loading={busy} disabled={include.size === 0}>
          Import {include.size} {include.size === 1 ? "row" : "rows"}
        </Button>
      </Group>
    </Stack>
  );
}
