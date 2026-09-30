import { Button, Group, Paper, SimpleGrid, Stack, Text, ThemeIcon, Title } from "@mantine/core";
import {
  IconBriefcase, IconBuildingBank, IconChecklist, IconFileImport, IconLock, IconPlus, IconTargetArrow, IconUsers,
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
const OPEN_LABEL: Record<string, string> = { outreach: "Open pipeline", deals: "Open active deals", tasks: "Open my tasks", vault: "Open the Vault" };
const ADD_LABEL: Record<string, string> = { vault: "Upload" };

export default function Home() {
  const { info, me } = useApp();
  const { data: schema } = useSchema();
  const firstName = me.full_name.split(" ")[0];
  const modules = info.modules.filter((m) => info.built_modules.includes(m.key) && me.perms[m.key]?.view && CARDS[m.key]);
  return (
    <Stack gap="lg" maw={1100}>
      <div>
        <Title order={2}>Welcome, {firstName}</Title>
        <Text c="dimmed">{info.firm_name}</Text>
      </div>
      {me.perms.deals?.view && info.built_modules.includes("deals") && <DealsDashboard />}
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        {modules.map((m) => {
          const card = CARDS[m.key];
          const perms = me.perms[m.key];
          const importable = schema?.modules.find((s) => s.key === m.key)?.importable ?? false;
          return (
            <Paper key={m.key} withBorder p="lg" radius="md">
              <Group gap="sm" mb="xs">
                <ThemeIcon variant="light" size="lg" color={card.color}><card.icon size={20} /></ThemeIcon>
                <Text fw={600}>{m.label}</Text>
              </Group>
              <Text size="sm" c="dimmed" mb="md">{card.blurb}</Text>
              <Group gap="xs">
                <Button size="xs" component={Link} to={`/m/${m.key}`}>{OPEN_LABEL[m.key] ?? `Open ${m.label.toLowerCase()}`}</Button>
                {perms.create && <Button size="xs" variant="default" leftSection={<IconPlus size={14} />} component={Link} to={`/m/${m.key}/new`}>{ADD_LABEL[m.key] ?? "Add one"}</Button>}
                {perms.create && importable && <Button size="xs" variant="default" leftSection={<IconFileImport size={14} />} component={Link} to={`/m/${m.key}/import`}>Import from Excel</Button>}
              </Group>
            </Paper>
          );
        })}
        {(me.flags.manage_users || me.role.is_admin) && (
          <Paper withBorder p="lg" radius="md">
            <Text fw={600} mb="xs">Set up your team</Text>
            <Text size="sm" c="dimmed" mb="md">Create roles to decide who can see and change each module, then add people or send them an invite link.</Text>
            <Group gap="xs">
              <Button size="xs" variant="default" component={Link} to="/settings/users">Users</Button>
              {me.role.is_admin && <Button size="xs" variant="default" component={Link} to="/settings/roles">Roles</Button>}
            </Group>
          </Paper>
        )}
      </SimpleGrid>
    </Stack>
  );
}
