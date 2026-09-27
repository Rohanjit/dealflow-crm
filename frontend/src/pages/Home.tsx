import { Button, Group, Paper, SimpleGrid, Stack, Text, ThemeIcon, Title } from "@mantine/core";
import { IconBriefcase, IconBuildingBank, IconFileImport, IconPlus } from "@tabler/icons-react";
import { Link } from "react-router-dom";

import { useApp } from "../context";

export default function Home() {
  const { info, me } = useApp();
  const inv = me.perms.investors;
  const firstName = me.full_name.split(" ")[0];
  return (
    <Stack gap="lg" maw={1100}>
      <div>
        <Title order={2}>Welcome, {firstName}</Title>
        <Text c="dimmed">{info.firm_name}</Text>
      </div>
      {me.perms.deals?.view && (
        <Paper withBorder p="lg" radius="md">
          <Group gap="sm" mb="xs">
            <ThemeIcon variant="light" size="lg"><IconBriefcase size={20} /></ThemeIcon>
            <Text fw={600}>Active deals</Text>
          </Group>
          <Text size="sm" c="dimmed">
            Deals by stage, deals near their target close date and mandates nearing expiry will show here once the
            Active Deals module is added.
          </Text>
        </Paper>
      )}
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        {inv?.view && (
          <Paper withBorder p="lg" radius="md">
            <Group gap="sm" mb="xs">
              <ThemeIcon variant="light" size="lg" color="teal"><IconBuildingBank size={20} /></ThemeIcon>
              <Text fw={600}>Investors</Text>
            </Group>
            <Text size="sm" c="dimmed" mb="md">Investor firms, what they invest in, and who owns each relationship.</Text>
            <Group gap="xs">
              <Button size="xs" component={Link} to="/m/investors">Open investors</Button>
              {inv.create && <Button size="xs" variant="default" leftSection={<IconPlus size={14} />} component={Link} to="/m/investors/new">Add one</Button>}
              {inv.create && <Button size="xs" variant="default" leftSection={<IconFileImport size={14} />} component={Link} to="/m/investors/import">Import from Excel</Button>}
            </Group>
          </Paper>
        )}
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
