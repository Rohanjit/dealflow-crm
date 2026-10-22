// My preferences > Email and calendar logging: connect Google, Microsoft 365, iCloud or another
// provider; see when each was last checked; meeting reminders.

import {
  Alert, Anchor, Badge, Button, Checkbox, Group, Modal, NumberInput, Paper, PasswordInput, Select, Stack, Text,
  TextInput, Title,
} from "@mantine/core";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import {
  IconBrandApple, IconBrandGoogle, IconBrandWindows, IconCalendar, IconMail, IconRefresh, IconServer,
} from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { api } from "../api";
import { useMe } from "../context";
import { errorMessage, fieldErrors, notifyError, notifyOk } from "../components/errors";
import { formatDateTime } from "../format";
import { KIND_LABEL, type Connection, type SyncKind, useSync } from "./api";

const ICON: Record<SyncKind, typeof IconMail> = {
  google: IconBrandGoogle, microsoft: IconBrandWindows, icloud: IconBrandApple, other: IconServer,
};

const REMINDERS = [
  { value: "off", label: "No reminder" }, { value: "5", label: "5 minutes before" },
  { value: "10", label: "10 minutes before" }, { value: "15", label: "15 minutes before" },
  { value: "30", label: "30 minutes before" }, { value: "60", label: "1 hour before" },
];

