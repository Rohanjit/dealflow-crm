import {
  ActionIcon, Alert, Badge, Button, Code, CopyButton, Group, Menu, Modal, Paper, PasswordInput, Select, Stack, Switch,
  Table, Text, TextInput, Title, Tooltip,
} from "@mantine/core";
import { modals } from "@mantine/modals";
import { IconCheck, IconCopy, IconDots, IconLink, IconPlus } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { useMe } from "../context";
import { errorMessage, fieldErrors, notifyError, notifyOk } from "../components/errors";
import { formatDate, formatDateTime } from "../format";

type Role = { id: number; name: string; is_admin: boolean };
type User = {
  id: number; email: string; full_name: string; role: Role; is_active: boolean; must_change_password: boolean;
  last_login: string | null; date_joined: string; has_vault_pin: boolean;
};
type Invite = {
  id: number; token: string; role: { id: number; name: string }; note: string; created_by: string | null;
  created_at: string; expires_at: string; used_at: string | null; used_by: string | null; revoked: boolean; usable: boolean;
};

export default function UsersSettings() {
  const me = useMe();
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("users/") });
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api<Role[]>("roles/") });
  const invites = useQuery({ queryKey: ["invites"], queryFn: () => api<Invite[]>("invites/") });
  const [editing, setEditing] = useState<User | "new" | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [secret, setSecret] = useState<{ title: string; email: string; password: string } | null>(null);

  const roleOptions = (roles.data ?? []).filter((r) => me.role.is_admin || !r.is_admin).map((r) => ({ value: String(r.id), label: r.name }));
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["users"] });
    qc.invalidateQueries({ queryKey: ["users-lookup"] });
  };

  function reset(u: User) {
    modals.openConfirmModal({
      title: `Reset ${u.full_name}'s password?`,
      children: <Text size="sm">They'll get a temporary password to share with them directly. They must choose a new one when they next sign in, and are signed out everywhere now.</Text>,
      labels: { confirm: "Reset password", cancel: "Cancel" },
      onConfirm: async () => {
        try {
          const r = await api<{ temp_password: string }>(`users/${u.id}/reset-password/`, { body: {} });
          setSecret({ title: "Temporary password", email: u.email, password: r.temp_password });
          refresh();
        } catch (e) {
          notifyError(e, "Couldn't reset");
        }
      },
    });
  }

  function resetPin(u: User) {
    modals.openConfirmModal({
      title: `Reset ${u.full_name}'s Vault PIN?`,
      children: <Text size="sm">Their PIN is cleared. Next time they open the Vault they set a new one, confirming it with their account password.</Text>,
      labels: { confirm: "Reset Vault PIN", cancel: "Cancel" },
      onConfirm: async () => {
        try {
          await api(`users/${u.id}/reset-vault-pin/`, { body: {} });
          notifyOk(`${u.full_name}'s Vault PIN was reset.`);
          refresh();
        } catch (e) {
          notifyError(e, "Couldn't reset the PIN");
        }
      },
    });
  }

  async function setActive(u: User, active: boolean) {
    try {
      await api(`users/${u.id}/`, { method: "PATCH", body: { is_active: active } });
      notifyOk(active ? `${u.full_name} can sign in again.` : `${u.full_name} is deactivated and signed out.`);
      refresh();
    } catch (e) {
      notifyError(e, "Couldn't change");
    }
  }

  async function revoke(i: Invite) {
    try {
      await api(`invites/${i.id}/`, { method: "DELETE" });
      qc.invalidateQueries({ queryKey: ["invites"] });
    } catch (e) {
      notifyError(e);
    }
  }

  const openInvites = (invites.data ?? []).filter((i) => i.usable);

  return (
    <Stack gap="lg">
      <Paper withBorder p="lg">
        <Group justify="space-between" mb="md">
          <Title order={4}>Users</Title>
          <Group gap="xs">
            <Button variant="default" leftSection={<IconLink size={16} />} onClick={() => setInviteOpen(true)}>Invite link</Button>
            <Button leftSection={<IconPlus size={16} />} onClick={() => setEditing("new")}>Add user</Button>
          </Group>
        </Group>
        <Table className="df-table" verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr><Table.Th>Name</Table.Th><Table.Th>Role</Table.Th><Table.Th>Status</Table.Th><Table.Th>Last sign-in</Table.Th><Table.Th w={40} /></Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {users.data?.map((u) => {
              const locked = u.role.is_admin && !me.role.is_admin;
              return (
                <Table.Tr key={u.id} opacity={u.is_active ? 1 : 0.55}>
                  <Table.Td><Text size="sm" fw={500}>{u.full_name}{u.id === me.id && <Text span c="dimmed" size="xs"> (you)</Text>}</Text><Text size="xs" c="dimmed">{u.email}</Text></Table.Td>
                  <Table.Td><Badge variant="light" color={u.role.is_admin ? "navy" : "gray"} tt="none">{u.role.name}</Badge></Table.Td>
                  <Table.Td>
                    {!u.is_active ? <Badge color="gray" variant="outline">Deactivated</Badge>
                      : u.must_change_password ? <Badge color="orange" variant="light">Temporary password</Badge>
                      : <Badge color="teal" variant="light">Active</Badge>}
                  </Table.Td>
                  <Table.Td><Text size="sm">{u.last_login ? formatDateTime(u.last_login, me) : "Never"}</Text></Table.Td>
                  <Table.Td>
                    {!locked && (
                      <Menu position="bottom-end">
                        <Menu.Target><ActionIcon variant="subtle" aria-label="Actions"><IconDots size={16} /></ActionIcon></Menu.Target>
                        <Menu.Dropdown>
                          <Menu.Item onClick={() => setEditing(u)}>Edit</Menu.Item>
                          <Menu.Item onClick={() => reset(u)}>Reset password</Menu.Item>
                          {u.has_vault_pin && <Menu.Item onClick={() => resetPin(u)}>Reset Vault PIN</Menu.Item>}
                          {u.id !== me.id && (u.is_active
                            ? <Menu.Item color="red" onClick={() => setActive(u, false)}>Deactivate</Menu.Item>
                            : <Menu.Item onClick={() => setActive(u, true)}>Reactivate</Menu.Item>)}
                        </Menu.Dropdown>
                      </Menu>
                    )}
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Paper>

      {openInvites.length > 0 && (
        <Paper withBorder p="lg">
          <Title order={5} mb="sm">Open invite links</Title>
          <Table className="df-table" verticalSpacing="xs">
            <Table.Thead><Table.Tr><Table.Th>For</Table.Th><Table.Th>Role</Table.Th><Table.Th>Expires</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>
              {openInvites.map((i) => (
                <Table.Tr key={i.id}>
                  <Table.Td><Text size="sm">{i.note || "—"}</Text></Table.Td>
                  <Table.Td><Text size="sm">{i.role.name}</Text></Table.Td>
                  <Table.Td><Text size="sm">{formatDate(i.expires_at.slice(0, 10), me)}</Text></Table.Td>
                  <Table.Td>
                    <Group gap="xs" justify="flex-end">
                      <CopyLink token={i.token} />
                      <Button size="xs" variant="subtle" color="red" onClick={() => revoke(i)}>Revoke</Button>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      )}

      {editing && (
        <UserModal user={editing === "new" ? null : editing} roleOptions={roleOptions} onClose={() => setEditing(null)}
          onSaved={(temp, email) => { setEditing(null); refresh(); if (temp) setSecret({ title: "Account created", email, password: temp }); }} />
      )}
      {inviteOpen && <InviteModal roleOptions={roleOptions} onClose={() => { setInviteOpen(false); qc.invalidateQueries({ queryKey: ["invites"] }); }} />}
      <Modal opened={!!secret} onClose={() => setSecret(null)} title={secret?.title}>
        {secret && (
          <Stack>
            <Text size="sm">Share these with the person directly (in person, by phone or chat). They'll be asked to choose their own password when they sign in.</Text>
            <Text size="sm">Email: <b>{secret.email}</b></Text>
            <Group gap="xs"><Text size="sm">Temporary password:</Text><Code fz="md">{secret.password}</Code><CopyIcon value={secret.password} /></Group>
            <Alert color="orange" variant="light">This password won't be shown again.</Alert>
            <Group justify="flex-end"><Button onClick={() => setSecret(null)}>Done</Button></Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}

function CopyIcon({ value }: { value: string }) {
  return (
    <CopyButton value={value}>
      {({ copied, copy }) => (
        <Tooltip label={copied ? "Copied" : "Copy"}>
          <ActionIcon variant="subtle" onClick={copy} color={copied ? "teal" : "gray"}>{copied ? <IconCheck size={16} /> : <IconCopy size={16} />}</ActionIcon>
        </Tooltip>
      )}
    </CopyButton>
  );
}

function inviteUrl(token: string) {
  return `${window.location.origin}/invite/${token}`;
}

function CopyLink({ token }: { token: string }) {
  return (
    <CopyButton value={inviteUrl(token)}>
      {({ copied, copy }) => <Button size="xs" variant="light" onClick={copy} color={copied ? "teal" : undefined}>{copied ? "Copied" : "Copy link"}</Button>}
    </CopyButton>
  );
}

function UserModal({
  user, roleOptions, onClose, onSaved,
}: {
  user: User | null; roleOptions: { value: string; label: string }[]; onClose: () => void;
  onSaved: (tempPassword: string | null, email: string) => void;
}) {
  const me = useMe();
  const [v, setV] = useState({
    full_name: user?.full_name ?? "", email: user?.email ?? "", role: user ? String(user.role.id) : "", temp_password: "",
    is_active: user?.is_active ?? true,
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setErrors({});
    setError("");
    try {
      if (user) {
        await api(`users/${user.id}/`, { method: "PATCH", body: { full_name: v.full_name, email: v.email, role: Number(v.role), is_active: v.is_active } });
        notifyOk("Saved.");
        onSaved(null, v.email);
      } else {
        const r = await api<{ temp_password: string }>("users/", { body: { full_name: v.full_name, email: v.email, role: Number(v.role), temp_password: v.temp_password } });
        onSaved(r.temp_password, v.email);
      }
    } catch (e) {
      setErrors(fieldErrors(e));
      if (!Object.keys(fieldErrors(e)).length) setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal opened onClose={onClose} title={user ? `Edit ${user.full_name}` : "Add user"}>
      <Stack>
        {error && <Alert color="red">{error}</Alert>}
        <TextInput label="Name" required value={v.full_name} onChange={(e) => setV({ ...v, full_name: e.currentTarget.value })} error={errors.full_name} />
        <TextInput label="Email" required type="email" value={v.email} onChange={(e) => setV({ ...v, email: e.currentTarget.value })} error={errors.email} />
        <Select label="Role" required data={roleOptions} value={v.role || null} onChange={(r) => setV({ ...v, role: r ?? "" })} error={errors.role} />
        {!user && (
          <PasswordInput label="Temporary password" description="Leave blank and Dealflow will make one up. They must change it at first sign-in."
            value={v.temp_password} onChange={(e) => setV({ ...v, temp_password: e.currentTarget.value })} error={errors.temp_password} />
        )}
        {user && user.id !== me.id && (
          <Switch label="Can sign in" checked={v.is_active} onChange={(e) => setV({ ...v, is_active: e.currentTarget.checked })} />
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button onClick={save} loading={busy}>{user ? "Save" : "Create account"}</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function InviteModal({ roleOptions, onClose }: { roleOptions: { value: string; label: string }[]; onClose: () => void }) {
  const [role, setRole] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [invite, setInvite] = useState<Invite | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function create() {
    setBusy(true);
    setError("");
    try {
      setInvite(await api<Invite>("invites/", { body: { role: Number(role), note } }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal opened onClose={onClose} title="Invite link" size="lg">
      {invite ? (
        <Stack>
          <Text size="sm">Send this link by email, WhatsApp or any channel. It works once and expires in 7 days. The person sets their own password and joins as <b>{invite.role.name}</b>.</Text>
          <Group gap="xs" wrap="nowrap">
            <TextInput readOnly value={inviteUrl(invite.token)} style={{ flex: 1 }} onFocus={(e) => e.currentTarget.select()} />
            <CopyLink token={invite.token} />
          </Group>
          <Group justify="flex-end"><Button onClick={onClose}>Done</Button></Group>
        </Stack>
      ) : (
        <Stack>
          {error && <Alert color="red">{error}</Alert>}
          <Select label="Role they'll get" required data={roleOptions} value={role} onChange={setRole} />
          <TextInput label="Who it's for (optional)" placeholder="e.g. Priya, new analyst" value={note} onChange={(e) => setNote(e.currentTarget.value)} />
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Cancel</Button>
            <Button onClick={create} loading={busy} disabled={!role}>Create link</Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
