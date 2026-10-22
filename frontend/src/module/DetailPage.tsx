import { Anchor, Badge, Breadcrumbs, Button, Grid, Group, Stack, Text } from "@mantine/core";
import { modals } from "@mantine/modals";
import { IconArrowUpRight, IconCalendarPlus, IconCheck, IconChevronRight, IconLock, IconMail, IconPencil, IconRotate, IconTrash } from "@tabler/icons-react";
import { useState } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import { api, ApiError } from "../api";
import { useApp, useModuleSchema } from "../context";
import DealTracker from "../deals/DealTracker";
import { useCompleteTask } from "../tasks/api";
import TasksPanel from "../tasks/TasksPanel";
import DocumentsPanel from "../vault/DocumentsPanel";
import FileCard from "../vault/FileCard";
import VaultGate from "../vault/VaultGate";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import { fieldIcon, moduleIcon, PageHeader, PageSkeleton, Reveal } from "../components/ui";
import { formatDateTime } from "../format";
import type { FieldSchema, LinkValue, ModuleSchema, RecordData } from "../types";
import { FieldValue, isEmpty } from "./fields";
import PipelineBar from "./PipelineBar";
import { exitForField, stageOf } from "./pipeline";
import RelatedSection from "./RelatedSection";
import { AccessPanel, RestrictedPage, type Placeholder } from "../deals/Restricted";
import Timeline from "./Timeline";
import { StuckLine } from "../insights/Stuck";
import Composer from "../mail/Composer";
import { EMAIL_MODULES } from "../mail/api";
import { SYNC_PAGES } from "../sync/api";
import { ScheduleModal, UpcomingMeetings } from "../sync/Meetings";

/** The record as a list already loaded it (lists send whole records), if any list has it. */
function fromLists(qc: QueryClient, key?: string, id?: string): RecordData | undefined {
  const n = Number(id);
  if (!key || !n) return undefined;
  for (const [, data] of qc.getQueriesData<unknown>({ queryKey: ["records", key] })) {
    const rows = Array.isArray(data) ? data : (data as { results?: unknown } | undefined)?.results;
    if (!Array.isArray(rows)) continue;
    const hit = rows.find((r) => (r as RecordData)?.id === n && typeof r === "object" && "_stamps" in (r as object));
    if (hit) return hit as RecordData;
  }
  return undefined;
}

// Record types that live inside another record (a deal's investor tracker): the page leads back there.
const PARENT_LINK: Record<string, string> = { deal_investors: "deal" };

export default function DetailPage() {
  const { module: key } = useParams();
  return key === "vault" ? <VaultGate><DetailInner /></VaultGate> : <DetailInner />;
}

