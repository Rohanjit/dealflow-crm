// Upload one or more files to the Vault with shared details: category, what they're about, notes.

import {
  ActionIcon, Alert, Anchor, Badge, Breadcrumbs, Button, Group, Paper, Select, SimpleGrid, Stack, Text, Textarea,
  TextInput, Title, UnstyledButton,
} from "@mantine/core";
import { IconCloudUpload, IconFile, IconX } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../api";
import { errorMessage, fieldErrors, notifyOk } from "../components/errors";
import { useSchema } from "../context";
import { formatBytes } from "../format";
import { MultiLinkInput, useLinkTitle } from "../module/LinkInput";
import type { LinkValue, ModuleSchema, RecordData } from "../types";
import { useVaultStatus } from "./api";

type Picked = { file: File; title: string; problem: string };

function stem(name: string) {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(0, i) : name;
}

export default function UploadPage({ module }: { module: ModuleSchema }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const { data: schema } = useSchema();
  const { data: status } = useVaultStatus();
  const input = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<Picked[]>([]);
  const [over, setOver] = useState(false);
  const [category, setCategory] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const returnTo = /^\/m\/[a-z_]+\/\d+$/.test(params.get("return") ?? "") ? params.get("return")! : null;

  // A page for a deal, investor etc. starts the upload tagged to it: ?tagged=deals:5
  const tagField = module.fields.find((f) => f.key === "tagged")!;
  const pre = params.get("tagged");
  const [pm, pid] = (pre ?? "").split(":");
  const preLink = useLinkTitle(pre && tagField.targets.includes(pm) && /^\d+$/.test(pid ?? "")
    ? { module: pm, id: Number(pid), title: "", archived: false } : null);
  const [tags, setTags] = useState<LinkValue[] | null>(null);
  const tagged = tags ?? (preLink && preLink.title ? [preLink] : []);

  const accepted = status?.accepted ?? [];
  const maxMb = status?.max_file_mb ?? 50;
  const categories = (schema?.choice_lists.document_categories?.options ?? []).filter((o) => o.is_active)
    .map((o) => ({ value: String(o.id), label: o.label }));

  function add(files: FileList | File[]) {
    const next = [...picked];
    for (const file of Array.from(files)) {
      if (next.some((p) => p.file.name === file.name && p.file.size === file.size)) continue;
      const ext = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
      const problem = !accepted.includes(ext) ? `Not accepted (.${ext || "?"})`
        : file.size > maxMb * 1024 * 1024 ? `Larger than ${maxMb} MB` : file.size === 0 ? "Empty file" : "";
      next.push({ file, title: stem(file.name), problem });
    }
    setPicked(next);
  }

  async function upload() {
    const ok = picked.filter((p) => !p.problem);
    if (!ok.length) return;
    setBusy(true);
    setErrors({});
    setProblems([]);
    const form = new FormData();
    ok.forEach((p) => form.append("files", p.file, p.file.name));
    form.append("data", JSON.stringify({
      category: category ? Number(category) : null,
      description,
      tagged: tagged.map((t) => `${t.module}:${t.id}`),
      titles: ok.map((p) => p.title),
    }));
    try {
      const docs = await api<RecordData[]>("vault/upload/", { form });
      await qc.invalidateQueries({ queryKey: ["records", "vault"] });
      notifyOk(docs.length === 1 ? `${docs[0]._title} is in the Vault.` : `${docs.length} documents are in the Vault.`);
      nav(returnTo ?? (docs.length === 1 ? `/m/vault/${docs[0].id}` : "/m/vault"), { replace: true });
    } catch (e) {
      const fe = fieldErrors(e);
      const list = (e as { fields?: Record<string, string> }).fields?.files;
      setProblems(list ? [list] : []);
      setErrors(fe);
      if (!Object.keys(fe).length) setProblems([errorMessage(e)]);
    } finally {
      setBusy(false);
    }
  }

  const good = picked.filter((p) => !p.problem).length;
  const totalMb = picked.filter((p) => !p.problem).reduce((n, p) => n + p.file.size, 0) / (1024 * 1024);
  const maxUploadMb = status?.max_upload_mb ?? 1024;
  const tooBig = totalMb > maxUploadMb;
  return (
    <Stack gap="lg" maw={1000}>
      <Breadcrumbs>
        <Anchor component={Link} to="/m/vault" size="sm">Vault</Anchor>
        <Text size="sm">Upload</Text>
      </Breadcrumbs>
      <Title order={2}>Upload documents</Title>
      {tooBig && <Alert color="orange">These files add up to {Math.ceil(totalMb)} MB. Upload at most {maxUploadMb} MB at a time.</Alert>}
      {problems.length > 0 && <Alert color="red">{problems.map((p, i) => <div key={i}>{p}</div>)}</Alert>}

      <Paper withBorder radius="md" p="lg">
        <UnstyledButton w="100%" onClick={() => input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); add(e.dataTransfer.files); }}
          style={{
            border: `2px dashed var(--mantine-color-${over ? "navy-5" : "gray-4"})`, borderRadius: 8, padding: 28,
            textAlign: "center", background: over ? "var(--mantine-color-navy-0)" : undefined,
          }}>
          <IconCloudUpload size={32} color="var(--mantine-color-navy-6)" />
          <Text fw={600} mt={4}>Drop files here, or click to choose</Text>
          <Text size="xs" c="dimmed">PDF, Word, Excel, PowerPoint, OpenDocument, CSV, text, email, images or ZIP, up to {maxMb} MB each</Text>
        </UnstyledButton>
        <input ref={input} type="file" multiple hidden accept={accepted.map((a) => `.${a}`).join(",")}
          onChange={(e) => { if (e.currentTarget.files) add(e.currentTarget.files); e.currentTarget.value = ""; }} />

        {picked.length > 0 && (
          <Stack gap={6} mt="md">
            {picked.map((p, i) => (
              <Group key={`${p.file.name}-${i}`} gap="sm" wrap="nowrap" align="center">
                <IconFile size={18} color="var(--mantine-color-gray-6)" style={{ flexShrink: 0 }} />
                <TextInput style={{ flex: 1 }} size="sm" aria-label={`Title for ${p.file.name}`} value={p.title} disabled={!!p.problem}
                  onChange={(e) => { const t = e.currentTarget.value; setPicked((all) => all.map((x, j) => (j === i ? { ...x, title: t } : x))); }} />
                <Text size="xs" c="dimmed" w={170} truncate title={p.file.name}>{p.file.name}</Text>
                <Text size="xs" c="dimmed" w={64} ta="right">{formatBytes(p.file.size)}</Text>
                {p.problem ? <Badge color="red" variant="light" tt="none" w={180}>{p.problem}</Badge> : null}
                <ActionIcon variant="subtle" color="gray" aria-label={`Remove ${p.file.name}`}
                  onClick={() => setPicked((all) => all.filter((_, j) => j !== i))}><IconX size={16} /></ActionIcon>
              </Group>
            ))}
          </Stack>
        )}
      </Paper>

      <Paper withBorder radius="md" p="lg">
        <Text fw={600} mb="md">Details for {picked.length > 1 ? "these documents" : "this document"}</Text>
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
          <Select label="Category" searchable clearable data={categories} value={category} onChange={setCategory} error={errors.category} />
          <MultiLinkInput f={tagField} value={tagged} onChange={setTags} error={errors.tagged} />
          <div style={{ gridColumn: "1 / -1" }}>
            <Textarea label="Description" autosize minRows={2} value={description} onChange={(e) => setDescription(e.currentTarget.value)}
              error={errors.description} />
          </div>
        </SimpleGrid>
      </Paper>

      <Group justify="flex-end" pb="xl">
        <Button variant="default" component={Link} to={returnTo ?? "/m/vault"}>Cancel</Button>
        <Button onClick={upload} loading={busy} disabled={!good || tooBig} leftSection={<IconCloudUpload size={16} />}>
          {good > 1 ? `Upload ${good} documents` : "Upload"}
        </Button>
      </Group>
    </Stack>
  );
}
