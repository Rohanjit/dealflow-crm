// Restricted deals (backend deals/access.py): the "Restricted deal" placeholder people without
// access see, the list of those on list pages, the Access panel on a deal, and access requests.

import { Alert, Badge, Button, Group, Paper, Select, Stack, Text, ThemeIcon, Title } from "@mantine/core";
import { modals } from "@mantine/modals";
import { IconLock, IconLockOpen, IconUserPlus, IconX } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { errorMessage, notifyError, notifyOk } from "../components/errors";
import { useApp, useMe, useUsers } from "../context";
import { formatDate } from "../format";
import type { Prefs } from "../format";

export type Placeholder = {
  id: number;
  approvers: string[];
  request: { status: "pending" | "approved" | "declined" | "withdrawn"; at: string } | null;
};

export type AccessRequest = {
  id: number; deal: number; deal_title: string; kind: string; what: string;
  person: { id: number; name: string } | null; requested_by: { id: number; name: string };
  created_at: string; status: string;
};

type Access = {
  restricted: boolean;
  team: { id: number; name: string; role: string }[];
  people: { id: number; name: string }[];
  approvers: string[];
  can_request: boolean;
  can_approve: boolean;
  pending: AccessRequest[];
  result?: "pending" | "done";
};

const names = (xs: string[]) => (xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} or ${xs[xs.length - 1]}`);

function requestLine(p: Placeholder, prefs: Prefs): string | null {
  if (!p.request) return null;
  const day = formatDate(p.request.at.slice(0, 10), prefs);
  if (p.request.status === "pending") return `You asked for access on ${day}. Waiting for ${names(p.approvers)}.`;
  if (p.request.status === "declined") return `Your request was declined on ${day}. You can ask again.`;
  return null;
}

/** "Ask for access" for one restricted deal; returns the updated placeholder. */
async function askAccess(id: number): Promise<Placeholder | null> {
  try {
    const p = await api<Placeholder>(`deals/${id}/access/ask/`, { body: {} });
    notifyOk("Request sent. You'll see the deal once it's approved.");
    return p;
  } catch (e) {
    notifyError(e, "Couldn't send the request");
    return null;
  }
}

/** Full-page placeholder shown instead of a deal someone can't see. */
export function RestrictedPage({ placeholder }: { placeholder: Placeholder }) {
  const me = useMe();
  const [p, setP] = useState(placeholder);
  const [busy, setBusy] = useState(false);
  const waiting = p.request?.status === "pending";
  return (
    <Paper withBorder p="xl" maw={560} mt="lg">
      <Stack gap="sm">
        <ThemeIcon size={44} radius="md" variant="light" color="gray"><IconLock size={24} /></ThemeIcon>
        <Title order={3}>Restricted deal</Title>
        <Text size="sm" c="dimmed">
          Only the deal team, people they've added and admins can see this deal.
          {p.approvers.length > 0 && ` ${names(p.approvers)} can give you access.`}
        </Text>
        {requestLine(p, me) && <Text size="sm">{requestLine(p, me)}</Text>}
        {!waiting && (
          <Group>
            <Button leftSection={<IconUserPlus size={16} />} loading={busy}
              onClick={async () => { setBusy(true); const n = await askAccess(p.id); if (n) setP(n); setBusy(false); }}>
              Ask for access
            </Button>
          </Group>
        )}
      </Stack>
    </Paper>
  );
}

/** Below a list (or a record's related list): restricted deals that are left out. */
export function RestrictedList({ items, where = "here" }: { items: Placeholder[]; where?: string }) {
  const me = useMe();
  const [state, setState] = useState<Record<number, Placeholder>>({});
  const [busy, setBusy] = useState<number | null>(null);
  if (!items.length) return null;
  return (
    <Paper withBorder radius="md" p="md" bg="gray.0">
      <Group gap={8} mb={6}>
        <IconLock size={16} />
        <Text size="sm" fw={600}>
          {items.length === 1 ? "1 restricted deal isn't shown" : `${items.length} restricted deals aren't shown`} {where}
        </Text>
      </Group>
      <Text size="xs" c="dimmed" mb="sm">Only their deal teams can see them. Ask for access if you need one.</Text>
      <Stack gap={8}>
        {items.map((raw) => {
          const p = state[raw.id] ?? raw;
          return (
            <Group key={p.id} justify="space-between" wrap="nowrap" gap="sm">
              <div style={{ minWidth: 0 }}>
                <Text size="sm">Restricted deal</Text>
                <Text size="xs" c="dimmed">{requestLine(p, me) ?? (p.approvers.length ? `Access from ${names(p.approvers)}` : "")}</Text>
              </div>
              {p.request?.status !== "pending" && (
                <Button size="compact-xs" variant="light" loading={busy === p.id}
                  onClick={async () => { setBusy(p.id); const n = await askAccess(p.id); if (n) setState((s) => ({ ...s, [p.id]: n })); setBusy(null); }}>
                  Ask for access
                </Button>
              )}
            </Group>
          );
        })}
      </Stack>
    </Paper>
  );
}

