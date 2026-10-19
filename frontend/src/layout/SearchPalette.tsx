// Search everything, from anywhere: ⌘K / Ctrl+K or "/". With nothing typed it offers quick jumps
// and "new record" actions; typing searches every module. Arrow keys move, Enter opens.

import { Group, Loader, Modal, ScrollArea, Stack, Text, TextInput, UnstyledButton } from "@mantine/core";
import { useDebouncedValue, useHotkeys } from "@mantine/hooks";
import { IconCornerDownLeft, IconPlus, IconSearch } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, qs } from "../api";
import { moduleIcon } from "../components/ui";
import { useApp } from "../context";

type Hit = { module: string; module_label: string; id?: number; title?: string; subtitle?: string; more?: number };
type Item = { key: string; group: string; label: string; sub?: string; icon: ReturnType<typeof moduleIcon>; plus?: boolean; to: string };

const NEW_LABEL: Record<string, string> = { vault: "Upload documents" };

export function useSearchPalette() {
  const [opened, setOpened] = useState(false);
  useHotkeys([["mod+K", () => setOpened((o) => !o)], ["/", () => setOpened(true)]]);
  return { opened, open: () => setOpened(true), close: () => setOpened(false) };
}

export default function SearchPalette({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const { info, me } = useApp();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [debounced] = useDebouncedValue(q.trim(), 160);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (opened) { setQ(""); setSel(0); } }, [opened]);

  const { data, isFetching } = useQuery({
    queryKey: ["search", debounced],
    queryFn: () => api<{ results: Hit[] }>(`search/${qs({ q: debounced })}`),
    enabled: opened && debounced.length >= 2,
  });

  const modules = info.modules.filter((m) => info.built_modules.includes(m.key) && me.perms[m.key]?.view);
  const items: Item[] = useMemo(() => {
    if (debounced.length >= 2) {
      return (data?.results ?? []).map((h, i) => ({
        key: `${h.module}:${h.id ?? "more"}:${i}`,
        group: h.module_label,
        label: h.more ? `See all ${h.more + 6} in ${h.module_label}` : h.title ?? "",
        sub: h.more ? undefined : h.subtitle,
        icon: moduleIcon(h.module),
        to: h.more ? `/m/${h.module}?q=${encodeURIComponent(debounced)}` : `/m/${h.module}/${h.id}`,
      }));
    }
    const lower = q.trim().toLowerCase();
    const jump = [{ key: "home", label: "Home" }, ...modules].filter((m) => m.label.toLowerCase().includes(lower));
    return [
      ...jump.map((m) => ({ key: `go:${m.key}`, group: "Go to", label: m.label, icon: moduleIcon(m.key), to: m.key === "home" ? "/" : `/m/${m.key}` })),
      ...modules.filter((m) => me.perms[m.key]?.create && m.label.toLowerCase().includes(lower)).map((m) => ({
        key: `new:${m.key}`, group: "Create", label: NEW_LABEL[m.key] ?? `New in ${m.label}`, icon: moduleIcon(m.key), plus: true,
        to: `/m/${m.key}/new`,
      })),
    ];
  }, [debounced, data, q, modules, me.perms]);

  useEffect(() => { setSel(0); }, [debounced, items.length]);
  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${sel}"]`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  function go(it: Item | undefined) {
    if (!it) return;
    onClose();
    nav(it.to);
  }

  let last = "";
  return (
    <Modal opened={opened} onClose={onClose} withCloseButton={false} size={620} padding={0} yOffset="12vh"
      radius="lg" aria-label="Search" returnFocus>
      <div style={{ position: "relative", borderBottom: "1px solid var(--df-line)" }}>
        <IconSearch size={20} style={{ position: "absolute", left: 16, top: 16, color: "var(--mantine-color-gray-6)", zIndex: 1 }} />
        <TextInput data-autofocus value={q} placeholder="Search or jump to…" classNames={{ input: "df-palette-input" }}
          rightSection={isFetching ? <Loader size={16} /> : null} rightSectionWidth={44}
          onChange={(e) => setQ(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, items.length - 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
            if (e.key === "Enter") { e.preventDefault(); go(items[sel]); }
          }} />
      </div>
      <ScrollArea.Autosize mah="52vh" type="scroll">
        <Stack gap={0} p={6} ref={listRef}>
          {debounced.length >= 2 && !isFetching && items.length === 0 && (
            <Text size="sm" c="dimmed" p="md" ta="center">No matches for “{debounced}”.</Text>
          )}
          {items.map((it, i) => {
            const header = it.group !== last ? it.group : null;
            last = it.group;
            const I = it.icon;
            return (
              <div key={it.key}>
                {header && <Text size="xs" c="dimmed" fw={600} px={10} pt={i ? 10 : 4} pb={4}>{header}</Text>}
                <UnstyledButton className="df-palette-item" data-index={i} data-selected={i === sel ? "" : undefined}
                  onMouseMove={() => setSel(i)} onClick={() => go(it)}>
                  <Group gap={6} wrap="nowrap" c="gray.7">
                    {it.plus && <IconPlus size={14} />}
                    <I size={18} stroke={1.6} />
                  </Group>
                  <div style={{ minWidth: 0 }}>
                    <Text size="sm" fw={500} truncate>{it.label}</Text>
                    {it.sub && <Text size="xs" c="dimmed" truncate>{it.sub}</Text>}
                  </div>
                  <IconCornerDownLeft size={14} className="df-palette-enter" />
                </UnstyledButton>
              </div>
            );
          })}
        </Stack>
      </ScrollArea.Autosize>
      <Group gap="md" px="md" py={8} style={{ borderTop: "1px solid var(--df-line)" }}>
        <Text size="xs" c="dimmed"><kbd className="df-kbd">↑</kbd> <kbd className="df-kbd">↓</kbd> to move</Text>
        <Text size="xs" c="dimmed"><kbd className="df-kbd">Enter</kbd> to open</Text>
        <Text size="xs" c="dimmed"><kbd className="df-kbd">Esc</kbd> to close</Text>
      </Group>
    </Modal>
  );
}
