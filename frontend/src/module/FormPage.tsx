import { Alert, Anchor, Breadcrumbs, Button, Group, Loader, Paper, SimpleGrid, Stack, Text } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api } from "../api";
import { useMe, useModuleSchema } from "../context";
import { errorMessage, fieldErrors, notifyOk } from "../components/errors";
import { PageHeader, PageSkeleton, moduleIcon } from "../components/ui";
import type { FieldSchema, LinkValue, ModuleSchema, RecordData } from "../types";
import { emptyValue, FieldInput } from "./fields";
import UploadPage from "../vault/UploadPage";
import VaultGate from "../vault/VaultGate";
import { exitForField } from "./pipeline";

/** New records can start with a link filled in from the page address: ?firm=investors:12 */
function prefill(f: FieldSchema, raw: string | null): unknown {
  if (!raw || f.type !== "link") return undefined;
  const [module, id] = raw.split(":");
  if (!f.targets.includes(module) || !/^\d+$/.test(id ?? "")) return undefined;
  return { module, id: Number(id), title: "", archived: false };
}

/** Which fields the form shows. Pipeline stage and status are moved with the stage controls, not
 *  typed in; a new record picks its starting stage; exit details show only while they apply. */
function visible(f: FieldSchema, module: ModuleSchema, editing: boolean, record?: RecordData): boolean {
  if (!f.enabled || f.readonly) return false;
  const p = module.pipeline;
  if (!p || !f.pipeline_role) return true;
  const status = record ? String(record[p.status_field] ?? p.open_value) : p.open_value;
  switch (f.pipeline_role) {
    case "stage": return !editing;
    case "status": case "entered": return false;
    case "exit": return editing && status === exitForField(p, f.key)?.value;
  }
  return true;
}

export default function FormPage() {
  const { module: key, id } = useParams();
  if (key !== "vault") return <FormInner />;
  return <VaultGate>{id ? <FormInner /> : <VaultUpload />}</VaultGate>;
}

function VaultUpload() {
  const { module, isLoading } = useModuleSchema("vault");
  if (isLoading) return <PageSkeleton />;
  if (!module?.perms.create) return <Navigate to="/m/vault" replace />;
  return <UploadPage module={module} />;
}

