import { Anchor, Badge, Breadcrumbs, Button, Center, Grid, Group, Loader, Paper, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { modals } from "@mantine/modals";
import { IconArchive, IconArrowUpRight, IconCheck, IconPencil, IconRotate } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { useApp, useModuleSchema } from "../context";
import DealTracker from "../deals/DealTracker";
import { useCompleteTask } from "../tasks/api";
import TasksPanel from "../tasks/TasksPanel";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import { formatDateTime } from "../format";
import type { FieldSchema, LinkValue, ModuleSchema, RecordData } from "../types";
import { FieldValue, isEmpty } from "./fields";
import PipelineBar from "./PipelineBar";
import { exitForField, stageOf } from "./pipeline";
import RelatedSection from "./RelatedSection";
import Timeline from "./Timeline";

// Record types that live inside another record (a deal's investor tracker): the page leads back there.
const PARENT_LINK: Record<string, string> = { deal_investors: "deal" };

export default function DetailPage() {
  const { module: key, id } = useParams();
  const { module, schema, isLoading } = useModuleSchema(key);
  const { info, me } = useApp();
  const nav = useNavigate();
  const qc = useQueryClient();
  const complete = useCompleteTask();
  const rec = useQuery({
    queryKey: ["record", key, id],
    queryFn: () => api<RecordData>(`m/${key}/${id}/`),
    enabled: !!module,
  });

  if (isLoading || rec.isLoading) return <Center py="xl"><Loader /></Center>;
  if (!module) return <Navigate to="/" replace />;
  if (rec.error || !rec.data) {
    return (
      <Stack>
        <Text c="red">{errorMessage(rec.error)}</Text>
        <Anchor component={Link} to={`/m/${module.key}`}>Back to {module.label}</Anchor>
      </Stack>
    );
  }
  const r = rec.data;
  const moduleLabel = (key?: string) => info.modules.find((m) => m.key === key)?.label ?? key ?? "";
  const p = module.pipeline;
  const state = p ? stageOf(r, p) : null;
  const shown = (f: FieldSchema) => {
    if (!f.enabled || f.key === module.title_field) return false;
    if (!p || !f.pipeline_role) return true;
    // Stage and status live in the stage bar; exit details only when they apply.
    if (f.pipeline_role === "stage" || f.pipeline_role === "status") return false;
    if (f.pipeline_role === "exit") return state!.status === exitForField(p, f.key)?.value;
    return true;
  };
  const fields = module.fields.filter(shown);
  const st = r._stamps;
  const titleLink = module.fields.find((f) => f.key === module.title_field && f.type === "link");
  const parentLink = titleLink ? (r[titleLink.key] as LinkValue | null) : null;
  const parentName = schema?.modules.find((m) => m.key === parentLink?.module)?.singular ?? "Linked record";
  const owner = PARENT_LINK[module.key] ? (r[PARENT_LINK[module.key]] as LinkValue | null) : null;
  const ownerModule = owner ? schema?.modules.find((m) => m.key === owner.module) : undefined;

  function archive() {
    modals.openConfirmModal({
      title: `Delete ${r._title}?`,
      children: (
        <Text size="sm">
          It moves to the Archive and disappears from lists and search. An admin can restore it from there.
        </Text>
      ),
      labels: { confirm: "Delete", cancel: "Cancel" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await api(`m/${module!.key}/${r.id}/`, { method: "DELETE" });
          await qc.invalidateQueries({ queryKey: ["records", module!.key] });
          notifyOk(`${r._title} moved to the Archive.`);
          nav(owner ? `/m/${owner.module}/${owner.id}` : `/m/${module!.key}`);
        } catch (e) {
          notifyError(e, "Couldn't delete");
        }
      },
    });
  }

  return (
    <Stack gap="lg" maw={module.timeline ? 1400 : 1200}>
      <Breadcrumbs>
        {owner && ownerModule ? (
          [
            <Anchor key="m" component={Link} to={`/m/${ownerModule.key}`} size="sm">{ownerModule.label}</Anchor>,
            <Anchor key="r" component={Link} to={`/m/${owner.module}/${owner.id}`} size="sm">{owner.title}</Anchor>,
          ]
        ) : (
          <Anchor component={Link} to={`/m/${module.key}`} size="sm">{module.label}</Anchor>
        )}
        <Text size="sm">{r._title}</Text>
      </Breadcrumbs>
      <Group justify="space-between" align="flex-start">
        <div>
          <Group gap="sm" align="baseline">
            <Title order={2}>{r._title}</Title>
            {parentLink && me.perms[parentLink.module]?.view && (
              <Anchor component={Link} to={`/m/${parentLink.module}/${parentLink.id}`} size="sm">
                <Group gap={2} component="span" wrap="nowrap">{parentName} page <IconArrowUpRight size={14} /></Group>
              </Anchor>
            )}
          </Group>
          <HeaderBadges r={r} module={module} />
        </div>
        <Group gap="xs">
          {module.key === "tasks" && module.perms.edit && (
            r.status === "done"
              ? <Button variant="default" leftSection={<IconRotate size={16} />} onClick={() => complete(r, false)}>Reopen</Button>
              : <Button variant="light" color="teal" leftSection={<IconCheck size={16} />} onClick={() => complete(r, true)}>Mark as done</Button>
          )}
          {module.perms.delete && (
            <Button variant="default" color="red" leftSection={<IconArchive size={16} />} onClick={archive}>Delete</Button>
          )}
          {module.perms.edit && (
            <Button leftSection={<IconPencil size={16} />} component={Link} to={`/m/${module.key}/${r.id}/edit`}>Edit</Button>
          )}
        </Group>
      </Group>

      {p && <PipelineBar module={module} record={r} />}

      <Grid gutter="lg" align="flex-start">
      <Grid.Col span={{ base: 12, lg: module.timeline ? 8 : 12 }}>
      <Stack gap="lg">
      {module.sections.filter((s) => s.status === "ready" && s.module).map((s) => (
        s.panel === "tracker" ? <DealTracker key={s.key} section={s} deal={r} />
          : s.panel === "tasks" ? <TasksPanel key={s.key} section={s} parent={module} record={r} />
            : <RelatedSection key={s.key} section={s} parent={module} record={r} />
      ))}

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
        {module.groups.map((g) => {
          const gf = fields.filter((f) => f.group === g);
          if (!gf.length) return null;
          return (
            <Paper key={g} withBorder p="lg" radius="md">
              <Text fw={600} mb="md">{g}</Text>
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md" verticalSpacing="md">
                {gf.map((f) => (
                  <div key={f.key} style={{ gridColumn: f.type === "longtext" ? "1 / -1" : undefined }}>
                    <Text className="df-field-label" mb={2}>{f.label}</Text>
                    <FieldValue f={f} v={r[f.key]} />
                  </div>
                ))}
              </SimpleGrid>
            </Paper>
          );
        })}
        {module.sections.filter((s) => s.status === "coming").map((s) => (
          <Paper key={s.key} withBorder p="lg" radius="md">
            <Text fw={600} mb="xs">{s.label}</Text>
            <Text size="sm" c="dimmed">
              This fills in automatically once the {moduleLabel(s.module ?? s.available_with)} module is added.
            </Text>
          </Paper>
        ))}
      </SimpleGrid>
      </Stack>
      </Grid.Col>
      {module.timeline && (
        <Grid.Col span={{ base: 12, lg: 4 }}>
          <Timeline module={module} recordId={r.id} />
        </Grid.Col>
      )}
      </Grid>

      <Text size="xs" c="dimmed">
        Created by {st.created_by?.name ?? "—"} on {formatDateTime(st.created_at, me)} · Last edited by {st.updated_by?.name ?? "—"} on {formatDateTime(st.updated_at, me)}
      </Text>
    </Stack>
  );
}

