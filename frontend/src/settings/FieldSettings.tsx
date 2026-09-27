import { Badge, Group, Paper, SegmentedControl, SimpleGrid, Stack, Switch, Text, Title, Tooltip } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { notifyError } from "../components/errors";

type Field = { key: string; label: string; group: string; type: string; enabled: boolean; can_disable: boolean };
type Mod = { module: string; label: string; groups: string[]; fields: Field[] };

export default function FieldSettings() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["field-switches"], queryFn: () => api<{ modules: Mod[] }>("settings/fields/") });
  const [module, setModule] = useState<string | null>(null);
  const mods = data?.modules ?? [];
  const current = mods.find((m) => m.module === (module ?? mods[0]?.module));

  async function toggle(f: Field, enabled: boolean) {
    qc.setQueryData<{ modules: Mod[] }>(["field-switches"], (old) => old && {
      modules: old.modules.map((m) => m.module !== current!.module ? m : { ...m, fields: m.fields.map((x) => x.key === f.key ? { ...x, enabled } : x) }),
    });
    try {
      await api("settings/fields/", { method: "PATCH", body: { module: current!.module, field_key: f.key, enabled } });
      qc.invalidateQueries({ queryKey: ["schema"] });
    } catch (e) {
      notifyError(e);
      qc.invalidateQueries({ queryKey: ["field-switches"] });
    }
  }

  if (!current) return null;
  return (
    <Paper withBorder p="lg">
      <Stack>
        <div>
          <Title order={4}>Fields</Title>
          <Text size="sm" c="dimmed">Switch off fields your firm doesn't use. They disappear from forms, lists, filters, imports and exports for everyone. Data already saved in a switched-off field is kept and comes back if you switch it on again.</Text>
        </div>
        {mods.length > 1 && <SegmentedControl w="fit-content" data={mods.map((m) => ({ value: m.module, label: m.label }))} value={current.module} onChange={setModule} />}
        <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
          {current.groups.map((g) => (
            <div key={g}>
              <Text fw={600} size="sm" mb="xs">{g}</Text>
              <Stack gap={8}>
                {current.fields.filter((f) => f.group === g).map((f) => (
                  <Group key={f.key} justify="space-between" wrap="nowrap">
                    <Text size="sm" c={f.enabled ? undefined : "dimmed"}>{f.label}</Text>
                    {f.can_disable ? (
                      <Switch checked={f.enabled} onChange={(e) => toggle(f, e.currentTarget.checked)} aria-label={f.label} />
                    ) : (
                      <Tooltip label="Required; always on"><Badge variant="light" color="gray" size="sm">Required</Badge></Tooltip>
                    )}
                  </Group>
                ))}
              </Stack>
            </div>
          ))}
        </SimpleGrid>
      </Stack>
    </Paper>
  );
}
