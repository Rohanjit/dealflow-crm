// The file behind a Vault document: name, type and size, with Download and (for PDFs and images) Open.

import { Button, Group, Paper, Text, ThemeIcon } from "@mantine/core";
import { IconDownload, IconExternalLink, IconFileText } from "@tabler/icons-react";

import { formatBytes } from "../format";
import type { RecordData } from "../types";
import { downloadUrl, opensInBrowser } from "./api";

export default function FileCard({ doc }: { doc: RecordData }) {
  return (
    <Paper withBorder radius="md" p="md">
      <Group justify="space-between" wrap="wrap" gap="sm">
        <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
          <ThemeIcon variant="light" size="xl" radius="md"><IconFileText size={22} /></ThemeIcon>
          <div style={{ minWidth: 0 }}>
            <Text fw={600} size="sm" truncate>{String(doc.file_name ?? "")}</Text>
            <Text size="xs" c="dimmed">{String(doc.file_type ?? "")} · {formatBytes(Number(doc.file_size ?? 0))}</Text>
          </div>
        </Group>
        <Group gap="xs">
          {opensInBrowser(doc.file_type) && (
            <Button variant="default" leftSection={<IconExternalLink size={16} />} component="a"
              href={downloadUrl(doc.id, true)} target="_blank" rel="noopener noreferrer">Open</Button>
          )}
          <Button leftSection={<IconDownload size={16} />} component="a" href={downloadUrl(doc.id)}>Download</Button>
        </Group>
      </Group>
    </Paper>
  );
}
