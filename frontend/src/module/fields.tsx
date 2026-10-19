// Rendering and editing of every field type, driven by the backend's field schema.

import {
  Anchor, Badge, Group, MultiSelect, NumberInput, SegmentedControl, Select, Switch, TagsInput, Text, TextInput,
  Textarea,
} from "@mantine/core";
import { DateInput, TimeInput } from "@mantine/dates";
import { IconBrandWhatsapp, IconCheck } from "@tabler/icons-react";
import dayjs from "dayjs";
import customParseFormat from "dayjs/plugin/customParseFormat";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { useMe, useSchema, useUsers } from "../context";
import {
  formatBirthday, formatBytes, formatDate, formatDateAndTime, formatMoney, groupDigits, whatsappLink, type Prefs,
} from "../format";
import type { FieldSchema, LinkValue, Money, Schema, UserLookup } from "../types";
import BirthdayInput from "./BirthdayInput";
import LinkInput, { MultiLinkInput } from "./LinkInput";

dayjs.extend(customParseFormat);

export const EMPTY_MONEY: Money = { amount: null, currency: "", unit: "" };

/** Reads a typed date in the user's format (or ISO, or "12 Mar 2026") for date pickers. */
export function dateParser(dateFormat: string) {
  return (s: string) => {
    const d = dayjs(s, [dateFormat, "YYYY-MM-DD", "D MMM YYYY", "D MMMM YYYY"], true);
    return d.isValid() ? d.toDate() : null;
  };
}

export function emptyValue(f: FieldSchema): unknown {
  if (f.multi) return [];
  switch (f.type) {
    case "money": return { ...EMPTY_MONEY };
    case "tags": return [];
    case "dropdown": return f.multi ? [] : null;
    case "boolean": return false;
    case "tristate": return "unknown";
    case "integer": case "decimal": case "percent": case "date": case "user": case "link": case "birthday":
    case "datetime": return null;
    default: return "";
  }
}

export function isEmpty(v: unknown): boolean {
  if (v === null || v === undefined || v === "") return true;
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === "object" && "amount" in (v as Money)) return (v as Money).amount === null || (v as Money).amount === "";
  return false;
}

function optionLabel(schema: Schema | undefined, list: string | null, id: number): { label: string; active: boolean } {
  const opt = list ? schema?.choice_lists[list]?.options.find((o) => o.id === id) : undefined;
  return opt ? { label: opt.label, active: opt.is_active } : { label: `#${id}`, active: false };
}

/** Plain-text value (for tables and exports on screen). */
export function textValue(f: FieldSchema, v: unknown, ctx: { schema?: Schema; users?: UserLookup[]; prefs: Prefs }): string {
  if (isEmpty(v) && f.type !== "boolean") return "";
  switch (f.type) {
    case "money": return formatMoney(v as Money, ctx.prefs);
    case "date": return formatDate(v as string, ctx.prefs);
    case "datetime": return formatDateAndTime(v as string, ctx.prefs);
    case "integer":
      if (f.key === "file_size") return formatBytes(v as number);
      return f.key.includes("vintage") || f.key.includes("year") ? String(v) : groupDigits(v as number, ctx.prefs.number_grouping);
    case "decimal": return groupDigits(v as string, ctx.prefs.number_grouping);
    case "percent": return `${v}%`;
    case "boolean": return v ? "Yes" : "No";
    case "tristate": case "choice": return f.choices.find((c) => c.value === v)?.label ?? String(v);
    case "dropdown":
      if (f.multi) return (v as number[]).map((id) => optionLabel(ctx.schema, f.choice_list, id).label).join(", ");
      return optionLabel(ctx.schema, f.choice_list, v as number).label;
    case "user":
      if (f.multi) return (v as number[]).map((id) => ctx.users?.find((u) => u.id === id)?.full_name ?? "").filter(Boolean).join(", ");
      return ctx.users?.find((u) => u.id === v)?.full_name ?? "";
    case "tags": return (v as string[]).join(", ");
    case "link": return f.multi ? (v as LinkValue[]).map((l) => l.title).join(", ") : (v as LinkValue).title;
    case "birthday": return formatBirthday(v as string, ctx.prefs);
    default: return String(v);
  }
}