function DetailInner() {
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
    // Show the row we already have from a list straight away (so the page opens without a wait and
    // the title can grow out of the row), then refresh it from the server.
    placeholderData: () => fromLists(qc, key, id),
  });
  const [composing, setComposing] = useState(false);
  const [scheduling, setScheduling] = useState(false);

  if (isLoading || rec.isLoading) return <PageSkeleton />;
  if (!module) return <Navigate to="/" replace />;
  if (rec.error instanceof ApiError && rec.error.code === "restricted") {
    return <RestrictedPage placeholder={(rec.error.body as { placeholder: Placeholder }).placeholder} />;
  }
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
          It moves to Deleted items and disappears from lists and search. It can be brought back from there.
        </Text>
      ),
      labels: { confirm: "Delete", cancel: "Cancel" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await api(`m/${module!.key}/${r.id}/`, { method: "DELETE" });
          await qc.invalidateQueries({ queryKey: ["records", module!.key] });
          notifyOk(`${r._title} deleted. You can bring it back from Deleted items.`);
          nav(owner ? `/m/${owner.module}/${owner.id}` : `/m/${module!.key}`);
        } catch (e) {
          notifyError(e, "Couldn't delete");
        }
      },
    });
  }

  const ModIcon = moduleIcon(module.key);
  const actions = (
    <>
      {module.key === "tasks" && module.perms.edit && (
        r.status === "done"
          ? <Button variant="default" leftSection={<IconRotate size={16} />} onClick={() => complete(r, false)}>Reopen</Button>
          : <Button variant="light" color="teal" leftSection={<IconCheck size={16} />} onClick={() => complete(r, true)}>Mark as done</Button>
      )}
      {module.perms.edit && EMAIL_MODULES.has(module.key) && (
        <Button variant="default" leftSection={<IconMail size={16} />} onClick={() => setComposing(true)}>Email</Button>
      )}
      {module.perms.edit && SYNC_PAGES.has(module.key) && (
        <Button variant="default" leftSection={<IconCalendarPlus size={16} />} onClick={() => setScheduling(true)}>Schedule meeting</Button>
      )}
      {module.perms.edit && (
        <Button variant="default" leftSection={<IconPencil size={16} />} component={Link} to={`/m/${module.key}/${r.id}/edit`}>Edit</Button>
      )}
      {module.perms.delete && (
        <Button variant="subtle" color="red" leftSection={<IconTrash size={16} />} onClick={archive}>Delete</Button>
      )}
    </>
  );

  return (
    <Stack gap="lg" maw={module.timeline ? 1280 : 980} className="df-record">
      <Breadcrumbs>
        {owner && ownerModule ? (
          [
            <Anchor key="m" component={Link} to={`/m/${ownerModule.key}`} size="sm">{ownerModule.label}</Anchor>,
            <Anchor key="r" component={Link} to={`/m/${owner.module}/${owner.id}`} size="sm">{owner.title}</Anchor>,
          ]
        ) : (
          <Anchor component={Link} to={`/m/${module.key}`} size="sm">{module.label}</Anchor>
        )}
        <Text size="sm" c="dimmed" truncate maw={320}>{r._title}</Text>
      </Breadcrumbs>

      <PageHeader icon={ModIcon} title={r._title} actions={actions} hero={`${module.key}:${r.id}`}
        sub={parentLink && me.perms[parentLink.module]?.view ? (
          <Anchor component={Link} to={`/m/${parentLink.module}/${parentLink.id}`} size="sm" c="dimmed">
            <Group gap={2} component="span" wrap="nowrap">{parentName} page <IconArrowUpRight size={14} /></Group>
          </Anchor>
        ) : undefined}>
        <HeaderBadges r={r} module={module} />
      </PageHeader>

      {p && <PipelineBar module={module} record={r} />}
      {p && <StuckLine module={module} record={r} />}
      {module.key === "vault" && <FileCard doc={r} />}

      <Grid gutter={{ base: "md", lg: 48 }} align="flex-start">
        <Grid.Col span={{ base: 12, lg: module.timeline ? 7 : 12 }}>
          <Stack gap="xl">
            {SYNC_PAGES.has(module.key) && <UpcomingMeetings module={module} record={r} />}
            <Properties module={module} record={r} fields={fields} />
            {module.sections.filter((s) => s.status === "ready" && s.module).map((s) => (
              <div key={s.key} className="df-fade">
                {s.panel === "access" ? <AccessPanel dealId={r.id} />
                  : s.panel === "tracker" ? <DealTracker section={s} deal={r} />
                  : s.panel === "tasks" ? <TasksPanel section={s} parent={module} record={r} />
                    : s.panel === "documents" ? <DocumentsPanel section={s} parent={module} record={r} />
                      : <RelatedSection section={s} parent={module} record={r} />}
              </div>
            ))}
            {module.sections.filter((s) => s.status === "coming").map((s) => (
              <div key={s.key}>
                <Text className="df-section-title" mb={4}>{s.label}</Text>
                <Text size="sm" c="dimmed">
                  This fills in automatically once the {moduleLabel(s.module ?? s.available_with)} module is added.
                </Text>
              </div>
            ))}
          </Stack>
        </Grid.Col>
        {module.timeline && (
          <Grid.Col span={{ base: 12, lg: 5 }} className="df-side">
            <Timeline module={module} recordId={r.id} />
          </Grid.Col>
        )}
      </Grid>

      {composing && <Composer module={module} record={r} onClose={() => setComposing(false)} />}
      {scheduling && <ScheduleModal module={module} record={r} onClose={() => setScheduling(false)} />}
      <Text size="xs" c="dimmed">
        Created by {st.created_by?.name ?? "—"} on {formatDateTime(st.created_at, me)} · Last edited by {st.updated_by?.name ?? "—"} on {formatDateTime(st.updated_at, me)}
      </Text>
    </Stack>
  );
}

