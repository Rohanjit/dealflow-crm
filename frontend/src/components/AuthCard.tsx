import { Group, Paper, Stack, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";

import Logo from "./Logo";

export default function AuthCard({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children: ReactNode }) {
  return (
    <div className="df-auth-bg">
      <Paper shadow="xl" p="xl" radius="lg" w="100%" maw={440}>
        <Stack gap="lg">
          <Group gap="xs">
            <Logo size={28} />
            <Text fw={700} size="lg">Dealflow</Text>
          </Group>
          <div>
            <Title order={3}>{title}</Title>
            {subtitle && <Text c="dimmed" size="sm" mt={4}>{subtitle}</Text>}
          </div>
          {children}
        </Stack>
      </Paper>
    </div>
  );
}
