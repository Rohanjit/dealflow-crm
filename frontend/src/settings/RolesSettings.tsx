import {
  Alert, Badge, Button, Checkbox, Flex, Group, NavLink, Paper, PasswordInput, SegmentedControl, Select, Stack, Switch, Table, Text,
  TextInput, Title,
} from "@mantine/core";
import { modals } from "@mantine/modals";
import { IconPlus } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { useApp, useSchema } from "../context";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import type { Action, Flags, ModulePerms } from "../types";

type Access = Record<string, Record<string, "read" | "hidden">>;
type Role = { id: number; name: string; is_admin: boolean; perms: Record<string, ModulePerms>; flags: Flags; user_count: number; field_access: Access };
const ACTIONS: { key: Action; label: string }[] = [
  { key: "view", label: "View" }, { key: "create", label: "Create" }, { key: "edit", label: "Edit" }, { key: "delete", label: "Delete" },
];
const FLAGS: { key: keyof Flags; label: string; help: string }[] = [
  { key: "manage_users", label: "Manage users", help: "Add people, send invite links, reset passwords. Can't touch Admin accounts." },
  { key: "manage_settings", label: "Manage settings", help: "Firm name, field switches and dropdown lists." },
  { key: "restore_archive", label: "Restore from archive", help: "See deleted records and restore them. Permanent deletion stays with Admins." },
  { key: "see_team_performance", label: "See team performance", help: "On the Insights page, see win rates broken down by banker. Firm-wide numbers are shown to everyone who can view Outreach or Active Deals." },
  { key: "send_campaigns", label: "Send campaigns", help: "Send one email to many people at once (a mail merge) from their own mailbox. Single emails from a record page don't need this." },
];

