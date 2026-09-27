import { Alert, Anchor, Breadcrumbs, Button, Center, Group, Loader, Paper, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { useModuleSchema } from "../context";
import { errorMessage, fieldErrors, notifyOk } from "../components/errors";
import type { RecordData } from "../types";
import { emptyValue, FieldInput } from "./fields";

export default function FormPage() {
  const { module: key, id } = useParams();
  const { module, isLoading } = useModuleSchema(key);
  const nav = useNavigate();
  const qc = useQueryClient();
  const editing = !!id;
  const rec = useQuery({
    queryKey: ["record", key, id],
    queryFn: () => api<RecordData>(`m/${key}/${id}/`),
    enabled: editing && !!module,
  });

  const fields = useMemo(() => module?.fields.filter((f) => f.enabled) ?? [], [module]);
  const [values, setValues] = useState<Record<string, unknown> | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!module || values) return;
    if (editing && !rec.data) return;
    const init: Record<string, unknown> = {};
    for (const f of fields) init[f.key] = editing ? rec.data![f.key] : emptyValue(f);
    setValues(init);
  }, [module, fields, editing, rec.data, values]);

  if (isLoading || (editing && rec.isLoading)) return <Center py="xl"><Loader /></Center>;
  if (!module) return <Navigate to="/" replace />;
  if (editing ? !module.perms.edit : !module.perms.create) return <Navigate to={`/m/${module.key}`} replace />;
  if (rec.error) return <Text c="red">{errorMessage(rec.error)}</Text>;
  if (!values) return null;

  const set = (k: string) => (v: unknown) => {
    setValues((prev) => ({ ...prev!, [k]: v }));
    if (errors[k]) setErrors((e) => { const n = { ...e }; delete n[k]; return n; });
  };

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setErrors({});
    try {
      let body: Record<string, unknown> = values!;
      if (editing) {
        body = Object.fromEntries(
          Object.entries(values!).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(rec.data![k])),
        );
      }
      const saved = await api<RecordData>(editing ? `m/${module!.key}/${id}/` : `m/${module!.key}/`, {
        method: editing ? "PATCH" : "POST",
        body,
      });
      qc.setQueryData(["record", module!.key, String(saved.id)], saved);
      await qc.invalidateQueries({ queryKey: ["records", module!.key] });
      await qc.invalidateQueries({ queryKey: ["search"] });
      notifyOk(editing ? "Saved." : `${saved._title} added.`);
      nav(`/m/${module!.key}/${saved.id}`, { replace: true });
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
  const cancelTo = editing ? `/m/${module.key}/${id}` : `/m/${module.key}`;

  return (
    <form onSubmit={save}>
      <Stack gap="lg" maw={1000}>
        <Breadcrumbs>
          <Anchor component={Link} to={`/m/${module.key}`} size="sm">{module.label}</Anchor>
          {editing && <Anchor component={Link} to={cancelTo} size="sm">{rec.data!._title}</Anchor>}
          <Text size="sm">{editing ? "Edit" : "New"}</Text>
        </Breadcrumbs>
        <Title order={2}>{title}</Title>
        {error && <Alert color="red">{error}</Alert>}
        {module.groups.map((g) => {
          const gf = fields.filter((f) => f.group === g);
          if (!gf.length) return null;
          return (
            <Paper key={g} withBorder p="lg" radius="md">
              <Text fw={600} mb="md">{g}</Text>
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md" verticalSpacing="md">
                {gf.map((f) => (
                  <div key={f.key} style={{ gridColumn: f.type === "longtext" || f.type === "tags" ? "1 / -1" : undefined }}>
                    <FieldInput f={f} value={values[f.key]} onChange={set(f.key)} error={errors[f.key]}
                      original={editing ? rec.data![f.key] : undefined} />
                  </div>
                ))}
              </SimpleGrid>
            </Paper>
          );
        })}
        <Group justify="flex-end" pb="xl">
          <Button variant="default" component={Link} to={cancelTo}>Cancel</Button>
          <Button type="submit" loading={busy}>{editing ? "Save changes" : `Add ${module.singular.toLowerCase()}`}</Button>
        </Group>
      </Stack>
    </form>
  );
}
