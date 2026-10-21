import { Group, Select, Stack, Text, UnstyledButton } from "@mantine/core";
import { IconBell, IconBuilding, IconFileExport, IconDatabase, IconShieldCheck, IconList, IconLockSquareRounded, IconMail, IconShieldLock, IconToggleRight, IconUserCircle, IconUsers } from "@tabler/icons-react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { useMe } from "../context";
import AlertSettings from "./AlertSettings";
import ExportSettings from "./ExportSettings";
import EmailSettings from "./EmailSettings";
import SecuritySettings from "./SecuritySettings";
import AuditSettings from "./AuditSettings";
import BackupsSettings from "./BackupsSettings";
import DropdownSettings from "./DropdownSettings";
import LayoutSettings from "./LayoutSettings";
import FirmSettings from "./FirmSettings";
import RolesSettings from "./RolesSettings";
import UsersSettings from "./UsersSettings";
import Preferences from "../pages/Preferences";
import { moduleIcon, PageHeader } from "../components/ui";

export default function SettingsPage() {
  const me = useMe();
  const loc = useLocation();
  const nav = useNavigate();
  // Everyone has their own preferences; the firm-wide sections depend on the role.
  const tabs = [
    { path: "preferences", label: "My preferences", icon: IconUserCircle, show: true, el: <Preferences />, group: "You" },
    { path: "export", label: "Export", icon: IconFileExport, show: true, el: <ExportSettings />, group: "You" },
    { path: "firm", label: "Firm", icon: IconBuilding, show: me.flags.manage_settings, el: <FirmSettings /> },
    { path: "users", label: "Users", icon: IconUsers, show: me.flags.manage_users, el: <UsersSettings /> },
    { path: "security", label: "Security", icon: IconLockSquareRounded, show: me.role.is_admin, el: <SecuritySettings /> },
    { path: "roles", label: "Roles & permissions", icon: IconShieldLock, show: me.role.is_admin, el: <RolesSettings /> },
    { path: "alerts", label: "Alerts", icon: IconBell, show: me.flags.manage_settings, el: <AlertSettings /> },
    { path: "email", label: "Email", icon: IconMail, show: me.role.is_admin, el: <EmailSettings /> },
    { path: "fields", label: "Fields & layout", icon: IconToggleRight, show: me.flags.manage_settings, el: <LayoutSettings /> },
    { path: "dropdowns", label: "Dropdown lists", icon: IconList, show: me.flags.manage_settings, el: <DropdownSettings /> },
    { path: "backups", label: "Backups", icon: IconDatabase, show: me.role.is_admin, el: <BackupsSettings /> },
    { path: "audit", label: "Audit log", icon: IconShieldCheck, show: me.role.is_admin, el: <AuditSettings /> },
  ].filter((t) => t.show);
  const firmTabs = tabs.filter((t) => t.group !== "You");
  const ownTabs = tabs.filter((t) => t.group === "You");

  return (
    <Stack gap="lg" maw={1200}>
      <PageHeader icon={moduleIcon("settings")} title="Settings" sub={firmTabs.length ? "Your own preferences, and the settings that apply to everyone at the firm." : "Your own preferences."} />
      <Select hiddenFrom="sm" label="Section" data={[
        { group: "Just for you", items: ownTabs.map((t) => ({ value: t.path, label: t.label })) },
        ...(firmTabs.length ? [{ group: "For everyone at the firm", items: firmTabs.map((t) => ({ value: t.path, label: t.label })) }] : []),
      ]}
        value={tabs.find((t) => loc.pathname.startsWith(`/settings/${t.path}`))?.path ?? null}
        onChange={(v) => v && nav(`/settings/${v}`)} />
      <Group align="flex-start" gap="lg" wrap="nowrap">
        <Stack gap={1} w={210} style={{ flexShrink: 0, position: "sticky", top: 56 }} visibleFrom="sm">
          {tabs.map((t, i) => {
            const on = loc.pathname.startsWith(`/settings/${t.path}`);
            return (
              <div key={t.path}>
                {i === 0 && <Text size="xs" fw={600} c="dimmed" px={8} pb={4}>Just for you</Text>}
                {i === ownTabs.length && <Text size="xs" fw={600} c="dimmed" px={8} pt="md" pb={4}>For everyone at the firm</Text>}
                <UnstyledButton component={Link} to={`/settings/${t.path}`} className="df-nav-item" data-active={on ? "" : undefined}>
                  {on && <span className="df-nav-pill" style={{ viewTransitionName: "df-settings-pill" }} aria-hidden />}
                  <t.icon size={18} stroke={1.7} className="df-nav-icon" />{t.label}
                </UnstyledButton>
              </div>
            );
          })}
        </Stack>
        <div style={{ flex: 1, minWidth: 0 }} key={loc.pathname} className="df-settings">
          <Routes>
            {tabs.map((t) => <Route key={t.path} path={t.path} element={t.el} />)}
            <Route path="*" element={<Navigate to={`/settings/${tabs[0].path}`} replace />} />
          </Routes>
        </div>
      </Group>
    </Stack>
  );
}
