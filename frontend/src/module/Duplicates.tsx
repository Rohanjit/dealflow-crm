// "Find duplicates": records that look like the same thing (same rules as the import's warnings),
// grouped, each group one click from the merge screen.

import { Alert, Badge, Button, Drawer, Group, Paper, Stack, Text } from "@mantine/core";
import { IconArrowsJoin, IconCopy } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { errorMessage } from "../components/errors";
import { Skeleton } from "../components/ui";
import type { ModuleSchema } from "../types";
import MergeModal from "./MergeModal";

type Group_ = { reasons: string[]; records: { id: number; title: string }[] };

export function DuplicatesButton({ module }: { module: ModuleSchema }) {
  const [open, setOpen] = useState(false);
  if (!module.finds_duplicates || !module.perms.edit || !module.perms.delete) return null;
  return (
    <>
      <Button variant="default" leftSection={<IconCopy size={16} />} onClick={() => setOpen(true)}>Find duplicates</Button>
      {open && <DuplicatesDrawer module={module} onClose={() => setOpen(false)} />}
    </>
  );
}

function DuplicatesDrawer({ module, onClose }: { module: ModuleSchema; onClose: () => void }) {
  const qc = useQueryClient();
  const [merging, setMerging] = useState<number[] | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey: ["duplicates", module.key],
    queryFn: () => api<{ groups: Group_[] }>(`m/${module.key}/duplicates/`),
  });
  const groups = data?.groups ?? [];
  const noun = module.label.toLowerCase();

  return (
    <Drawer opened onClose={onClose} position="right" size="lg" title={<Text fw={600}>Possible duplicate {noun}</Text>}>
      <Stack>
        <Text size="sm" c="dimmed">
          Records that share a name, website, email or phone number. Check each group: if they're the same,
          merge them into one. If they're not, just leave them.
        </Text>
        {error && <Alert color="red">{errorMessage(error)}</Alert>}
        {isLoading && [0, 1, 2].map((i) => <Skeleton key={i} h={86} r={8} />)}
        {!isLoading && groups.length === 0 && (
          <Paper withBorder p="lg"><Text fw={600}>No duplicates found</Text><Text size="sm" c="dimmed">Nothing here looks like the same {module.singular.toLowerCase()} twice.</Text></Paper>
        )}
        <Stack gap="sm">
          {groups.map((g, i) => (
            <Paper key={i} withBorder p="md">
              <Group gap={6} mb={8}>
                {g.reasons.map((r) => <Badge key={r} color="orange" variant="light">{r}</Badge>)}
              </Group>
              <Stack gap={2} mb="sm">
                {g.records.map((r) => (
                  <Text key={r.id} size="sm">
                    <Link to={`/m/${module.key}/${r.id}`} target="_blank" className="mantine-Anchor-root" style={{ color: "inherit" }}>{r.title}</Link>
                  </Text>
                ))}
              </Stack>
              <Button size="xs" leftSection={<IconArrowsJoin size={14} />} onClick={() => setMerging(g.records.map((r) => r.id).slice(0, 10))}>
                Review and merge
              </Button>
            </Paper>
          ))}
        </Stack>
      </Stack>
      {merging && (
        <MergeModal module={module} ids={merging} onClose={() => setMerging(null)}
          onMerged={async () => {
            setMerging(null);
            await qc.invalidateQueries({ queryKey: ["duplicates", module.key] });
            await qc.invalidateQueries({ queryKey: ["records"] });
          }} />
      )}
    </Drawer>
  );
}