/** The record's details as a list of properties, grouped under toggles. Empty properties are
 *  folded away under "N empty properties" so the page shows what's known first. */
function Properties({ module, record, fields }: { module: ModuleSchema; record: RecordData; fields: FieldSchema[] }) {
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const [showEmpty, setShowEmpty] = useState(false);
  const empty = (f: FieldSchema) => f.type !== "boolean" && isEmpty(record[f.key]);
  const emptyCount = fields.filter(empty).length;
  const groups = module.groups.map((g) => ({ g, gf: fields.filter((f) => f.group === g && (showEmpty || !empty(f))) }))
    .filter((x) => x.gf.length);
  const single = module.groups.length === 1;
  return (
    <div>
      {groups.map(({ g, gf }) => {
        const open = !closed[g];
        return (
          <div key={g}>
            {!single && (
              <div className="df-group-toggle" role="button" tabIndex={0} aria-expanded={open}
                onClick={() => setClosed((c) => ({ ...c, [g]: open }))}
                onKeyDown={(e) => e.key === "Enter" && setClosed((c) => ({ ...c, [g]: open }))}>
                <IconChevronRight size={14} className="df-chevron" data-open={open ? "" : undefined} />
                {g}
              </div>
            )}
            <Reveal open={open}>
              <div className="df-props">
                {gf.map((f) => {
                  const I = fieldIcon(f);
                  return (
                    <div key={f.key} className="df-prop">
                      <div className="df-prop-name"><I size={16} stroke={1.6} /><span title={f.label}>{f.label}</span></div>
                      <div className="df-prop-value">
                        {empty(f) ? <Text size="sm" c="gray.5">Empty</Text> : <FieldValue f={f} v={record[f.key]} />}
                      </div>
                    </div>
                  );
                })}
              </div>
            </Reveal>
          </div>
        );
      })}
      {emptyCount > 0 && (
        <div className="df-ghost-link" role="button" tabIndex={0} style={{ marginTop: 8 }}
          onClick={() => setShowEmpty((v) => !v)} onKeyDown={(e) => e.key === "Enter" && setShowEmpty((v) => !v)}>
          <IconChevronRight size={14} className="df-chevron" data-open={showEmpty ? "" : undefined} />
          {showEmpty ? `Hide the ${emptyCount} empty ${emptyCount === 1 ? "field" : "fields"}` : `Show ${emptyCount} empty ${emptyCount === 1 ? "field" : "fields"} (to see everything you can fill in)`}
        </div>
      )}
    </div>
  );
}

// A few key facts shown under the record's name, per module.
const HEADER_FIELDS: Record<string, string[]> = {
  investors: ["investor_type", "relationship_strength", "actively_deploying", "hq_city"],
  contacts: ["designation", "firm", "decision_level", "primary_contact", "relationship_strength", "city"],
  companies: ["sector", "sub_sector", "city"],
  outreach: ["transaction_type", "expected_size", "lead_banker"],
  deals: ["restricted", "transaction_type", "target_raise", "lead_banker", "exclusivity"],
  deal_investors: ["deal"],
  tasks: ["status", "priority", "due", "assigned_to", "related"],
};

function HeaderBadges({ r, module }: { r: RecordData; module: ModuleSchema }) {
  const items = (HEADER_FIELDS[module.key] ?? [])
    .map((k) => module.fields.find((f) => f.key === k && f.enabled))
    .filter((f): f is FieldSchema => !!f && !isEmpty(r[f.key]) && r[f.key] !== "unknown" && r[f.key] !== false);
  if (!items.length) return null;
  return (
    <div className="df-facts">
      {items.map((f) => (
        <span key={f.key} style={{ display: "inline-flex", alignItems: "center" }}>
          {f.type === "text" ? (
            <Text size="sm" c="dimmed">{String(r[f.key])}</Text>
          ) : f.key === "restricted" ? (
            <Badge variant="light" color="orange" leftSection={<IconLock size={12} />} tt="none">Restricted</Badge>
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
    </div>
  );
}