function LinkAnchor({ l }: { l: LinkValue }) {
  const me = useMe();
  const canOpen = me.perms[l.module]?.view;
  return (
    <Group gap={6} wrap="nowrap">
      {canOpen ? (
        <Anchor component={Link} to={`/m/${l.module}/${l.id}`} size="sm" onClick={(e) => e.stopPropagation()}>{l.title}</Anchor>
      ) : (
        <Text size="sm">{l.title}</Text>
      )}
      {l.archived && <Badge size="xs" variant="light" color="gray">Archived</Badge>}
    </Group>
  );
}

/** One dropdown option shown as a coloured tag; the colour follows the option, so it never changes. */
export function OptionTag({ id, label, removed = false }: { id: number; label: string; removed?: boolean }) {
  return (
    <span className={`df-tag df-tag-${id % 9}`} data-removed={removed ? "" : undefined} title={removed ? `${label} (removed from the list)` : label}>
      {label}
    </span>
  );
}

const STRENGTH_COLOR: Record<string, string> = { cold: "blue", warm: "orange", strong: "teal" };
const TRI_COLOR: Record<string, string> = { yes: "teal", no: "red", unknown: "gray" };

/** Rich read-only display for detail pages and list cells. */
export function FieldValue({ f, v, compact = false }: { f: FieldSchema; v: unknown; compact?: boolean }) {
  const me = useMe();
  const { data: schema } = useSchema();
  const { data: users } = useUsers();
  const ctx = { schema, users, prefs: me };
  if (isEmpty(v) && f.type !== "boolean") return <Text c="dimmed" size="sm">—</Text>;

  let out: ReactNode;
  switch (f.type) {
    case "url": {
      const href = String(v);
      out = (
        <Anchor href={href} target="_blank" rel="noopener noreferrer" size="sm" onClick={(e) => e.stopPropagation()}>
          {href.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}
        </Anchor>
      );
      break;
    }
    case "email":
      out = <Anchor href={`mailto:${v}`} size="sm" onClick={(e) => e.stopPropagation()}>{String(v)}</Anchor>;
      break;
    case "phone": {
      const phone = String(v);
      const wa = f.key.includes("mobile") ? whatsappLink(phone) : null;
      out = (
        <Group gap={6} wrap="nowrap">
          <Anchor href={`tel:${phone.replace(/[^\d+]/g, "")}`} size="sm" onClick={(e) => e.stopPropagation()} style={{ whiteSpace: "nowrap" }}>{phone}</Anchor>
          {wa && (
            <Anchor href={wa} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} aria-label="WhatsApp" c="teal" lh={0}>
              <IconBrandWhatsapp size={16} />
            </Anchor>
          )}
        </Group>
      );
      break;
    }
    case "link":
      out = f.multi ? (
        <Group gap={4} wrap={compact ? "nowrap" : "wrap"} style={{ columnGap: 10 }}>
          {(v as LinkValue[]).map((l, i) => (
            <Group key={`${l.module}:${l.id}`} gap={0} wrap="nowrap">
              <LinkAnchor l={l} />
              {i < (v as LinkValue[]).length - 1 && <Text size="sm" c="dimmed">,</Text>}
            </Group>
          ))}
        </Group>
      ) : <LinkAnchor l={v as LinkValue} />;
      break;
    case "boolean":
      out = compact
        ? (v ? <IconCheck size={16} color="var(--mantine-color-teal-7)" aria-label="Yes" /> : <Text c="dimmed" size="sm">—</Text>)
        : <Text size="sm">{v ? "Yes" : "No"}</Text>;
      break;
    case "longtext":
      out = <Text size="sm" className="df-prewrap" lineClamp={compact ? 2 : undefined}>{String(v)}</Text>;
      break;
    case "dropdown":
      {
        const ids = f.multi ? (v as number[]) : [v as number];
        const shown = compact ? ids.slice(0, 2) : ids;
        out = (
          <div className="df-tags" data-compact={compact ? "" : undefined}>
            {shown.map((id) => {
              const o = optionLabel(schema, f.choice_list, id);
              return <OptionTag key={id} id={id} label={o.label} removed={!o.active} />;
            })}
            {compact && ids.length > 2 && <Text size="xs" c="dimmed" style={{ alignSelf: "center" }}>+{ids.length - 2}</Text>}
          </div>
        );
      }
      break;
    case "tags":
      out = (
        <div className="df-tags" data-compact={compact ? "" : undefined}>
          {(v as string[]).map((t) => <span key={t} className="df-tag df-tag-0">{t}</span>)}
        </div>
      );
      break;
    case "choice":
      out = f.key.includes("strength")
        ? <Badge variant="light" color={STRENGTH_COLOR[v as string] ?? "gray"}>{textValue(f, v, ctx)}</Badge>
        : <Text size="sm">{textValue(f, v, ctx)}</Text>;
      break;
    case "tristate":
      out = <Badge variant="dot" color={TRI_COLOR[v as string]}>{textValue(f, v, ctx)}</Badge>;
      break;
    default:
      out = <Text size="sm" style={{ whiteSpace: compact ? "nowrap" : undefined }}>{textValue(f, v, ctx)}</Text>;
  }
  return <>{out}</>;
}

