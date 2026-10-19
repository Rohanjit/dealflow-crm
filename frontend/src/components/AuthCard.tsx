import { Group, Paper, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";

import Logo from "./Logo";

export default function AuthCard({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children: ReactNode }) {
  return (
    <div className="df-auth-bg">
      <Stack gap="lg" w="100%" maw={400} className="df-auth-card">
        <Group gap={10} justify="center">
          <Logo size={30} />
          <Text fw={700} size="lg">Dealflow</Text>
        </Group>
        <Paper shadow="md" p={28} radius="lg">
          <Stack gap="lg">
            <div>
              <h2 style={{ margin: 0, fontSize: 22, lineHeight: 1.3, fontWeight: 700 }}>{title}</h2>
              {subtitle && <Text c="dimmed" size="sm" mt={4}>{subtitle}</Text>}
            </div>
            {children}
          </Stack>
        </Paper>
      </Stack>
    </div>
  );
}