// A few key facts shown under the record's name, per module.
const HEADER_FIELDS: Record<string, string[]> = {
  investors: ["investor_type", "relationship_strength", "actively_deploying", "hq_city"],
  contacts: ["designation", "firm", "decision_level", "primary_contact", "relationship_strength", "city"],
  companies: ["sector", "sub_sector", "city"],
  outreach: ["transaction_type", "expected_size", "lead_banker"],
  deals: ["transaction_type", "target_raise", "lead_banker", "exclusivity"],
  deal_investors: ["deal"],
  tasks: ["status", "priority", "due", "assigned_to", "related"],
};

function HeaderBadges({ r, module }: { r: RecordData; module: ModuleSchema }) {
  const items = (HEADER_FIELDS[module.key] ?? [])
    .map((k) => module.fields.find((f) => f.key === k && f.enabled))
    .filter((f): f is FieldSchema => !!f && !isEmpty(r[f.key]) && r[f.key] !== "unknown" && r[f.key] !== false);
  if (!items.length) return null;
  return (
    <Group gap="sm" mt={6}>
      {items.map((f) => (
        <span key={f.key}>
          {f.type === "text" ? (
            <Text size="sm" c="dimmed">{String(r[f.key])}</Text>
          ) : f.type === "boolean" ? (
            <Badge variant="light" color="teal">{f.label}</Badge>
          ) : f.type === "tristate" ? (
            <Badge variant="dot" color={r[f.key] === "yes" ? "teal" : "red"} tt="none">
              {r[f.key] === "yes" ? f.label : `Not ${f.label.toLowerCase()}`}
            </Badge>
          ) : (
            <FieldValue f={f} v={r[f.key]} compact />
          )}
        </span>
      ))}
    </Group>
  );
}
