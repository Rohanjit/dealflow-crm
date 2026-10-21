// The app frame: a quiet sidebar (firm menu, search, home, modules, archive, settings) and a slim
// sticky top bar with the reminders bell. The sidebar folds away on desktop and slides in on phones.

import { ActionIcon, AppShell, Avatar, Button, Group, Menu, ScrollArea, Text, Tooltip, UnstyledButton } from "@mantine/core";
import { modals } from "@mantine/modals";
import { useDisclosure, useWindowScroll } from "@mantine/hooks";
import {
  IconChevronsLeft, IconCompass, IconLayoutSidebarLeftExpand, IconLogout, IconMenu2, IconPlus, IconSearch, IconSelector, IconSettings, IconUserCircle,
} from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";

import { api } from "../api";
import { notifyError } from "../components/errors";
import { leaveSample } from "../components/guest";
import Logo from "../components/Logo";
import { moduleIcon } from "../components/ui";
import { resetSession, useApp } from "../context";
import TaskBell from "../tasks/TaskBell";
import { useMailboxReturn } from "../mail/MailboxCard";
import SessionGuard from "./SessionGuard";
import SearchPalette, { useSearchPalette } from "./SearchPalette";

// Record types shown under another menu item (companies live under Outreach).
const NAV_CHILDREN: Record<string, string[]> = { outreach: ["companies"] };
const NEW_LABEL: Record<string, string> = { vault: "Upload" };

export default function Shell() {
  const { info, me } = useApp();
  const [mobileOpen, mobile] = useDisclosure();
  const [collapsed, setCollapsed] = useState(false);
  const search = useSearchPalette();
  const loc = useLocation();
  const qc = useQueryClient();
  const [scroll] = useWindowScroll();
  const canSettings = me.flags.manage_settings || me.flags.manage_users || me.role.is_admin;
  useMailboxReturn();

  async function signOut() {
    await api("auth/logout/", { method: "POST" });
    await resetSession(qc);
  }

  const active = (path: string) => loc.pathname === path || loc.pathname.startsWith(path + "/");
  // Scrolling between pages is handled with the page transition (navigation.ts).
  useEffect(() => {
    mobile.close();
  }, [loc.pathname]); // eslint-disable-line react-hooks/exhaustive-deps

  const modules = info.modules.filter((m) => me.perms[m.key]?.view && info.built_modules.includes(m.key));

  return (
    <AppShell navbar={{ width: 248, breakpoint: "sm", collapsed: { mobile: !mobileOpen, desktop: collapsed } }}
      transitionDuration={480} transitionTimingFunction="var(--df-spring-smooth)">
      <AppShell.Navbar className="df-sidebar">
        <AppShell.Section>
          <Group gap={2} wrap="nowrap">
            <Group gap={8} wrap="nowrap" px={8} style={{ flex: 1, minWidth: 0, minHeight: 40 }}>
              <Logo size={22} />
              <Text size="sm" fw={600} truncate style={{ flex: 1 }}>{info.firm_name || (info.workspace_kind === "individual" ? me.full_name : "Dealflow")}</Text>
            </Group>
            <Tooltip label="Hide the sidebar (bring it back with the button at the top left)">
              <ActionIcon variant="subtle" color="gray" visibleFrom="sm" onClick={() => setCollapsed(true)} aria-label="Hide sidebar">
                <IconChevronsLeft size={18} />
              </ActionIcon>
            </Tooltip>
          </Group>
          <UnstyledButton className="df-nav-search" onClick={search.open}>
            <IconSearch size={16} />
            <span style={{ flex: 1 }}>Search everything…</span>
            <kbd className="df-kbd">{isMac() ? "⌘K" : "Ctrl K"}</kbd>
          </UnstyledButton>
          <NavItem to="/" icon="home" label="Home" active={loc.pathname === "/"} />
          {(me.perms.outreach?.view || me.perms.deals?.view) && (
            <NavItem to="/insights" icon="insights" label="Insights" active={active("/insights")} />
          )}
          {me.flags.send_campaigns && <NavItem to="/campaigns" icon="campaigns" label="Campaigns" active={active("/campaigns")} />}
        </AppShell.Section>

        <AppShell.Section grow component={ScrollArea} type="scroll" mt={4}>
          <div className="df-nav-section">Your work</div>
          <div>
            <div>
              {modules.map((m) => (
                <NavItem key={m.key} to={`/m/${m.key}`} icon={m.key} label={m.label}
                  active={active(`/m/${m.key}`) || (NAV_CHILDREN[m.key] ?? []).some((c) => active(`/m/${c}`))}
                  extra={me.perms[m.key]?.create ? (
                    <Tooltip label={NEW_LABEL[m.key] ?? `Add new to ${m.label}`} position="right">
                      <Link to={`/m/${m.key}/new`} className="df-nav-mini" aria-label={`Add new to ${m.label}`}
                        onClick={(e) => e.stopPropagation()}>
                        <IconPlus size={14} />
                      </Link>
                    </Tooltip>
                  ) : null} />
              ))}
            </div>
          </div>
        </AppShell.Section>

        <AppShell.Section>
          {me.flags.restore_archive && <NavItem to="/archive" icon="archive" label="Deleted items" active={active("/archive")} />}
          <Menu position="top-start" width={248} offset={4}>
            <Menu.Target>
              <UnstyledButton className="df-nav-user" data-active={active("/settings") ? "" : undefined} aria-label="Your account, settings and sign out">
                <Avatar size={30} radius="xl" color="navy">{initials(me.full_name)}</Avatar>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <Text size="sm" fw={600} truncate>{me.full_name}</Text>
                  <Text size="xs" c="dimmed" truncate>Settings and sign out</Text>
                </div>
                <IconSelector size={16} className="df-nav-icon" />
              </UnstyledButton>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Label>{me.email} · {me.role.name}</Menu.Label>
              <Menu.Item component={Link} to="/settings/preferences" leftSection={<IconUserCircle size={16} />}>My preferences</Menu.Item>
              {canSettings && (
                <Menu.Item component={Link} to={`/settings/${me.flags.manage_settings ? "firm" : "users"}`} leftSection={<IconSettings size={16} />}>Firm settings</Menu.Item>
              )}
              {!info.is_guest && (
                <>
                  <Menu.Divider />
                  <Menu.Item onClick={signOut} leftSection={<IconLogout size={16} />}>Sign out</Menu.Item>
                </>
              )}
            </Menu.Dropdown>
          </Menu>
        </AppShell.Section>
      </AppShell.Navbar>

      <AppShell.Main>
        {info.is_guest && <GuestBanner demo={info.demo} />}
        <div className="df-topbar" data-scrolled={scroll.y > 4 ? "" : undefined}>
          <Button variant="default" size="compact-sm" hiddenFrom="sm" onClick={mobile.toggle} leftSection={<IconMenu2 size={16} />}>
            Menu
          </Button>
          {collapsed && (
            <Button variant="default" size="compact-sm" visibleFrom="sm" onClick={() => setCollapsed(false)}
              leftSection={<IconLayoutSidebarLeftExpand size={16} />}>
              Show menu
            </Button>
          )}
          <div style={{ flex: 1 }} />
          <Tooltip label="Search">
            <ActionIcon variant="subtle" color="gray" onClick={search.open} aria-label="Search" hiddenFrom="sm">
              <IconSearch size={18} />
            </ActionIcon>
          </Tooltip>
          <TaskBell />
        </div>
        <div className="df-main-inner">
          <div key={loc.pathname}>
            <Outlet />
          </div>
        </div>
      </AppShell.Main>
      <SearchPalette opened={search.opened} onClose={search.close} />
      <SessionGuard />
    </AppShell>
  );
}

