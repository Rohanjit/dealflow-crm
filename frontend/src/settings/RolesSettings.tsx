import { Alert, Badge, Button, Checkbox, Flex, Group, NavLink, Paper, Stack, Switch, Table, Text, TextInput, Title } from "@mantine/core";
import { modals } from "@mantine/modals";
import { IconPlus } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api";
import { useApp } from "../context";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import type { Action, Flags, ModulePerms } from "../types";

type Role = { id: number; name: string; is_admin: boolean; perms: Record<string, ModulePerms>; flags: Flags; user_count: number };
const ACTIONS: { key: Action; label: string }[] = [
  { key: "view", label: "View" }, { key: "create", label: "Create" }, { key: "edit", label: "Edit" }, { key: "delete", label: "Delete" },
];
const FLAGS: { key: keyof Flags; label: string; help: string }[] = [
  { key: "manage_users", label: "Manage users", help: "Add people, send invite links, reset passwords. Can't touch Admin accounts." },
  { key: "manage_settings", label: "Manage settings", help: "Firm name, field switches and dropdown lists." },
  { key: "restore_archive", label: "Restore from archive", help: "See deleted records and restore them. Permanent deletion stays with Admins." },
];

export default function RolesSettings() {
  const { info, refresh } = useApp();
  const qc = useQueryClient();
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api<Role[]>("roles/") });
  const [selected, setSelected] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState<{ name: string; perms: Record<string, ModulePerms>; flags: Flags } | null>(null);
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
        flags: { manage_users: false, manage_settings: false, restore_archive: false },
      });
    } else if (current) {
      setDraft({ name: current.name, perms: structuredClone(current.perms), flags: { ...current.flags } });
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
        body: draft,
      });
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
    modals.openConfirmModal({
      title: `Delete the ${r.name} role?`,
      children: <Text size="sm">Only possible when nobody has this role.</Text>,
      labels: { confirm: "Delete role", cancel: "Cancel" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await api(`roles/${r.id}/`, { method: "DELETE" });
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
            {!locked && <Group><Button onClick={save} loading={busy}>{selected === "new" ? "Create role" : "Save role"}</Button></Group>}
          </Stack>
        </Paper>
      )}
    </Flex>
  );
}
