import { Group, NavLink, Paper, Select, Stack, Title } from "@mantine/core";
import { IconBuilding, IconDatabase, IconList, IconShieldLock, IconToggleRight, IconUsers } from "@tabler/icons-react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { useMe } from "../context";
import BackupsSettings from "./BackupsSettings";
import DropdownSettings from "./DropdownSettings";
import FieldSettings from "./FieldSettings";
import FirmSettings from "./FirmSettings";
import RolesSettings from "./RolesSettings";
import UsersSettings from "./UsersSettings";

export default function SettingsPage() {
  const me = useMe();
  const loc = useLocation();
  const nav = useNavigate();
  const tabs = [
    { path: "firm", label: "Firm", icon: IconBuilding, show: me.flags.manage_settings, el: <FirmSettings /> },
    { path: "users", label: "Users", icon: IconUsers, show: me.flags.manage_users, el: <UsersSettings /> },
    { path: "roles", label: "Roles & permissions", icon: IconShieldLock, show: me.role.is_admin, el: <RolesSettings /> },
    { path: "fields", label: "Fields", icon: IconToggleRight, show: me.flags.manage_settings, el: <FieldSettings /> },
    { path: "dropdowns", label: "Dropdown lists", icon: IconList, show: me.flags.manage_settings, el: <DropdownSettings /> },
    { path: "backups", label: "Backups", icon: IconDatabase, show: me.role.is_admin, el: <BackupsSettings /> },
  ].filter((t) => t.show);
  if (!tabs.length) return <Navigate to="/" replace />;

  return (
    <Stack gap="lg" maw={1200}>
      <Title order={2}>Settings</Title>
      <Select hiddenFrom="sm" data={tabs.map((t) => ({ value: t.path, label: t.label }))}
        value={tabs.find((t) => loc.pathname.startsWith(`/settings/${t.path}`))?.path ?? null}
        onChange={(v) => v && nav(`/settings/${v}`)} />
      <Group align="flex-start" gap="lg" wrap="nowrap">
        <Paper withBorder p={6} w={220} style={{ flexShrink: 0 }} visibleFrom="sm">
          {tabs.map((t) => (
            <NavLink key={t.path} component={Link} to={`/settings/${t.path}`} label={t.label} leftSection={<t.icon size={18} />}
              active={loc.pathname.startsWith(`/settings/${t.path}`)} />
          ))}
        </Paper>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Routes>
            {tabs.map((t) => <Route key={t.path} path={t.path} element={t.el} />)}
            <Route path="*" element={<Navigate to={`/settings/${tabs[0].path}`} replace />} />
          </Routes>
        </div>
      </Group>
    </Stack>
  );
}
