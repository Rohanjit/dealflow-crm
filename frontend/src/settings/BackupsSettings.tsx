import {
  Alert, Button, FileInput, Group, Modal, NumberInput, Paper, Select, Stack, Switch, Table, Text, TextInput, Title,
} from "@mantine/core";
import { IconDownload, IconUpload } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, download } from "../api";
import { useMe } from "../context";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import { formatBytes, formatDateTime } from "../format";

type Backup = { name: string; kind: string; size: number; created_at: string };
type Data = {
  backups: Backup[]; folder: string; backup_keep: number; backup_hour: number; backups_enabled: boolean;
  last_backup_at: string | null; last_backup_error: string;
};
const KIND: Record<string, string> = { daily: "Daily", manual: "Manual", "pre-restore": "Before a restore" };

export default function BackupsSettings() {
  const me = useMe();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["backups"], queryFn: () => api<Data>("backups/") });
  const [cfg, setCfg] = useState({ backups_enabled: true, backup_keep: 7, backup_hour: 2 });
  const [busy, setBusy] = useState<string | null>(null);
  const [restoreOpen, setRestoreOpen] = useState(false);
  useEffect(() => {
    if (data) setCfg({ backups_enabled: data.backups_enabled, backup_keep: data.backup_keep, backup_hour: data.backup_hour });
  }, [data]);

  async function saveCfg() {
    setBusy("cfg");
    try {
      await api("settings/firm/", { method: "PATCH", body: cfg });
      qc.invalidateQueries({ queryKey: ["backups"] });
      notifyOk("Backup schedule saved.");
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(null);
    }
  }

  async function backupNow() {
    setBusy("now");
    try {
      const r = await api<{ name: string }>("backups/", { method: "POST" });
      await qc.invalidateQueries({ queryKey: ["backups"] });
      download(`backups/${r.name}/download/`);
    } catch (e) {
      notifyError(e, "Backup failed");
    } finally {
      setBusy(null);
    }
  }

  const hours = Array.from({ length: 24 }, (_, h) => ({ value: String(h), label: `${String(h).padStart(2, "0")}:00` }));

  return (
    <Stack gap="lg">
      <Paper withBorder p="lg">
        <Stack>
          <Group justify="space-between">
            <div>
              <Title order={4}>Backups</Title>
              <Text size="sm" c="dimmed">Each backup holds all records and uploaded files. They're stored on this server{data ? <> in <code>{data.folder}</code></> : null}.</Text>
            </div>
            <Button leftSection={<IconDownload size={16} />} onClick={backupNow} loading={busy === "now"}>Download a backup now</Button>
          </Group>
          {data?.last_backup_error && <Alert color="red" title="The last automatic backup failed">{data.last_backup_error}</Alert>}
          <Text size="sm">Last automatic backup: <b>{data?.last_backup_at ? formatDateTime(data.last_backup_at, me) : "none yet"}</b></Text>
          <Group align="flex-end">
            <Switch label="Daily automatic backups" checked={cfg.backups_enabled} onChange={(e) => setCfg({ ...cfg, backups_enabled: e.currentTarget.checked })} mb={8} />
            <Select label="Time (server clock)" w={140} data={hours} value={String(cfg.backup_hour)} onChange={(v) => setCfg({ ...cfg, backup_hour: Number(v ?? 2) })} disabled={!cfg.backups_enabled} />
            <NumberInput label="Copies to keep" w={140} min={1} max={365} value={cfg.backup_keep} onChange={(v) => setCfg({ ...cfg, backup_keep: Number(v) || 1 })} />
            <Button variant="default" onClick={saveCfg} loading={busy === "cfg"}>Save schedule</Button>
          </Group>
        </Stack>
      </Paper>

      <Paper withBorder p="lg">
        <Group justify="space-between" mb="sm">
          <Title order={5}>Backups on the server</Title>
          <Button variant="light" color="red" leftSection={<IconUpload size={16} />} onClick={() => setRestoreOpen(true)}>Restore from a file</Button>
        </Group>
        <Table className="df-table" verticalSpacing="xs">
          <Table.Thead><Table.Tr><Table.Th>Taken</Table.Th><Table.Th>Type</Table.Th><Table.Th>Size</Table.Th><Table.Th /></Table.Tr></Table.Thead>
          <Table.Tbody>
            {data?.backups.map((b) => (
              <Table.Tr key={b.name}>
                <Table.Td><Text size="sm">{formatDateTime(b.created_at, me)}</Text></Table.Td>
                <Table.Td><Text size="sm">{KIND[b.kind] ?? b.kind}</Text></Table.Td>
                <Table.Td><Text size="sm">{formatBytes(b.size)}</Text></Table.Td>
                <Table.Td ta="right"><Button size="xs" variant="subtle" leftSection={<IconDownload size={14} />} onClick={() => download(`backups/${b.name}/download/`)}>Download</Button></Table.Td>
              </Table.Tr>
            ))}
            {data && data.backups.length === 0 && <Table.Tr><Table.Td colSpan={4}><Text size="sm" c="dimmed" ta="center" py="md">No backups yet.</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      </Paper>
      {restoreOpen && <RestoreModal onClose={() => setRestoreOpen(false)} />}
    </Stack>
  );
}

function RestoreModal({ onClose }: { onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function restore() {
    if (!file) return;
    setBusy(true);
    setError("");
    const form = new FormData();
    form.append("file", file);
    form.append("confirm", confirm);
    try {
      await api("backups/restore/", { form });
      // Everyone, including you, is signed out by a restore.
      window.location.href = "/";
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Modal opened onClose={onClose} title="Restore from a backup">
      <Stack>
        <Alert color="red" variant="light">
          Restoring replaces <b>everything</b> in Dealflow (records, users, settings and files) with the contents of the backup.
          A safety backup of the current data is taken first. Everyone will be signed out.
        </Alert>
        {error && <Alert color="red">{error}</Alert>}
        <FileInput label="Backup file (.zip)" accept=".zip" value={file} onChange={setFile} />
        <TextInput label="Type RESTORE to confirm" value={confirm} onChange={(e) => setConfirm(e.currentTarget.value)} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button color="red" onClick={restore} loading={busy} disabled={!file || confirm !== "RESTORE"}>Restore</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
