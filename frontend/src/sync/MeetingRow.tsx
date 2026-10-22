// A meeting from the person's calendar in My tasks: when, what, with whom and what it's about.
// Meetings aren't ticked off; they're logged on their records once they've happened.

import { Anchor, Badge, Button, Group, Text, ThemeIcon } from "@mantine/core";
import { IconCalendarEvent } from "@tabler/icons-react";
import dayjs from "dayjs";
import { useState } from "react";
import { Link } from "react-router-dom";

import { useMe } from "../context";
import { formatDate } from "../format";
import { type Item, withLine } from "./api";
import { ItemModal } from "./LoggedEntry";

export function meetingTime(m: Item, prefs: { date_format: string; number_grouping: string }) {
  const start = dayjs(m.at);
  const day = start.isSame(dayjs(), "day") ? "Today" : start.isSame(dayjs().add(1, "day"), "day") ? "Tomorrow"
    : formatDate(start.format("YYYY-MM-DD"), prefs);
  if (m.all_day) return `${day}, all day`;
  return `${day} ${start.format("HH:mm")}${m.end ? `–${dayjs(m.end).format("HH:mm")}` : ""}`;
}

export function MeetingRow({ meeting: m }: { meeting: Item }) {
  const me = useMe();
  const [open, setOpen] = useState(false);
  return (
    <Group justify="space-between" wrap="nowrap" gap="sm" py={8} px="md" className="df-task-row">
      <Group gap="sm" wrap="nowrap" align="flex-start" style={{ minWidth: 0 }}>
        <ThemeIcon variant="light" color="grape" radius="xl" size={20} mt={2}><IconCalendarEvent size={12} /></ThemeIcon>
        <div style={{ minWidth: 0 }}>
          <Text size="sm" fw={500} style={{ wordBreak: "break-word" }}>{m.subject || "(no title)"}</Text>
          <Text size="xs" c="dimmed">
            With {withLine(m.participants)}
            {m.links.length > 0 && <> · {m.links.map((l, i) => (
              <span key={`${l.module}${l.id}`}>{i ? ", " : ""}<Anchor component={Link} to={`/m/${l.module}/${l.id}`} size="xs">{l.title}</Anchor></span>
            ))}</>}
          </Text>
        </div>
      </Group>
      <Group gap={6} wrap="nowrap">
        {m.can_open && <Button size="compact-xs" variant="subtle" onClick={() => setOpen(true)}>Details</Button>}
        <Badge variant="light" color="grape" tt="none">{meetingTime(m, me)}</Badge>
      </Group>
      {open && <ItemModal id={m.id} onClose={() => setOpen(false)} />}
    </Group>
  );
}
