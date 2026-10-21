// My preferences > Email: connect your own Gmail or Outlook, see today's count, disconnect.

import { Alert, Anchor, Badge, Button, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconBrandGmail, IconBrandWindows, IconMail, IconPlugConnected } from "@tabler/icons-react";
import { modals } from "@mantine/modals";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { api } from "../api";
import { useMe } from "../context";
import { notifyError, notifyOk } from "../components/errors";
import { formatDate } from "../format";
import { useMail } from "./api";

const HELP: Record<string, string> = {
  gmail: "Google asks only for permission to send email. Gmail then confirms each one is in your Sent folder.",
  outlook: "Microsoft also asks to read your mail: Dealflow only uses that to confirm each email reached your Sent Items.",
};

export async function connectMailbox(provider: string, next?: string) {
  const r = await api<{ url: string }>(`mail/connect/${provider}/`, { body: { next: next ?? window.location.pathname } });
  window.location.assign(r.url);
}

export default function MailboxCard() {
  const me = useMe();
  const qc = useQueryClient();
  const { data } = useMail();
  const [busy, setBusy] = useState<string | null>(null);
  if (!data) return null;
  const a = data.account;

  async function connect(p: string) {
    setBusy(p);
    try {
      await connectMailbox(p, "/settings/preferences");
    } catch (e) {
      notifyError(e, "Couldn't connect");
      setBusy(null);
    }
  }

  function disconnect() {
    modals.openConfirmModal({
      title: `Disconnect ${a!.email}?`,
      children: <Text size="sm">Dealflow stops sending from it, and any campaign you're sending pauses until you connect a mailbox again. Emails already sent stay logged.</Text>,
      labels: { confirm: "Disconnect", cancel: "Cancel" },
      confirmProps: { color: "red" },
      onConfirm: async () => {
        try {
          await api("mail/account/", { method: "DELETE" });
          await qc.invalidateQueries({ queryKey: ["mail"] });
          notifyOk("Mailbox disconnected.");
        } catch (e) {
          notifyError(e, "Couldn't disconnect");
        }
      },
    });
  }

  const icon = (p: string) => (p === "gmail" ? <IconBrandGmail size={16} /> : <IconBrandWindows size={16} />);

  return (
    <Paper withBorder p="lg">
      <Stack>
        <div>
          <Title order={4}>Email</Title>
          <Text size="sm" c="dimmed">
            Send emails from Dealflow through your own mailbox. They're sent as you, kept in your Sent folder, and logged on the record.
          </Text>
        </div>
        {data.in_sample ? (
          <Text size="sm" c="dimmed">Mailboxes can't be connected while the sample firm is loaded. Set up Dealflow for real first.</Text>
        ) : a ? (
          <>
            {a.needs_reconnect && (
              <Alert color="orange" title={`${a.provider_label} needs to be connected again`}>
                <Text size="sm">{a.last_error || "The permission Dealflow had has ended."} Nothing can be sent until you reconnect.</Text>
              </Alert>
            )}
            <Group gap="sm">
              {icon(a.provider)}
              <Text fw={600}>{a.email}</Text>
              <Badge variant="light" color={a.needs_reconnect ? "orange" : "teal"}>{a.needs_reconnect ? "Reconnect needed" : "Connected"}</Badge>
            </Group>
            <Text size="sm" c="dimmed">
              {a.provider_label} · connected on {formatDate(a.connected_at.slice(0, 10), me)} · sent today: {a.sent_today} of {a.daily_limit}
            </Text>
            <Group gap="xs">
              <Button variant={a.needs_reconnect ? "filled" : "default"} leftSection={<IconPlugConnected size={16} />}
                loading={busy === a.provider} onClick={() => connect(a.provider)}>
                Reconnect
              </Button>
              {data.providers.filter((p) => p.key !== a.provider && p.ready).map((p) => (
                <Button key={p.key} variant="default" leftSection={icon(p.key)} loading={busy === p.key} onClick={() => connect(p.key)}>
                  Use {p.label} instead
                </Button>
              ))}
              <Button variant="subtle" color="red" onClick={disconnect}>Disconnect</Button>
            </Group>
            <Text size="xs" c="dimmed">{HELP[a.provider]}</Text>
          </>
        ) : (
          <>
            <Group gap="xs">
              {data.providers.map((p) => (
                <Button key={p.key} variant="default" leftSection={icon(p.key)} disabled={!p.ready}
                  loading={busy === p.key} onClick={() => connect(p.key)}>
                  Connect {p.label}
                </Button>
              ))}
            </Group>
            {data.providers.some((p) => !p.ready) && (
              <Text size="xs" c="dimmed">
                {data.providers.filter((p) => !p.ready).map((p) => p.label).join(" and ")} isn't set up for this install yet.{" "}
                {me.role.is_admin
                  ? <Anchor component={Link} to="/settings/email" size="xs">Set it up in Settings &gt; Email</Anchor>
                  : "Ask an admin to set it up in Settings > Email."}
              </Text>
            )}
            {data.providers.some((p) => p.ready) && (
              <Text size="xs" c="dimmed">You'll sign in with Google or Microsoft and come back here. {HELP.gmail} {HELP.outlook}</Text>
            )}
          </>
        )}
      </Stack>
    </Paper>
  );
}

/** Google or Microsoft sends people back with ?mailbox=connected (or error): say how it went. */
export function useMailboxReturn() {
  const loc = useLocation();
  const nav = useNavigate();
  const qc = useQueryClient();
  useEffect(() => {
    const p = new URLSearchParams(loc.search);
    const result = p.get("mailbox");
    if (!result) return;
    if (result === "connected") {
      notifications.show({ color: "teal", icon: <IconMail size={16} />, message: "Mailbox connected. You can send email from Dealflow now." });
    } else {
      notifications.show({ color: "red", title: "Mailbox not connected", message: p.get("reason") || "Try connecting again.", autoClose: 10_000 });
    }
    qc.invalidateQueries({ queryKey: ["mail"] });
    p.delete("mailbox");
    p.delete("reason");
    nav({ pathname: loc.pathname, search: p.toString() }, { replace: true });
  }, [loc.search, loc.pathname, nav, qc]);
}
