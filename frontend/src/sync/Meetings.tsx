// Upcoming meetings on contact, outreach and deal pages, and "Schedule meeting" (decision 64).

import { Anchor, Button, Group, Modal, Select, Stack, TagsInput, Text, Textarea, TextInput, ThemeIcon } from "@mantine/core";
import { DateTimePicker } from "@mantine/dates";
import { IconCalendarEvent, IconCalendarPlus } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { errorMessage, fieldErrors, notifyOk } from "../components/errors";
import { useMe } from "../context";
import { formatDateTime } from "../format";
import type { ModuleSchema, RecordData } from "../types";
import { type Item, useMeetingsAbout, useSync, withLine } from "./api";
import { ItemModal } from "./LoggedEntry";

export function UpcomingMeetings({ module, record }: { module: ModuleSchema; record: RecordData }) {
  const me = useMe();
  const about = `${module.key}:${record.id}`;
  const { data } = useMeetingsAbout(about);
  const [open, setOpen] = useState<number | null>(null);
  if (!data?.length) return null;
  return (
    <div className="df-fade">
      <Text className="df-section-title" mb={6}>Upcoming meetings</Text>
      <Stack gap={8}>
        {data.map((m: Item) => (
          <Group key={m.id} gap="sm" wrap="nowrap" align="flex-start">
            <ThemeIcon variant="light" color="grape" radius="xl" size="md"><IconCalendarEvent size={15} /></ThemeIcon>
            <div style={{ minWidth: 0, flex: 1 }}>
              <Text size="sm" fw={500} truncate>{m.subject || "(no title)"}</Text>
              <Text size="xs" c="dimmed">
                {m.all_day ? `${formatDateTime(m.at, me).split(" ")[0]} · all day` : formatDateTime(m.at, me)} · with {withLine(m.participants)}
              </Text>
            </div>
            {m.can_open && <Button size="compact-xs" variant="subtle" onClick={() => setOpen(m.id)}>Details</Button>}
          </Group>
        ))}
      </Stack>
      {open && <ItemModal id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

type Person = { name: string; email: string };

export function ScheduleModal({ module, record, onClose }: { module: ModuleSchema; record: RecordData; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: sync } = useSync();
  const about = `${module.key}:${record.id}`;
  const { data: sugg } = useQuery({
    queryKey: ["mail-recipients", about],
    queryFn: () => api<{ people: Person[] }>(`mail/recipients/?about=${encodeURIComponent(about)}`),
  });
  const calendars = (sync?.connections ?? []).filter((c) => c.calendar && !c.needs_reconnect);
  const [title, setTitle] = useState(`Meeting: ${record._title}`);
  const [start, setStart] = useState<Date | null>(dayjs().add(1, "day").hour(11).minute(0).second(0).toDate());
  const [minutes, setMinutes] = useState("30");
  const [people, setPeople] = useState<string[] | null>(null);
  const [location, setLocation] = useState("");
  const [notes, setNotes] = useState("");
  const [calendar, setCalendar] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const suggested = (sugg?.people ?? []).map((p) => `${p.name} <${p.email}>`);
  const chosen = people ?? suggested.slice(0, 1);

  async function go() {
    setBusy(true);
    setErrors({});
    try {
      const attendees = chosen.map((s) => {
        const m = s.match(/^(.*)<([^>]+)>\s*$/);
        return m ? { name: m[1].trim(), email: m[2].trim() } : { name: "", email: s.trim() };
      });
      await api("sync/schedule/", {
        body: { about, title, start: start ? dayjs(start).format("YYYY-MM-DDTHH:mm:ss") : "", minutes: Number(minutes),
          attendees, location, notes, connection: calendar ?? calendars[0]?.id },
      });
      await qc.invalidateQueries({ queryKey: ["sync-meetings"] });
      await qc.invalidateQueries({ queryKey: ["sync-agenda"] });
      notifyOk("Meeting added to your calendar. Invitations are on their way.");
      onClose();
    } catch (e) {
      const f = fieldErrors(e);
      setErrors(Object.keys(f).length ? f : { detail: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal opened onClose={onClose} title="Schedule a meeting" size="lg">
      {sync && !calendars.length ? (
        <Stack>
          <Text size="sm">Connect your calendar first. Meetings you schedule here go straight into it, with invitations sent to the people you pick.</Text>
          <Group><Button component={Link} to="/settings/preferences" onClick={onClose}>Connect your calendar</Button></Group>
        </Stack>
      ) : (
        <Stack>
          <TextInput label="Title" value={title} onChange={(e) => setTitle(e.currentTarget.value)} error={errors.title} required />
          <Group grow align="flex-start">
            <DateTimePicker label="Starts" value={start} onChange={(v) => setStart(v ? new Date(v as unknown as string) : null)}
              minDate={new Date()} error={errors.start} valueFormat="DD MMM YYYY, HH:mm" required />
            <Select label="Length" data={[["15", "15 minutes"], ["30", "30 minutes"], ["45", "45 minutes"], ["60", "1 hour"],
              ["90", "1½ hours"], ["120", "2 hours"]].map(([value, label]) => ({ value, label }))} value={minutes}
              onChange={(v) => setMinutes(v ?? "30")} allowDeselect={false} error={errors.minutes} />
          </Group>
          <TagsInput label="Invite" description="Pick from the suggestions or type an email address and press Enter." data={suggested}
            value={chosen} onChange={setPeople} error={errors.attendees} clearable />
          <TextInput label="Where" placeholder="Their office, or a video link" value={location} onChange={(e) => setLocation(e.currentTarget.value)} />
          <Textarea label="Notes for the invitation" autosize minRows={2} value={notes} onChange={(e) => setNotes(e.currentTarget.value)} />
          {calendars.length > 1 && (
            <Select label="Add to" data={calendars.map((c) => ({ value: String(c.id), label: `${c.label}: ${c.email}` }))}
              value={calendar ?? String(calendars[0].id)} onChange={setCalendar} allowDeselect={false} />
          )}
          {errors.detail && <Text c="red" size="sm">{errors.detail}</Text>}
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Cancel</Button>
            <Button leftSection={<IconCalendarPlus size={16} />} onClick={go} loading={busy} disabled={!title.trim() || !start || !chosen.length}>
              Add to calendar and invite
            </Button>
          </Group>
          <Text size="xs" c="dimmed">
            It's added to your {calendars[0]?.label ?? ""} calendar and logged here once it's happened.{" "}
            <Anchor component={Link} to="/settings/preferences" size="xs" onClick={onClose}>Calendar settings</Anchor>
          </Text>
        </Stack>
      )}
    </Modal>
  );
}