export default function RolesSettings() {
  const { info, refresh } = useApp();
  const qc = useQueryClient();
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api<Role[]>("roles/") });
  const [selected, setSelected] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState<{ name: string; perms: Record<string, ModulePerms>; flags: Flags; field_access: Access } | null>(null);
  const [password, setPassword] = useState("");
  const delPwRef = useRef("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const current = typeof selected === "number" ? roles.data?.find((r) => r.id === selected) : undefined;
  useEffect(() => {
    if (selected === null && roles.data?.length) setSelected(roles.data[0].id);
  }, [roles.data, selected]);
  useEffect(() => {
    setError("");
    if (selected === "new") {
      setDraft({
        name: "",
        perms: Object.fromEntries(info.modules.map((m) => [m.key, { view: false, create: false, edit: false, delete: false }])),
        flags: { manage_users: false, manage_settings: false, restore_archive: false, see_team_performance: false, send_campaigns: false },
        field_access: {},
      });
    } else if (current) {
      setDraft({ name: current.name, perms: structuredClone(current.perms), flags: { ...current.flags }, field_access: structuredClone(current.field_access ?? {}) });
    }
  }, [selected, current, info.modules]);

  const locked = current?.is_admin ?? false;

  function toggle(module: string, action: Action, on: boolean) {
    if (!draft) return;
    const p = { ...draft.perms[module], [action]: on };
    if (on && action !== "view") p.view = true; // anything beyond viewing needs viewing
    if (!on && action === "view") { p.create = false; p.edit = false; p.delete = false; }
    setDraft({ ...draft, perms: { ...draft.perms, [module]: p } });
  }

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      const saved = await api<Role>(selected === "new" ? "roles/" : `roles/${selected}/`, {
        method: selected === "new" ? "POST" : "PATCH",
        body: { ...draft, password },
      });
      setPassword("");
      await qc.invalidateQueries({ queryKey: ["roles"] });
      await refresh();
      setSelected(saved.id);
      notifyOk("Role saved.");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function remove(r: Role) {
    delPwRef.current = "";
    modals.openConfirmModal({
      title: `Delete the ${r.name} role?`,
      children: (
        <Stack gap="xs">
          <Text size="sm">Only possible when nobody has this role.</Text>
          <PasswordInput label="Your password" onChange={(e) => { delPwRef.current = e.currentTarget.value; }} />
        </Stack>
      ),
      labels: { confirm: "Delete role", cancel: "Cancel" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await api(`roles/${r.id}/`, { method: "DELETE", body: { password: delPwRef.current } });
          setSelected(null);
          qc.invalidateQueries({ queryKey: ["roles"] });
        } catch (e) {
          notifyError(e, "Couldn't delete");
        }
      },
    });
  }

  return (
    <Flex align={{ base: "stretch", sm: "flex-start" }} gap="lg" direction={{ base: "column", sm: "row" }}>
      <Paper withBorder p={6} w={{ base: "100%", sm: 220 }} style={{ flexShrink: 0 }}>
        {roles.data?.map((r) => (
          <NavLink key={r.id} label={r.name} description={`${r.user_count} ${r.user_count === 1 ? "person" : "people"}`}
            active={selected === r.id} onClick={() => setSelected(r.id)}
            rightSection={r.is_admin ? <Badge size="xs" variant="light">Built-in</Badge> : null} />
        ))}
        <Button variant="subtle" fullWidth mt={4} leftSection={<IconPlus size={16} />} onClick={() => setSelected("new")}>New role</Button>
      </Paper>

      {draft && (
        <Paper withBorder p="lg" style={{ flex: 1, minWidth: 0 }}>
          <Stack>
            <Group justify="space-between">
              <Title order={4}>{selected === "new" ? "New role" : draft.name}</Title>
              {current && !current.is_admin && <Button variant="subtle" color="red" size="xs" onClick={() => remove(current)}>Delete role</Button>}
            </Group>
            {locked && <Alert variant="light">Admins can do everything, including managing roles and backups. This role can't be changed.</Alert>}
            {error && <Alert color="red">{error}</Alert>}
            {!locked && <TextInput label="Role name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.currentTarget.value })} maw={320} />}

            <div>
              <Text fw={600} size="sm" mb={4}>What this role can do in each module</Text>
              <Table.ScrollContainer minWidth={340} type="native">
              <Table className="df-table" verticalSpacing={6} maw={620}>
                <Table.Thead>
                  <Table.Tr><Table.Th>Module</Table.Th>{ACTIONS.map((a) => <Table.Th key={a.key} ta="center">{a.label}</Table.Th>)}</Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {info.modules.map((m) => (
                    <Table.Tr key={m.key}>
                      <Table.Td>
                        <Text size="sm">{m.label}</Text>
                        {!info.built_modules.includes(m.key) && <Text size="xs" c="dimmed">Coming soon; set it up now</Text>}
                        {m.key === "outreach" && <Text size="xs" c="dimmed">Also covers companies</Text>}
                        {m.key === "deals" && <Text size="xs" c="dimmed">Also covers companies and each deal's investor tracker</Text>}
                      </Table.Td>
                      {ACTIONS.map((a) => (
                        <Table.Td key={a.key} ta="center">
                          <Checkbox style={{ display: "inline-block" }} disabled={locked} aria-label={`${m.label} ${a.label}`}
                            checked={draft.perms[m.key]?.[a.key] ?? false} onChange={(e) => toggle(m.key, a.key, e.currentTarget.checked)} />
                        </Table.Td>
                      ))}
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
              </Table.ScrollContainer>
              <Text size="xs" c="dimmed" mt={4}>Delete moves records to Deleted items, where they can be brought back. Create, Edit and Delete include View.</Text>
            </div>

            <div>
              <Text fw={600} size="sm" mb="xs">Firm-wide permissions</Text>
              <Stack gap="sm">
                {FLAGS.map((f) => (
                  <Switch key={f.key} disabled={locked} label={f.label} description={f.help}
                    checked={draft.flags[f.key]} onChange={(e) => setDraft({ ...draft, flags: { ...draft.flags, [f.key]: e.currentTarget.checked } })} />
                ))}
              </Stack>
            </div>
            {!locked && <FieldAccessEditor value={draft.field_access} onChange={(field_access) => setDraft({ ...draft, field_access })} />}
            {!locked && (
              <Group align="flex-end" gap="sm">
                <PasswordInput label="Your password" description="Needed to change roles." value={password}
                  onChange={(e) => setPassword(e.currentTarget.value)} w={260} />
                <Button onClick={save} loading={busy} disabled={!password}>{selected === "new" ? "Create role" : "Save role"}</Button>
              </Group>
            )}
          </Stack>
        </Paper>
      )}
    </Flex>
  );
}


const LEVEL_OPTIONS = [
  { value: "full", label: "Can change" }, { value: "read", label: "Read only" }, { value: "hidden", label: "Hidden" },
];

/** Per module, the fields this role can only read or can't see at all (decision 63, S3). */
function FieldAccessEditor({ value, onChange }: { value: Access; onChange: (v: Access) => void }) {
  const { data: schema } = useSchema();
  const [moduleKey, setModuleKey] = useState<string | null>(null);
  const modules = schema?.modules ?? [];
  const mod = modules.find((m) => m.key === (moduleKey ?? modules[0]?.key));
  if (!mod) return null;
  // The primary field, the pipeline's own fields and company details shown here (ruled by Companies) can't be restricted.
  const fields = mod.fields.filter((f) => f.key !== mod.title_field && !f.pipeline_role && !f.via);
  const rules = value[mod.key] ?? {};
  const count = (k: string) => Object.keys(value[k] ?? {}).length;
  const set = (key: string, lvl: string) => {
    const next = { ...rules };
    if (lvl === "full") delete next[key]; else next[key] = lvl as "read" | "hidden";
    const all = { ...value, [mod.key]: next };
    if (!Object.keys(next).length) delete all[mod.key];
    onChange(all);
  };
  return (
    <div>
      <Text fw={600} size="sm" mb={2}>Fields this role can't change or can't see</Text>
      <Text size="xs" c="dimmed" mb="xs">
        Hidden fields never reach this role: not on pages, lists, search, filters, exports, reports, imports or emails.
        Company details on outreach and deals follow the Companies settings.
      </Text>
      <Select label="Module" w={260} allowDeselect={false} value={mod.key} onChange={setModuleKey}
        data={modules.map((m) => ({ value: m.key, label: count(m.key) ? `${m.label} (${count(m.key)} restricted)` : m.label }))} />
      <Table.ScrollContainer minWidth={420} type="native" mt="xs">
        <Table className="df-table" verticalSpacing={4} maw={620}>
          <Table.Tbody>
            {mod.groups.map((g) => {
              const inGroup = fields.filter((f) => f.group === g);
              if (!inGroup.length) return null;
              return [
                <Table.Tr key={`g-${g}`}><Table.Td colSpan={2}><Text size="xs" fw={600} c="dimmed">{g}</Text></Table.Td></Table.Tr>,
                ...inGroup.map((f) => (
                  <Table.Tr key={f.key}>
                    <Table.Td><Text size="sm">{f.label}</Text></Table.Td>
                    <Table.Td w={290}>
                      <SegmentedControl size="xs" fullWidth data={LEVEL_OPTIONS} value={rules[f.key] ?? "full"}
                        onChange={(v) => set(f.key, v)} aria-label={`${f.label} access`}
                        color={rules[f.key] === "hidden" ? "red" : rules[f.key] === "read" ? "orange" : undefined} />
                    </Table.Td>
                  </Table.Tr>
                )),
              ];
            })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </div>
  );
}