/** Editor for one field. */
export function FieldInput({
  f, value, onChange, error, original,
}: {
  f: FieldSchema;
  value: unknown;
  onChange: (v: unknown) => void;
  error?: string;
  original?: unknown; // the saved value, so removed options / deactivated users stay selectable
}) {
  const me = useMe();
  const { data: schema } = useSchema();
  const { data: users } = useUsers();
  const common = { label: f.label, required: f.required, error, description: f.help || undefined };
  const grouping = me.number_grouping === "indian" ? "lakh" : "thousand";

  switch (f.type) {
    case "text": case "phone":
      return <TextInput {...common} value={(value as string) ?? ""} onChange={(e) => onChange(e.currentTarget.value)} />;
    case "email":
      return <TextInput {...common} type="email" value={(value as string) ?? ""} onChange={(e) => onChange(e.currentTarget.value)} />;
    case "url":
      return <TextInput {...common} placeholder="example.com" value={(value as string) ?? ""} onChange={(e) => onChange(e.currentTarget.value)} />;
    case "longtext":
      return <Textarea {...common} autosize minRows={2} maxRows={12} value={(value as string) ?? ""} onChange={(e) => onChange(e.currentTarget.value)} />;
    case "integer":
      return (
        <NumberInput {...common} allowDecimal={false} hideControls
          thousandSeparator={f.key.includes("vintage") ? undefined : ","} thousandsGroupStyle={grouping}
          value={(value as number) ?? ""} onChange={(v) => onChange(v === "" ? null : v)} />
      );
    case "decimal": case "percent":
      return (
        <NumberInput {...common} hideControls thousandSeparator={f.type === "decimal" ? "," : undefined}
          thousandsGroupStyle={grouping} suffix={f.type === "percent" ? "%" : undefined}
          value={(value as string) ?? ""} onChange={(v) => onChange(v === "" ? null : String(v))} />
      );
    case "date":
      return (
        <DateInput {...common} clearable valueFormat={me.date_format} placeholder={me.date_format.toLowerCase()}
          dateParser={dateParser(me.date_format)}
          value={value ? dayjs(value as string).toDate() : null}
          onChange={(d) => onChange(d ? dayjs(d).format("YYYY-MM-DD") : null)} />
      );
    case "boolean":
      return <Switch label={f.label} checked={Boolean(value)} onChange={(e) => onChange(e.currentTarget.checked)} mt="lg" />;
    case "tristate":
      return (
        <div>
          <Text size="sm" fw={500} mb={4}>{f.label}</Text>
          <SegmentedControl size="sm" data={f.choices} value={(value as string) || "unknown"} onChange={onChange} />
        </div>
      );
    case "choice":
      return (
        <Select {...common} clearable={!f.required} data={f.choices} value={(value as string) || null}
          onChange={(v) => onChange(v ?? "")} />
      );
    case "dropdown": {
      const list = f.choice_list ? schema?.choice_lists[f.choice_list] : undefined;
      const keep = new Set<number>(
        [original].flat().filter((x): x is number => typeof x === "number"),
      );
      const data = (list?.options ?? [])
        .filter((o) => o.is_active || keep.has(o.id))
        .map((o) => ({ value: String(o.id), label: o.is_active ? o.label : `${o.label} (removed)` }));
      if (f.multi) {
        return (
          <MultiSelect {...common} searchable clearable data={data}
            value={((value as number[]) ?? []).map(String)} onChange={(v) => onChange(v.map(Number))} />
        );
      }
      return (
        <Select {...common} searchable clearable data={data} value={value ? String(value) : null}
          onChange={(v) => onChange(v ? Number(v) : null)} />
      );
    }
    case "user": {
      const keep = new Set([original].flat());
      const data = (users ?? [])
        .filter((u) => u.is_active || keep.has(u.id))
        .map((u) => ({ value: String(u.id), label: u.is_active ? u.full_name : `${u.full_name} (deactivated)` }));
      if (f.multi) {
        return (
          <MultiSelect {...common} searchable clearable data={data}
            value={((value as number[]) ?? []).map(String)} onChange={(v) => onChange(v.map(Number))} />
        );
      }
      return (
        <Select {...common} searchable clearable data={data} value={value ? String(value) : null}
          onChange={(v) => onChange(v ? Number(v) : null)} />
      );
    }
    case "tags":
      return <TagsInput {...common} clearable value={(value as string[]) ?? []} onChange={onChange} splitChars={[","]} placeholder="Type and press Enter" />;
    case "money":
      return <MoneyInput {...common} value={(value as Money) ?? EMPTY_MONEY} onChange={onChange} grouping={grouping} schema={schema} />;
    case "link":
      if (f.multi) return <MultiLinkInput f={f} value={(value as LinkValue[]) ?? []} onChange={onChange} error={error} />;
      return <LinkInput f={f} value={(value as LinkValue) ?? null} onChange={onChange} error={error} schemaModules={schema?.modules ?? []} />;
    case "datetime":
      return <DateTimeInput {...common} value={(value as string) ?? null} onChange={onChange} dateFormat={me.date_format} />;
    case "birthday":
      return <BirthdayInput label={f.label} value={(value as string) ?? null} onChange={onChange} error={error} dayFirst={me.date_format !== "MM/DD/YYYY"} />;
  }
}

