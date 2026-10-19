import { Alert, Anchor, Button, Group, Paper, SimpleGrid, Stack, Text, ThemeIcon, UnstyledButton } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import {
  IconArrowRight, IconBriefcase, IconBuildingBank, IconChecklist, IconFileImport, IconLock, IconPlus, IconTargetArrow, IconUsers,
  IconUsersPlus,
} from "@tabler/icons-react";
import { Link } from "react-router-dom";

import { useApp, useSchema } from "../context";
import DealsDashboard from "../deals/Dashboard";

const CARDS: Record<string, { icon: typeof IconBriefcase; color: string; blurb: string }> = {
  investors: { icon: IconBuildingBank, color: "teal", blurb: "Investor firms, what they invest in, and who owns each relationship." },
  contacts: { icon: IconUsers, color: "grape", blurb: "People at investor firms and client companies: how to reach them and who knows them best." },
  outreach: { icon: IconTargetArrow, color: "orange", blurb: "Companies you're approaching for mandates, from first contact to mandate won." },
  deals: { icon: IconBriefcase, color: "navy", blurb: "Mandates being executed: stages, deal team, fees, and each deal's investor tracker with suggested investors." },
  tasks: { icon: IconChecklist, color: "violet", blurb: "To-dos with due dates and reminders, tied to the deals, outreach, investors and contacts they're about." },
  vault: { icon: IconLock, color: "dark", blurb: "Deal documents kept on your own server behind a second PIN, tagged to the companies, deals and investors they concern." },
};
const ADD_LABEL: Record<string, string> = { vault: "Upload" };

function greeting(d = new Date()) {
  const h = d.getHours();
  return h < 5 ? "Working late" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

export default function Home() {
  const { info, me } = useApp();
  const { data: schema } = useSchema();
  const firstName = me.full_name.split(" ")[0];
  const modules = info.modules.filter((m) => info.built_modules.includes(m.key) && me.perms[m.key]?.view && CARDS[m.key]);
  const today = new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  return (
    <Stack gap={36} maw={1100}>
      <div>
        <Text c="dimmed" size="sm" mb={4}>{[today, info.firm_name].filter(Boolean).join(" · ")}</Text>
        <h1 className="df-page-title">{greeting()}, {firstName}</h1>
      </div>

      {me.role.is_admin && !info.sample && <AuditPassphraseNotice />}

      <div>
        <Text fw={600} size="sm" c="dimmed" mb="xs">Your workspace</Text>
        <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }} spacing="sm">
          {modules.map((m) => {
            const card = CARDS[m.key];
            const perms = me.perms[m.key];
            const importable = schema?.modules.find((s) => s.key === m.key)?.importable ?? false;
            return (
              <Paper key={m.key} withBorder p="md" className="df-card-link" style={{ display: "flex", flexDirection: "column" }}>
                <UnstyledButton component={Link} to={`/m/${m.key}`} style={{ flex: 1 }}>
                  <Group gap="sm" mb={6} wrap="nowrap">
                    <ThemeIcon variant="light" size={32} radius="md" color={card.color}><card.icon size={18} stroke={1.7} /></ThemeIcon>
                    <Text fw={600}>{m.label}</Text>
                    <IconArrowRight size={14} className="df-card-arrow" />
                  </Group>
                  <Text size="sm" c="dimmed" lineClamp={2}>{card.blurb}</Text>
                </UnstyledButton>
                {perms.create && (
                  <Group gap={4} mt="sm">
                    <Button size="compact-xs" variant="subtle" color="gray" leftSection={<IconPlus size={13} />} component={Link} to={`/m/${m.key}/new`}>
                      {ADD_LABEL[m.key] ?? "New"}
                    </Button>
                    {importable && (
                      <Button size="compact-xs" variant="subtle" color="gray" leftSection={<IconFileImport size={13} />} component={Link} to={`/m/${m.key}/import`}>
                        Import
                      </Button>
                    )}
                  </Group>
                )}
              </Paper>
            );
          })}
          {(me.flags.manage_users || me.role.is_admin) && info.workspace_kind !== "individual" && !info.is_guest && (
            <Paper withBorder p="md" className="df-card-link">
              <UnstyledButton component={Link} to="/settings/users" w="100%">
                <Group gap="sm" mb={6} wrap="nowrap">
                  <ThemeIcon variant="light" size={32} radius="md" color="gray"><IconUsersPlus size={18} stroke={1.7} /></ThemeIcon>
                  <Text fw={600}>Set up your team</Text>
                  <IconArrowRight size={14} className="df-card-arrow" />
                </Group>
                <Text size="sm" c="dimmed" lineClamp={2}>Decide who can see and change each module, then add people or send an invite link.</Text>
              </UnstyledButton>
            </Paper>
          )}
        </SimpleGrid>
      </div>

      {me.perms.deals?.view && info.built_modules.includes("deals") && <DealsDashboard />}
    </Stack>
  );
}

/** Admins: the audit log records from day one, but stays readable by the server until a passphrase locks it. */
function AuditPassphraseNotice() {
  const { data } = useQuery({ queryKey: ["audit"], queryFn: () => api<{ has_passphrase: boolean }>("audit/") });
  if (!data || data.has_passphrase) return null;
  return (
    <Alert color="orange" variant="light" title="Set the audit log passphrase">
      Dealflow records every action in an encrypted audit log. Set a passphrase so only people who know it can read the log.{" "}
      <Anchor component={Link} to="/settings/audit" size="sm">Set it in Settings</Anchor>
    </Alert>
  );
}