export default function LoggingCard() {
  const me = useMe();
  const qc = useQueryClient();
  const { data } = useSync();
  const [picking, setPicking] = useState<SyncKind | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  if (!data) return null;
  const refresh = () => qc.invalidateQueries({ queryKey: ["sync"] });
  const have = new Set(data.connections.map((c) => c.kind));

  async function checkNow(c: Connection) {
    setBusy(`now${c.id}`);
    try {
      const r = await api<{ emails: number; meetings: number; error?: string }>(`sync/connections/${c.id}/now/`, { body: {} });
      await refresh();
      await qc.invalidateQueries({ queryKey: ["sync-agenda"] });
      if (r.error) notifications.show({ color: "orange", message: r.error });
      else notifyOk(`Checked. ${r.emails} new ${r.emails === 1 ? "email" : "emails"} and ${r.meetings} ${r.meetings === 1 ? "meeting" : "meetings"} with people in Dealflow.`);
    } catch (e) {
      notifyError(e, "Couldn't check");
    } finally {
      setBusy(null);
    }
  }

  async function turnOff(c: Connection, what: "mail" | "calendar") {
    try {
      await api(`sync/connections/${c.id}/`, { method: "PATCH", body: { [what]: false } });
      await refresh();
    } catch (e) {
      notifyError(e, "Couldn't change it");
    }
  }

  function disconnect(c: Connection) {
    modals.openConfirmModal({
      title: `Disconnect ${c.email}?`,
      children: <Text size="sm">Nothing new is logged from it. Emails and meetings already logged stay on their records, but you can no longer open their full text from Dealflow.</Text>,
      labels: { confirm: "Disconnect", cancel: "Cancel" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await api(`sync/connections/${c.id}/`, { method: "DELETE" });
          await refresh();
          notifyOk("Disconnected.");
        } catch (e) {
          notifyError(e, "Couldn't disconnect");
        }
      },
    });
  }

  async function reminder(v: string | null) {
    try {
      await api("sync/", { method: "PUT", body: { reminder_minutes: v === "off" || !v ? null : Number(v) } });
      await refresh();
      await qc.invalidateQueries({ queryKey: ["sync-agenda"] });
    } catch (e) {
      notifyError(e);
    }
  }

  return (
    <Paper withBorder p="lg">
      <Stack>
        <div>
          <Title order={4}>Email and calendar logging</Title>
          <Text size="sm" c="dimmed">
            Emails and meetings with people already in Dealflow are added to their records automatically, from the day you connect.
            Colleagues see the subject, date and who it was with; only you can open the email itself or a meeting's notes.
            Emails among colleagues are never logged.
          </Text>
        </div>
        {data.in_sample ? (
          <Text size="sm" c="dimmed">Mailboxes can't be connected while the sample firm is loaded. Set up Dealflow for real first.</Text>
        ) : (
          <>
            {data.connections.map((c) => {
              const Icon = ICON[c.kind];
              return (
                <Paper key={c.id} withBorder p="sm" radius="md">
                  <Stack gap={6}>
                    <Group gap="sm" wrap="wrap">
                      <Icon size={18} />
                      <Text fw={600}>{c.email}</Text>
                      <Text size="sm" c="dimmed">{c.label}</Text>
                      {c.mail && <Badge variant="light" leftSection={<IconMail size={11} />}>Email</Badge>}
                      {c.calendar && <Badge variant="light" color="grape" leftSection={<IconCalendar size={11} />}>Calendar</Badge>}
                      <Badge variant="light" color={c.needs_reconnect ? "orange" : c.problem ? "yellow" : "teal"}>
                        {c.needs_reconnect ? "Reconnect needed" : c.problem ? "Having trouble" : "Connected"}
                      </Badge>
                    </Group>
                    {(c.needs_reconnect || c.problem) && <Text size="sm" c="orange.8">{c.problem}</Text>}
                    <Text size="xs" c="dimmed">
                      Logging since {formatDateTime(c.connected_at, me)}
                      {c.synced_at ? ` · last checked ${formatDateTime(c.synced_at, me)}` : " · first check within 5 minutes"}
                    </Text>
                    <Group gap="xs">
                      <Button size="xs" variant="default" leftSection={<IconRefresh size={14} />} loading={busy === `now${c.id}`}
                        disabled={c.needs_reconnect} onClick={() => checkNow(c)}>Check now</Button>
                      {(c.needs_reconnect || c.kind === "google" || c.kind === "microsoft") && (
                        <Button size="xs" variant={c.needs_reconnect ? "filled" : "default"} onClick={() => setPicking(c.kind)}>Reconnect</Button>
                      )}
                      {c.mail && c.calendar && <Button size="xs" variant="subtle" color="gray" onClick={() => turnOff(c, "mail")}>Stop logging email</Button>}
                      {c.mail && c.calendar && <Button size="xs" variant="subtle" color="gray" onClick={() => turnOff(c, "calendar")}>Stop logging calendar</Button>}
                      <Button size="xs" variant="subtle" color="red" onClick={() => disconnect(c)}>Disconnect</Button>
                    </Group>
                  </Stack>
                </Paper>
              );
            })}
            <div>
              <Text size="sm" fw={500} mb={6}>{data.connections.length ? "Connect another account" : "Connect your email and calendar"}</Text>
              <Group gap="xs">
                {(Object.keys(KIND_LABEL) as SyncKind[]).filter((k) => !have.has(k)).map((k) => {
                  const Icon = ICON[k];
                  return (
                    <Button key={k} variant="default" leftSection={<Icon size={16} />} disabled={!data.ready[k]} onClick={() => setPicking(k)}>
                      {k === "other" ? "Another provider" : KIND_LABEL[k]}
                    </Button>
                  );
                })}
              </Group>
              {(!data.ready.google || !data.ready.microsoft) && (
                <Text size="xs" c="dimmed" mt={6}>
                  {[!data.ready.google && "Google", !data.ready.microsoft && "Microsoft 365"].filter(Boolean).join(" and ")} isn't set up for this install yet.{" "}
                  {me.role.is_admin
                    ? <Anchor component={Link} to="/settings/email" size="xs">Set it up in Settings &gt; Email</Anchor>
                    : "Ask an admin to set it up in Settings > Email."}
                </Text>
              )}
            </div>
            {data.connections.some((c) => c.mail) && (
              <Text size="sm">
                People you email who aren't in Dealflow yet are listed, just for you, in{" "}
                <Anchor component={Link} to="/capture" size="sm">Add people</Anchor>.
              </Text>
            )}
            <Select label="Remind me before meetings" description="The bell reminds you before each meeting with someone in Dealflow."
              data={REMINDERS} value={data.reminder_minutes ? String(data.reminder_minutes) : "off"} onChange={reminder}
              allowDeselect={false} w={280} />
          </>
        )}
      </Stack>
      {picking && <ConnectModal kind={picking} existing={data.connections.find((c) => c.kind === picking)} onClose={() => setPicking(null)} />}
    </Paper>
  );
}

