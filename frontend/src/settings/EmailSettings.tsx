// Settings > Email (admins): this install's app registrations with Google and Microsoft, which
// let each person connect their own mailbox, plus sending limits and the campaign opt-out line.

import {
  ActionIcon, Alert, Badge, Button, Code, CopyButton, Group, List, NumberInput, Paper, PasswordInput, SimpleGrid, Stack,
  Text, TextInput, Title, Tooltip,
} from "@mantine/core";
import { IconCheck, IconCopy } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api";
import { errorMessage, fieldErrors, notifyOk } from "../components/errors";
import { Skeleton } from "../components/ui";

type Provider = { client_id: string; has_secret: boolean; from_env: boolean; ready: boolean; redirect_uri: string; tenant?: string };
type Settings = {
  gmail: Provider; outlook: Provider; public_url: string; gmail_daily_limit: number; outlook_daily_limit: number;
  per_minute: number; opt_out_text: string; connected: number;
};

function Copyable({ value }: { value: string }) {
  return (
    <Group gap={4} wrap="nowrap">
      <Code style={{ wordBreak: "break-all" }}>{value}</Code>
      <CopyButton value={value}>
        {({ copied, copy }) => (
          <Tooltip label={copied ? "Copied" : "Copy"}>
            <ActionIcon variant="subtle" color={copied ? "teal" : "gray"} onClick={copy} aria-label="Copy the address">
              {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
            </ActionIcon>
          </Tooltip>
        )}
      </CopyButton>
    </Group>
  );
}

export default function EmailSettings() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["mail", "settings"], queryFn: () => api<Settings>("mail/settings/") });
  const [v, setV] = useState<Record<string, unknown>>({});
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (data) {
      setV({
        gmail_client_id: data.gmail.client_id, gmail_client_secret: "", outlook_client_id: data.outlook.client_id,
        outlook_client_secret: "", outlook_tenant: data.outlook.tenant ?? "common", public_url: data.public_url,
        gmail_daily_limit: data.gmail_daily_limit, outlook_daily_limit: data.outlook_daily_limit, per_minute: data.per_minute,
        opt_out_text: data.opt_out_text,
      });
    }
  }, [data]);

  if (isLoading) return <Stack><Skeleton h={160} /><Skeleton h={160} /></Stack>;
  if (error || !data) return <Text c="red">{errorMessage(error)}</Text>;
  const set = (k: string) => (x: unknown) => setV((o) => ({ ...o, [k]: x }));
  const str = (k: string) => String(v[k] ?? "");

  async function save(extra: Record<string, unknown> = {}) {
    setBusy(true);
    setProblem("");
    setErrors({});
    try {
      const body: Record<string, unknown> = { ...v, ...extra, password };
      if (data!.gmail.from_env) { delete body.gmail_client_id; delete body.gmail_client_secret; }
      if (data!.outlook.from_env) { delete body.outlook_client_id; delete body.outlook_client_secret; }
      await api("mail/settings/", { method: "PUT", body });
      await qc.invalidateQueries({ queryKey: ["mail"] });
      setPassword("");
      notifyOk("Email settings saved.");
    } catch (e) {
      setErrors(fieldErrors(e));
      setProblem(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const status = (p: Provider) => (
    <Badge variant="light" color={p.ready ? "teal" : "gray"}>{p.ready ? "Ready" : "Not set up"}</Badge>
  );

  return (
    <Stack maw={860} gap="lg">
      <div>
        <Title order={4}>Email</Title>
        <Text size="sm" c="dimmed">
          Each person connects their own Gmail or Outlook in My preferences and sends as themselves. For that, this install
          needs to be registered once with Google, Microsoft, or both. {data.connected
            ? `${data.connected} ${data.connected === 1 ? "person has" : "people have"} connected a mailbox.` : ""}
        </Text>
      </div>

      <Paper withBorder p="lg">
        <Stack gap="sm">
          <Title order={5}>Address of this install</Title>
          <TextInput label="Address people open Dealflow at" placeholder="https://crm.yourfirm.com" value={str("public_url")}
            onChange={(e) => set("public_url")(e.currentTarget.value)} error={errors.public_url}
            description="Google and Microsoft send people back here after they connect. Leave empty to use the address you're on now. Google needs https unless it's localhost." />
        </Stack>
      </Paper>

      <Paper withBorder p="lg">
        <Stack gap="sm">
          <Group justify="space-between"><Title order={5}>Gmail and Google Workspace</Title>{status(data.gmail)}</Group>
          <List size="sm" type="ordered" spacing={2}>
            <List.Item>In the Google Cloud console, create a project and turn on the Gmail API.</List.Item>
            <List.Item>Set up the OAuth consent screen and add the scope <Code>gmail.send</Code> (only sending).</List.Item>
            <List.Item>Create an OAuth client ID of type “Web application” with this redirect address:</List.Item>
          </List>
          <Copyable value={data.gmail.redirect_uri} />
          {data.gmail.from_env ? (
            <Alert color="gray" py="xs">Set by the server's environment variables (client ID {data.gmail.client_id}).</Alert>
          ) : (
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput label="Client ID" value={str("gmail_client_id")} onChange={(e) => set("gmail_client_id")(e.currentTarget.value)}
                error={errors.gmail_client_id} />
              <PasswordInput label="Client secret" value={str("gmail_client_secret")} error={errors.gmail_client_secret}
                placeholder={data.gmail.has_secret ? "Saved. Leave empty to keep it" : ""}
                onChange={(e) => set("gmail_client_secret")(e.currentTarget.value)} />
            </SimpleGrid>
          )}
          {!data.gmail.from_env && data.gmail.ready && (
            <Group><Button size="xs" variant="subtle" color="red" onClick={() => save({ clear_gmail: true })} disabled={!password}>
              Remove Gmail set-up</Button></Group>
          )}
        </Stack>
      </Paper>

      <Paper withBorder p="lg">
        <Stack gap="sm">
          <Group justify="space-between"><Title order={5}>Outlook and Microsoft 365</Title>{status(data.outlook)}</Group>
          <List size="sm" type="ordered" spacing={2}>
            <List.Item>In the Microsoft Entra admin center, open App registrations and add a new registration.</List.Item>
            <List.Item>Add the “Web” platform with this redirect address:</List.Item>
          </List>
          <Copyable value={data.outlook.redirect_uri} />
          <List size="sm" type="ordered" spacing={2} start={3}>
            <List.Item>Under API permissions add the delegated Microsoft Graph permissions <Code>Mail.Send</Code> and <Code>Mail.ReadWrite</Code> (to confirm each email reached Sent Items).</List.Item>
            <List.Item>Under Certificates &amp; secrets create a client secret, and paste both here.</List.Item>
          </List>
          {data.outlook.from_env ? (
            <Alert color="gray" py="xs">Set by the server's environment variables (application ID {data.outlook.client_id}).</Alert>
          ) : (
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput label="Application (client) ID" value={str("outlook_client_id")}
                onChange={(e) => set("outlook_client_id")(e.currentTarget.value)} error={errors.outlook_client_id} />
              <PasswordInput label="Client secret" value={str("outlook_client_secret")} error={errors.outlook_client_secret}
                placeholder={data.outlook.has_secret ? "Saved. Leave empty to keep it" : ""}
                onChange={(e) => set("outlook_client_secret")(e.currentTarget.value)} />
            </SimpleGrid>
          )}
          <TextInput label="Who can connect" value={str("outlook_tenant")} onChange={(e) => set("outlook_tenant")(e.currentTarget.value)}
            error={errors.outlook_tenant} maw={360}
            description="“common” lets any Microsoft account connect. Put your directory (tenant) ID or domain to allow only your firm's." />
          {!data.outlook.from_env && data.outlook.ready && (
            <Group><Button size="xs" variant="subtle" color="red" onClick={() => save({ clear_outlook: true })} disabled={!password}>
              Remove Outlook set-up</Button></Group>
          )}
        </Stack>
      </Paper>

      <Paper withBorder p="lg">
        <Stack gap="sm">
          <Title order={5}>Sending limits and campaigns</Title>
          <Text size="sm" c="dimmed">Kept under Google's and Microsoft's own limits so mailboxes aren't flagged. Campaigns pause at the limit and carry on the next day.</Text>
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <NumberInput label="Gmail: emails a day per person" min={1} max={2000} value={Number(v.gmail_daily_limit)}
              onChange={set("gmail_daily_limit")} error={errors.gmail_daily_limit} allowDecimal={false} />
            <NumberInput label="Outlook: emails a day per person" min={1} max={10000} value={Number(v.outlook_daily_limit)}
              onChange={set("outlook_daily_limit")} error={errors.outlook_daily_limit} allowDecimal={false} />
            <NumberInput label="Campaign emails a minute" min={1} max={60} value={Number(v.per_minute)}
              onChange={set("per_minute")} error={errors.per_minute} allowDecimal={false} />
          </SimpleGrid>
          <TextInput label="Opt-out line at the end of every campaign email" value={str("opt_out_text")}
            onChange={(e) => set("opt_out_text")(e.currentTarget.value)} error={errors.opt_out_text} maxLength={300}
            description="When someone asks to stop, tick “No campaign emails” on their contact. Campaigns skip them." />
        </Stack>
      </Paper>

      <Paper withBorder p="lg">
        <Group align="flex-end" gap="sm">
          <PasswordInput label="Your password" description="Needed to save email settings." value={password}
            onChange={(e) => setPassword(e.currentTarget.value)} error={errors.password} w={280} />
          <Button onClick={() => save()} loading={busy} disabled={!password}>Save email settings</Button>
        </Group>
        {problem && !Object.keys(errors).length && <Text c="red" size="sm" mt="xs">{problem}</Text>}
      </Paper>
    </Stack>
  );
}