function NavItem({ to, icon, label, active, extra }: { to: string; icon: string; label: string; active: boolean; extra?: ReactNode }) {
  const I = moduleIcon(icon);
  // The row is a box; the label is the link and stretches over the row (so the "+" beside it can be
  // a link of its own without nesting links).
  return (
    <div className="df-nav-item" data-active={active ? "" : undefined}>
      {/* The highlight behind the current page. It has a name of its own in page transitions, so it
          glides from the item you left to the one you picked. */}
      {active && <span className="df-nav-pill" aria-hidden />}
      <I size={18} stroke={1.7} className="df-nav-icon" />
      <Link to={to} className="df-nav-link" aria-current={active ? "page" : undefined}>{label}</Link>
      {extra && <span className="df-nav-extra">{extra}</span>}
    </div>
  );
}

/** Shown while someone looks around with the sample firm. */
function GuestBanner({ demo }: { demo: boolean }) {
  const qc = useQueryClient();
  function setUp() {
    modals.openConfirmModal({
      title: "Set up Dealflow for real?",
      children: (
        <Text size="sm">
          The sample firm and anything you changed in it are deleted, and you'll create your own admin account
          with an empty workspace. This can't be undone.
        </Text>
      ),
      labels: { confirm: "Delete sample and set up", cancel: "Keep looking around" },
      onConfirm: async () => {
        try {
          await leaveSample(qc);
        } catch (e) {
          notifyError(e, "Couldn't remove the sample");
        }
      },
    });
  }
  return (
    <div className="df-guest-banner" role="status">
      <IconCompass size={16} />
      <span style={{ flex: 1, minWidth: 200 }}>
        {demo
          ? "You're in the demo with a sample firm. Try anything: it all resets every day."
          : "You're looking around with a sample firm. Nothing here is real."}
      </span>
      {!demo && <Button size="compact-xs" color="dark" onClick={setUp}>Set up for real</Button>}
    </div>
  );
}

function isMac() {
  return typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
}

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join("");
}