/** A date with an optional time of day: "2026-10-12" or "2026-10-12T14:30". */
function DateTimeInput({
  label, required, error, description, value, onChange, dateFormat,
}: {
  label: string; required: boolean; error?: string; description?: string; value: string | null;
  onChange: (v: string | null) => void; dateFormat: string;
}) {
  const [day, time = ""] = (value ?? "").split("T");
  const join = (d: string, t: string) => (d ? (t ? `${d}T${t}` : d) : null);
  return (
    <div>
      <Text size="sm" fw={500} mb={4}>{label}{required && <Text span c="red"> *</Text>}</Text>
      <Group gap={6} wrap="nowrap" align="flex-start">
        <DateInput aria-label={label} style={{ flex: 1 }} clearable valueFormat={dateFormat} placeholder={dateFormat.toLowerCase()}
          dateParser={dateParser(dateFormat)} error={!!error}
          value={day ? dayjs(day).toDate() : null}
          onChange={(d) => onChange(join(d ? dayjs(d).format("YYYY-MM-DD") : "", d ? time : ""))} />
        <TimeInput aria-label={`${label} time`} w={120} disabled={!day} value={time}
          onChange={(e) => onChange(join(day, e.currentTarget.value))} />
      </Group>
      {error ? <Text size="xs" c="red" mt={4}>{error}</Text>
        : <Text size="xs" c="dimmed" mt={4}>{description || "The time is optional."}</Text>}
    </div>
  );
}

function MoneyInput({
  label, required, error, description, value, onChange, grouping, schema,
}: {
  label: string; required: boolean; error?: string; description?: string; value: Money;
  onChange: (v: Money) => void; grouping: "lakh" | "thousand"; schema?: Schema;
}) {
  const currencies = (schema?.currencies ?? []).map((c) => ({ value: c.code, label: `${c.code} ${c.symbol !== c.code ? c.symbol : ""}`.trim() }));
  const units = (schema?.units ?? []).map((u) => ({ value: u.value || "none", label: u.value ? u.value : "—" }));
  return (
    <div>
      <Text size="sm" fw={500} mb={4}>{label}{required && <Text span c="red"> *</Text>}</Text>
      <Group gap={6} wrap="nowrap" align="flex-start">
        <Select aria-label={`${label} currency`} w={100} data={currencies} placeholder="Cur." searchable
          value={value.currency || null} onChange={(c) => onChange({ ...value, currency: c ?? "" })} error={!!error} />
        <NumberInput aria-label={`${label} amount`} style={{ flex: 1 }} hideControls thousandSeparator="," thousandsGroupStyle={grouping}
          value={value.amount ?? ""} error={!!error}
          onChange={(a) => onChange({ ...value, amount: a === "" ? null : String(a) })} />
        <Select aria-label={`${label} unit`} w={92} data={units}
          value={value.unit || "none"} onChange={(u) => onChange({ ...value, unit: !u || u === "none" ? "" : u })} />
      </Group>
      {error ? <Text size="xs" c="red" mt={4}>{error}</Text> : description ? <Text size="xs" c="dimmed" mt={4}>{description}</Text> : null}
    </div>
  );
}
