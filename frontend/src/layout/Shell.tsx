import { AppShell, Avatar, Badge, Burger, Group, Menu, NavLink, ScrollArea, Text, UnstyledButton } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconArchive, IconBriefcase, IconBuildingBank, IconChecklist, IconChevronDown, IconHome, IconLock, IconLogout,
  IconSettings, IconTargetArrow, IconUserCircle, IconUsers,
} from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";

import { api } from "../api";
import { useApp, resetSession } from "../context";
import Logo from "../components/Logo";
import TaskBell from "../tasks/TaskBell";
import GlobalSearch from "./GlobalSearch";

const ICONS: Record<string, typeof IconHome> = {
  investors: IconBuildingBank,
  contacts: IconUsers,
  outreach: IconTargetArrow,
  deals: IconBriefcase,
  tasks: IconChecklist,
  vault: IconLock,
};

// Record types shown under another menu item (companies live under Outreach).
const NAV_CHILDREN: Record<string, string[]> = { outreach: ["companies"] };

export default function Shell() {
  const { info, me } = useApp();
  const [opened, { toggle, close }] = useDisclosure();
  const loc = useLocation();
  const qc = useQueryClient();
  const canSettings = me.flags.manage_settings || me.flags.manage_users || me.role.is_admin;

  async function signOut() {
    await api("auth/logout/", { method: "POST" });
    await resetSession(qc);
  }

  const active = (path: string) => loc.pathname === path || loc.pathname.startsWith(path + "/");
  // Each new page starts at the top (React Router keeps the old scroll position otherwise).
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [loc.pathname]);

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 232, breakpoint: "sm", collapsed: { mobile: !opened } }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <UnstyledButton component={Link} to="/">
              <Group gap={8} wrap="nowrap">
                <Logo size={26} />
                <Text fw={700}>Dealflow</Text>
                <Text c="dimmed" size="sm" visibleFrom="md" truncate maw={220}>{info.firm_name}</Text>
              </Group>
            </UnstyledButton>
          </Group>
          <GlobalSearch />
          <Group gap="xs" wrap="nowrap">
          <TaskBell />
          <Menu position="bottom-end" width={220}>
            <Menu.Target>
              <UnstyledButton>
                <Group gap={6} wrap="nowrap">
                  <Avatar size={30} radius="xl" color="navy">{initials(me.full_name)}</Avatar>
                  <Text size="sm" visibleFrom="md">{me.full_name}</Text>
                  <IconChevronDown size={14} />
                </Group>
              </UnstyledButton>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Label>{me.email} · {me.role.name}</Menu.Label>
              <Menu.Item component={Link} to="/preferences" leftSection={<IconUserCircle size={16} />}>My preferences</Menu.Item>
              <Menu.Item onClick={signOut} leftSection={<IconLogout size={16} />}>Sign out</Menu.Item>
            </Menu.Dropdown>
          </Menu>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <AppShell.Section grow component={ScrollArea}>
          <NavLink component={Link} to="/" label="Home" leftSection={<IconHome size={18} />} active={loc.pathname === "/"} onClick={close} />
          <Text size="xs" c="dimmed" fw={600} tt="uppercase" mt="md" mb={4} px="sm">Modules</Text>
          {info.modules.map((m) => {
            const Icon = ICONS[m.key] ?? IconHome;
            const built = info.built_modules.includes(m.key);
            if (!me.perms[m.key]?.view) return null;
            if (!built) {
              return (
                <NavLink key={m.key} label={m.label} leftSection={<Icon size={18} />} disabled
                  rightSection={<Badge size="xs" variant="light" color="gray">Soon</Badge>} />
              );
            }
            return (
              <NavLink key={m.key} component={Link} to={`/m/${m.key}`} label={m.label} leftSection={<Icon size={18} />}
                active={active(`/m/${m.key}`) || (NAV_CHILDREN[m.key] ?? []).some((c) => active(`/m/${c}`))} onClick={close} />
            );
          })}
        </AppShell.Section>
        <AppShell.Section>
          {me.flags.restore_archive && (
            <NavLink component={Link} to="/archive" label="Archive" leftSection={<IconArchive size={18} />} active={active("/archive")} onClick={close} />
          )}
          {canSettings && (
            <NavLink component={Link} to="/settings" label="Settings" leftSection={<IconSettings size={18} />} active={active("/settings")} onClick={close} />
          )}
        </AppShell.Section>
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join("");
}