/** The Access panel on a deal: restricted or not, who can see it, and changes. */
export function AccessPanel({ dealId }: { dealId: number }) {
  const qc = useQueryClient();
  const { me } = useApp();
  const { data: users } = useUsers();
  const key = ["deal-access", dealId];
  const { data, error } = useQuery({ queryKey: key, queryFn: () => api<Access>(`deals/${dealId}/access/`) });
  const [adding, setAdding] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function act(action: string, person?: number, label = action) {
    setBusy(label);
    try {
      const r = await api<Access>(`deals/${dealId}/access/`, { body: { action, person } });
      qc.setQueryData(key, r);
      await qc.invalidateQueries({ queryKey: ["record", "deals", String(dealId)] });
      await qc.invalidateQueries({ queryKey: ["notes", "deals", dealId] });
      await qc.invalidateQueries({ queryKey: ["access-requests"] });
      notifyOk(r.result === "pending" ? `Sent to ${names(r.approvers)} for approval.` : "Done.");
      setAdding(null);
    } catch (e) {
      notifyError(e, "Couldn't change access");
    } finally {
      setBusy(null);
    }
  }
  async function decide(id: number, decision: "approve" | "decline" | "withdraw") {
    setBusy(`${decision}${id}`);
    try {
      await api(`access-requests/${id}/`, { body: { decision } });
      notifyOk(decision === "approve" ? "Approved." : decision === "decline" ? "Declined." : "Request withdrawn.");
      await qc.invalidateQueries({ queryKey: key });
      await qc.invalidateQueries({ queryKey: ["record", "deals", String(dealId)] });
      await qc.invalidateQueries({ queryKey: ["access-requests"] });
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(null);
    }
  }

  if (error) return <Paper withBorder radius="md" p="lg"><Text c="red" size="sm">{errorMessage(error)}</Text></Paper>;
  if (!data) return null;
  const needsApproval = !data.can_approve;
  const waitingOn = (kind: string) => data.pending.some((r) => r.kind === kind);
  const confirmLift = () => modals.openConfirmModal({
    title: "Lift the restriction?",
    children: <Text size="sm">Everyone whose role can see Active Deals will see this deal, its tracker, documents and tasks.</Text>,
    labels: { confirm: needsApproval ? "Ask to lift it" : "Lift restriction", cancel: "Cancel" },
    onConfirm: () => act("unrestrict"),
  });
  const shown = new Set([...data.team.map((t) => t.id), ...data.people.map((p) => p.id)]);
  const choices = (users ?? []).filter((u) => u.is_active && !shown.has(u.id)).map((u) => ({ value: String(u.id), label: u.full_name }));

  return (
    <Paper withBorder radius="md">
      <Group justify="space-between" px="lg" pt="md" pb="sm">
        <Group gap="xs">
          <Text fw={600}>Access</Text>
          {data.restricted
            ? <Badge color="orange" variant="light" leftSection={<IconLock size={12} />} tt="none">Restricted</Badge>
            : <Badge color="gray" variant="light" tt="none">Open to everyone with deal access</Badge>}
        </Group>
        {data.can_request && !waitingOn(data.restricted ? "unrestrict" : "restrict") && (data.restricted ? (
          <Button size="xs" variant="default" leftSection={<IconLockOpen size={14} />} loading={busy === "unrestrict"} onClick={confirmLift}>
            {needsApproval ? "Ask to lift restriction" : "Lift restriction"}
          </Button>
        ) : (
          <Button size="xs" variant="light" color="orange" leftSection={<IconLock size={14} />} loading={busy === "restrict"} onClick={() => act("restrict")}>
            {needsApproval ? "Ask to restrict" : "Restrict this deal"}
          </Button>
        ))}
      </Group>
      <Stack px="lg" pb="lg" gap="sm">
        <Text size="sm" c="dimmed">
          {data.restricted
            ? "Only the deal team, the people below and admins can see this deal and everything about it."
            : "Restricting hides this deal, its tracker, documents and tasks from everyone except the deal team, people you add and admins."}
          {needsApproval && data.can_request && ` Changes are approved by ${names(data.approvers)}.`}
        </Text>
        {data.pending.map((r) => (
          <Alert key={r.id} variant="light" color="yellow" p="xs">
            <Group justify="space-between" wrap="nowrap">
              <Text size="sm">{r.what} · asked by {r.requested_by.id === me.id ? "you" : r.requested_by.name}</Text>
              <Group gap={4} wrap="nowrap">
                {data.can_approve && r.requested_by.id !== me.id && (
                  <>
                    <Button size="compact-xs" color="teal" loading={busy === `approve${r.id}`} onClick={() => decide(r.id, "approve")}>Approve</Button>
                    <Button size="compact-xs" variant="default" loading={busy === `decline${r.id}`} onClick={() => decide(r.id, "decline")}>Decline</Button>
                  </>
                )}
                {r.requested_by.id === me.id && (
                  <Button size="compact-xs" variant="subtle" color="gray" onClick={() => decide(r.id, "withdraw")}>Withdraw</Button>
                )}
              </Group>
            </Group>
          </Alert>
        ))}
        <div>
          <Text size="xs" fw={600} c="dimmed" tt="uppercase" mb={4}>Deal team</Text>
          {data.team.length === 0 ? <Text size="sm" c="dimmed">No one on the deal team yet. Add them by editing the deal.</Text> : (
            <Group gap={6}>{data.team.map((t) => <Badge key={`${t.role}${t.id}`} variant="light" color="gray" tt="none">{t.name} · {t.role}</Badge>)}</Group>
          )}
        </div>
        {data.restricted && (
          <div>
            <Text size="xs" fw={600} c="dimmed" tt="uppercase" mb={4}>Also added</Text>
            {data.people.length === 0 && <Text size="sm" c="dimmed">Nobody else.</Text>}
            <Stack gap={4}>
              {data.people.map((p) => (
                <Group key={p.id} gap="xs">
                  <Text size="sm">{p.name}</Text>
                  {data.can_request && (
                    <Button size="compact-xs" variant="subtle" color="red" leftSection={<IconX size={12} />} loading={busy === `remove${p.id}`}
                      onClick={() => act("remove", p.id, `remove${p.id}`)}>Remove</Button>
                  )}
                </Group>
              ))}
            </Stack>
            {data.can_request && (
              <Group mt="sm" gap="xs" align="flex-end">
                <Select size="xs" w={240} placeholder="Choose a person" searchable data={choices} value={adding} onChange={setAdding}
                  label={needsApproval ? "Ask to give someone access" : "Give someone access"} />
                <Button size="xs" disabled={!adding} loading={busy === "add"} onClick={() => act("add", Number(adding), "add")}>
                  {needsApproval ? "Ask" : "Add"}
                </Button>
              </Group>
            )}
          </div>
        )}
      </Stack>
    </Paper>
  );
}

export type AccessRequests = { to_decide: AccessRequest[]; mine: AccessRequest[]; count: number };

export function useAccessRequests(enabled: boolean) {
  return useQuery({
    queryKey: ["access-requests"],
    queryFn: () => api<AccessRequests>("access-requests/"),
    refetchInterval: 60_000,
    enabled,
  });
}

export function useDecide() {
  const qc = useQueryClient();
  return async (r: AccessRequest, decision: "approve" | "decline") => {
    try {
      const fresh = await api<AccessRequests>(`access-requests/${r.id}/`, { body: { decision } });
      qc.setQueryData(["access-requests"], fresh);
      await qc.invalidateQueries({ queryKey: ["deal-access", r.deal] });
      notifyOk(decision === "approve" ? `Approved: ${r.what}.` : "Declined.");
    } catch (e) {
      notifyError(e);
    }
  };
}

