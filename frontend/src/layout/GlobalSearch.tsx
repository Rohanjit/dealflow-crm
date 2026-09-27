import { Box, Kbd, Loader, Paper, Popover, Stack, Text, TextInput, UnstyledButton } from "@mantine/core";
import { useDebouncedValue, useHotkeys } from "@mantine/hooks";
import { IconSearch } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, qs } from "../api";

type Hit = { module: string; module_label: string; id?: number; title?: string; subtitle?: string; more?: number };

export default function GlobalSearch() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [debounced] = useDebouncedValue(q.trim(), 200);
  const nav = useNavigate();
  const ref = useRef<HTMLInputElement>(null);
  useHotkeys([["/", () => ref.current?.focus()], ["mod+K", () => ref.current?.focus()]]);

  const { data, isFetching } = useQuery({
    queryKey: ["search", debounced],
    queryFn: () => api<{ results: Hit[] }>(`search/${qs({ q: debounced })}`),
    enabled: debounced.length >= 2,
  });
  const hits = data?.results ?? [];
  const first = hits.find((h) => h.id);

  function go(h: Hit) {
    setOpen(false);
    setQ("");
    ref.current?.blur();
    if (h.more) nav(`/m/${h.module}?q=${encodeURIComponent(debounced)}`);
    else nav(`/m/${h.module}/${h.id}`);
  }

  let lastModule = "";
  return (
    <Popover opened={open && debounced.length >= 2} position="bottom" width="target" shadow="md" trapFocus={false} returnFocus={false}>
      <Popover.Target>
        <TextInput
          ref={ref}
          w={{ base: 180, sm: 360, md: 460 }}
          placeholder="Search everything"
          leftSection={<IconSearch size={16} />}
          rightSection={isFetching ? <Loader size={14} /> : <Kbd size="xs" visibleFrom="sm">/</Kbd>}
          value={q}
          onChange={(e) => { setQ(e.currentTarget.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && first) go(first);
            if (e.key === "Escape") { setOpen(false); ref.current?.blur(); }
          }}
        />
      </Popover.Target>
      <Popover.Dropdown p={4}>
        {hits.length === 0 && !isFetching && <Text size="sm" c="dimmed" p="sm">No matches for “{debounced}”.</Text>}
        <Stack gap={0}>
          {hits.map((h, i) => {
            const header = h.module !== lastModule ? h.module_label : null;
            lastModule = h.module;
            return (
              <Box key={i}>
                {header && <Text size="xs" c="dimmed" fw={600} tt="uppercase" px="sm" pt="xs" pb={2}>{header}</Text>}
                <UnstyledButton w="100%" onMouseDown={(e) => e.preventDefault()} onClick={() => go(h)}>
                  <Paper px="sm" py={6} radius="sm" className="df-row-link" bg="transparent"
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--mantine-color-gray-1)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
                    {h.more ? (
                      <Text size="sm" c="navy">See all {h.more + 6} in {h.module_label} →</Text>
                    ) : (
                      <>
                        <Text size="sm" fw={500}>{h.title}</Text>
                        {h.subtitle && <Text size="xs" c="dimmed">{h.subtitle}</Text>}
                      </>
                    )}
                  </Paper>
                </UnstyledButton>
              </Box>
            );
          })}
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