function ConnectModal({ kind, existing, onClose }: { kind: SyncKind; existing?: Connection; onClose: () => void }) {
  const qc = useQueryClient();
  const [mail, setMail] = useState(existing ? existing.mail : true);
  const [calendar, setCalendar] = useState(existing ? existing.calendar : true);
  const [email, setEmail] = useState(existing?.email ?? "");
  const [password, setPassword] = useState("");
  const [imapHost, setImapHost] = useState(existing?.imap_host ?? "");
  const [imapPort, setImapPort] = useState<number | string>(existing?.imap_port ?? 993);
  const [caldav, setCaldav] = useState(existing?.caldav_url ?? "");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const oauth = kind === "google" || kind === "microsoft";

  async function go() {
    setBusy(true);
    setErrors({});
    try {
      if (oauth) {
        const r = await api<{ url: string }>(`sync/connect/${kind}/`, { body: { mail, calendar, next: "/settings/preferences" } });
        window.location.assign(r.url);
        return;
      }
      await api(`sync/connect/${kind}/`, {
        body: { mail, calendar, email, password, imap_host: imapHost, imap_port: Number(imapPort) || 993, caldav_url: caldav },
      });
      await qc.invalidateQueries({ queryKey: ["sync"] });
      notifyOk("Connected. New emails and meetings with people in Dealflow will appear on their records.");
      onClose();
    } catch (e) {
      const f = fieldErrors(e);
      setErrors(Object.keys(f).length ? f : { detail: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal opened onClose={onClose} title={`Connect ${KIND_LABEL[kind]}`} size="lg">
      <Stack>
        <Group gap="lg">
          <Checkbox label="Email" checked={mail} onChange={(e) => setMail(e.currentTarget.checked)} />
          <Checkbox label="Calendar" checked={calendar} onChange={(e) => setCalendar(e.currentTarget.checked)} />
        </Group>
        {oauth ? (
          <Text size="sm" c="dimmed">
            You'll sign in with {kind === "google" ? "Google" : "Microsoft"} and come back here.{" "}
            {kind === "google" ? "Google" : "Microsoft"} will ask Dealflow for permission to
            {mail && " read your email"}{mail && calendar && " and"}{calendar && " see and add calendar events"}.
            Only emails and meetings with people already in Dealflow are logged, and only from today.
          </Text>
        ) : (
          <>
            <TextInput label="Email address" value={email} onChange={(e) => setEmail(e.currentTarget.value)} error={errors.email} required />
            <PasswordInput label="App password" value={password} onChange={(e) => setPassword(e.currentTarget.value)} error={errors.password} required
              description={kind === "icloud"
                ? "Not your Apple Account password: make an app-specific password at account.apple.com, under Sign-In and Security."
                : "Most providers want an app password made for Dealflow, not your usual password."} />
            {kind === "other" && (
              <>
                {mail && (
                  <Group grow align="flex-start">
                    <TextInput label="Mail (IMAP) server" placeholder="imap.yourprovider.com" value={imapHost}
                      onChange={(e) => setImapHost(e.currentTarget.value)} error={errors.imap_host} />
                    <NumberInput label="Port" value={imapPort} onChange={setImapPort} min={1} max={65535} maw={110} error={errors.imap_port} />
                  </Group>
                )}
                {calendar && (
                  <TextInput label="Calendar (CalDAV) address" placeholder="https://calendar.yourprovider.com/" value={caldav}
                    onChange={(e) => setCaldav(e.currentTarget.value)} error={errors.caldav_url} />
                )}
              </>
            )}
            <Text size="xs" c="dimmed">The password is stored encrypted and only used to read your mailbox and calendar.</Text>
          </>
        )}
        {errors.detail && <Alert color="red">{errors.detail}</Alert>}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button onClick={go} loading={busy} disabled={!(mail || calendar) || (!oauth && (!email || !password))}>
            {oauth ? `Continue to ${kind === "google" ? "Google" : "Microsoft"}` : "Connect"}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

/** Coming back from Google or Microsoft with ?logging=connected (or error). */
export function useLoggingReturn() {
  const loc = useLocation();
  const nav = useNavigate();
  const qc = useQueryClient();
  useEffect(() => {
    const p = new URLSearchParams(loc.search);
    const result = p.get("logging");
    if (!result) return;
    if (result === "connected") {
      notifications.show({ color: "teal", icon: <IconCalendar size={16} />,
        message: "Connected. New emails and meetings with people in Dealflow will appear on their records." });
    } else {
      notifications.show({ color: "red", title: "Not connected", message: p.get("reason") || "Try connecting again.", autoClose: 10_000 });
    }
    qc.invalidateQueries({ queryKey: ["sync"] });
    p.delete("logging");
    p.delete("reason");
    nav({ pathname: loc.pathname, search: p.toString() }, { replace: true });
  }, [loc.search, loc.pathname, nav, qc]);
}
