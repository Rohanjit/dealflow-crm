// Small building blocks shared by every screen: icons per module and field type, page headers,
// loading placeholders and a smooth open/close wrapper.

import { Box, Group, Stack } from "@mantine/core";
import {
  IconAlignLeft, IconArchive, IconArrowUpRight, IconAt, IconBriefcase, IconBuildingBank, IconBuildingSkyscraper,
  IconCake, IconCalendar, IconCash, IconChecklist, IconCircleDot, IconClock, IconFile, IconHash, IconHome, IconLink,
  IconList, IconLock, IconPercentage, IconPhone, IconSelector, IconSettings, IconSquareCheck, IconTag, IconTargetArrow,
  IconUser, IconUserCircle, IconUsers, IconUsersGroup, type Icon,
} from "@tabler/icons-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import type { FieldSchema } from "../types";

export const MODULE_ICONS: Record<string, Icon> = {
  investors: IconBuildingBank,
  contacts: IconUsers,
  outreach: IconTargetArrow,
  companies: IconBuildingSkyscraper,
  deals: IconBriefcase,
  deal_investors: IconUsersGroup,
  tasks: IconChecklist,
  vault: IconLock,
  home: IconHome,
  archive: IconArchive,
  settings: IconSettings,
  preferences: IconUserCircle,
};

export function moduleIcon(key: string | undefined): Icon {
  return (key && MODULE_ICONS[key]) || IconFile;
}

/** The little icon in front of each property name, by field type. */
export function fieldIcon(f: FieldSchema): Icon {
  switch (f.type) {
    case "longtext": case "text": return IconAlignLeft;
    case "url": return IconLink;
    case "email": return IconAt;
    case "phone": return IconPhone;
    case "integer": case "decimal": return IconHash;
    case "percent": return IconPercentage;
    case "date": return IconCalendar;
    case "datetime": return IconClock;
    case "boolean": return IconSquareCheck;
    case "tristate": return IconCircleDot;
    case "choice": return IconSelector;
    case "dropdown": return f.multi ? IconList : IconSelector;
    case "user": return f.multi ? IconUsers : IconUser;
    case "money": return IconCash;
    case "tags": return IconTag;
    case "link": return IconArrowUpRight;
    case "birthday": return IconCake;
    default: return IconAlignLeft;
  }
}

/** Big page heading: icon tile, title, a grey line under it, and actions on the right. */
export function PageHeader({
  icon: I, title, sub, actions, children, hero,
}: { icon?: Icon; title: ReactNode; sub?: ReactNode; actions?: ReactNode; children?: ReactNode; hero?: string }) {
  return (
    <Stack gap={10}>
      {I && <div className="df-page-icon" aria-hidden><I size={28} stroke={1.6} /></div>}
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
        <Box style={{ minWidth: 0, flex: "1 1 320px" }}>
          <h1 className="df-page-title">
            {/* hero ("deals:5"): the title a list row or board card grows into (navigation.ts). */}
            {hero ? <span className="df-hero-title" data-hero={hero}>{title}</span> : title}
          </h1>
          {sub && <div className="df-page-sub" style={{ marginTop: 4 }}>{sub}</div>}
        </Box>
        {actions && <Group gap={6} wrap="wrap">{actions}</Group>}
      </Group>
      {children}
    </Stack>
  );
}

export function Skeleton({ w = "100%", h = 14, r }: { w?: number | string; h?: number; r?: number }) {
  return <div className="df-skeleton" style={{ width: w, height: h, borderRadius: r }} />;
}

/** Placeholder while a page loads: a header and a few lines, in the page's shape. */
export function PageSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <Stack gap="md" className="df-fade" aria-busy="true" aria-label="Loading">
      <Skeleton w={52} h={52} r={10} />
      <Skeleton w="38%" h={30} />
      <Skeleton w="22%" h={14} />
      <Stack gap={12} mt="lg">
        {Array.from({ length: rows }, (_, i) => (
          <Group key={i} gap="lg" wrap="nowrap">
            <Skeleton w={150} />
            <Skeleton w={`${40 + ((i * 37) % 45)}%`} />
          </Group>
        ))}
      </Stack>
    </Stack>
  );
}

export function TableSkeleton({ cols = 5, rows = 8 }: { cols?: number; rows?: number }) {
  return (
    <Stack gap={0} className="df-fade df-db" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, r) => (
        <Group key={r} gap="xl" wrap="nowrap" py={12} px="md" style={{ borderBottom: "1px solid var(--df-line)" }}>
          {Array.from({ length: cols }, (_, c) => (
            <Skeleton key={c} w={c === 0 ? 180 : `${8 + ((r + c * 3) % 5) * 3}%`} />
          ))}
        </Group>
      ))}
    </Stack>
  );
}

/** Opens and closes its children with a height animation (and keeps them mounted). */
export function Reveal({ open, children }: { open: boolean; children: ReactNode }) {
  return (
    <div className="df-collapse" data-closed={open ? undefined : ""} aria-hidden={!open}>
      <div>{children}</div>
    </div>
  );
}

/** A critically damped spring over 0..1 (same feel as --df-spring-smooth). */
function springOut(k: number) {
  const w = 9; // settles by k = 1
  return 1 - (1 + w * k) * Math.exp(-w * k);
}

/** A number that counts up to its value when it first appears (and to new values after). */
export function CountUp({ value, duration = 600 }: { value: number; duration?: number }) {
  const [shown, setShown] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce || value === from.current) { setShown(value); from.current = value; return; }
    const start = performance.now();
    const a = from.current;
    let raf = 0;
    const tick = (t: number) => {
      const k = Math.min(1, (t - start) / duration);
      const eased = k >= 1 ? 1 : springOut(k);
      setShown(Math.round(a + (value - a) * eased));
      if (k < 1) raf = requestAnimationFrame(tick);
      else from.current = value;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);
  return <span className="df-count">{shown.toLocaleString()}</span>;
}