function FormInner() {
  const { module: key, id } = useParams();
  const { module, isLoading } = useModuleSchema(key);
  const me = useMe();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const editing = !!id;
  // Where to go after saving or cancelling, e.g. back to the investor a contact was added from.
  const returnTo = /^\/m\/[a-z]+\/\d+$/.test(params.get("return") ?? "") ? params.get("return")! : null;
  const rec = useQuery({
    queryKey: ["record", key, id],
    queryFn: () => api<RecordData>(`m/${key}/${id}/`),
    enabled: editing && !!module,
  });

  const fields = useMemo(
    () => (module ? module.fields.filter((f) => visible(f, module, editing, rec.data)) : []),
    [module, editing, rec.data],
  );
  const [values, setValues] = useState<Record<string, unknown> | null>(null);
  // Saved values, so options removed from a list (or deactivated users) stay selectable.
  const [originals, setOriginals] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingLinked, setLoadingLinked] = useState(false);

  // Company-level fields (via a link, e.g. an outreach attempt's company) load from that record.
  async function loadLinked(viaKey: string, link: LinkValue | null) {
    const proxies = (module?.fields ?? []).filter((f) => f.via === viaKey);
    if (!proxies.length) return;
    if (!link) {
      setValues((v) => ({ ...v!, ...Object.fromEntries(proxies.map((f) => [f.key, emptyValue(f)])) }));
      return;
    }
    setLoadingLinked(true);
    try {
      const other = await api<RecordData>(`m/${link.module}/${link.id}/`);
      const loaded = Object.fromEntries(proxies.map((f) => [f.key, other[f.key] ?? emptyValue(f)]));
      setValues((v) => ({ ...v!, ...loaded }));
      setOriginals((o) => ({ ...o, ...loaded }));
    } finally {
      setLoadingLinked(false);
    }
  }

  useEffect(() => {
    if (!module || values) return;
    if (editing && !rec.data) return;
    const init: Record<string, unknown> = {};
    for (const f of module.fields) {
      if (editing) init[f.key] = rec.data![f.key];
      else if (f.pipeline_role === "stage" && module.pipeline) {
        // "+ New" under a board column starts the record in that column's stage.
        const asked = params.get(f.key);
        init[f.key] = module.pipeline.stages.some((st) => st.value === asked) ? asked : module.pipeline.stages[0].value;
      }
      else if (f.default_me && f.type === "user" && !f.multi) init[f.key] = me.id;
      else if (f.default != null && !params.get(f.key)) init[f.key] = f.default;
      else init[f.key] = prefill(f, params.get(f.key)) ?? emptyValue(f);
    }
    setValues(init);
    setOriginals(editing ? rec.data! : {});
    if (!editing) {
      // A prefilled company brings its details with it.
      for (const f of module.fields) {
        if (f.type === "link" && init[f.key] && module.fields.some((pf) => pf.via === f.key)) loadLinked(f.key, init[f.key] as LinkValue);
      }
    }
  }, [module, editing, rec.data, values, params]); // eslint-disable-line react-hooks/exhaustive-deps

  if (isLoading || (editing && rec.isLoading)) return <PageSkeleton rows={8} />;
  if (!module) return <Navigate to="/" replace />;
  if (editing ? !module.perms.edit : !module.perms.create) return <Navigate to={`/m/${module.key}`} replace />;
  if (rec.error) return <Text c="red">{errorMessage(rec.error)}</Text>;
  if (!values) return null;

  const set = (f: FieldSchema) => (v: unknown) => {
    const before = values[f.key] as LinkValue | null;
    setValues((prev) => ({ ...prev!, [f.key]: v }));
    if (errors[f.key]) setErrors((e) => { const n = { ...e }; delete n[f.key]; return n; });
    if (f.type === "link" && module.fields.some((pf) => pf.via === f.key)) {
      const after = v as LinkValue | null;
      if (before?.module !== after?.module || before?.id !== after?.id) loadLinked(f.key, after);
    }
  };

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setErrors({});
    try {
      const shownKeys = new Set(fields.map((f) => f.key));
      let body: Record<string, unknown> = Object.fromEntries(Object.entries(values!).filter(([k]) => shownKeys.has(k)));
      if (editing) {
        body = Object.fromEntries(Object.entries(body).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(rec.data![k])));
      }
      const saved = await api<RecordData>(editing ? `m/${module!.key}/${id}/` : `m/${module!.key}/`, {
        method: editing ? "PATCH" : "POST",
        body,
      });
      qc.setQueryData(["record", module!.key, String(saved.id)], saved);
      await qc.invalidateQueries({ queryKey: ["records"] });
      await qc.invalidateQueries({ queryKey: ["search"] });
      await qc.invalidateQueries({ queryKey: ["notes", module!.key, saved.id] });
      notifyOk(editing ? "Saved." : `${saved._title} added.`);
      nav(returnTo ?? `/m/${module!.key}/${saved.id}`, { replace: true });
    } catch (err) {
      const fe = fieldErrors(err);
      setErrors(fe);
      setError(Object.keys(fe).length ? "Please fix the highlighted fields." : errorMessage(err));
      window.scrollTo({ top: 0, behavior: "smooth" });
    } finally {
      setBusy(false);
    }
  }

  const title = editing ? `Edit ${rec.data!._title}` : `New ${module.singular.toLowerCase()}`;
  const cancelTo = returnTo ?? (editing ? `/m/${module.key}/${id}` : `/m/${module.key}`);
  const viaLabel = (viaKey: string) => module.fields.find((f) => f.key === viaKey)?.label.toLowerCase() ?? viaKey;

  return (
    <form onSubmit={save} className="df-form">
      <Stack gap="lg" maw={920}>
        <Breadcrumbs>
          <Anchor component={Link} to={`/m/${module.key}`} size="sm">{module.label}</Anchor>
          {editing && <Anchor component={Link} to={cancelTo} size="sm">{rec.data!._title}</Anchor>}
          <Text size="sm">{editing ? "Edit" : "New"}</Text>
        </Breadcrumbs>
        <PageHeader icon={moduleIcon(module.key)} title={title} />
        {error && <Alert color="red">{error}</Alert>}
        {module.groups.map((g) => {
          const gf = fields.filter((f) => f.group === g);
          if (!gf.length) return null;
          const vias = [...new Set(gf.filter((f) => f.via).map((f) => f.via!))];
          const missing = vias.find((v) => !values[v]);
          const own = gf.filter((f) => !f.via || !missing);
          return (
            <Paper key={g} withBorder p="lg" radius="md" className="df-fade">
              <Group justify="space-between" mb="md">
                <Text className="df-section-title">{g}</Text>
                {loadingLinked && vias.length > 0 && <Loader size="xs" />}
              </Group>
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md" verticalSpacing="md">
                {own.map((f) => (
                  <div key={f.key} style={{ gridColumn: f.type === "longtext" || f.type === "tags" ? "1 / -1" : undefined }}>
                    <FieldInput f={f} value={values[f.key]} onChange={set(f)} error={errors[f.key]} original={originals[f.key]} />
                  </div>
                ))}
              </SimpleGrid>
              {vias.length > 0 && (
                <Text size="xs" c="dimmed" mt="md">
                  {missing
                    ? `Pick the ${viaLabel(missing)} first; its details fill in here.`
                    : `These details belong to the ${viaLabel(vias[0])} and are shared with its outreach and deals.`}
                </Text>
              )}
            </Paper>
          );
        })}
        <Group justify="flex-end" className="df-savebar">
          <Button variant="default" component={Link} to={cancelTo}>Cancel</Button>
          <Button type="submit" loading={busy}>{editing ? "Save changes" : `Add ${module.singular.toLowerCase()}`}</Button>
        </Group>
      </Stack>
    </form>
  );
}
