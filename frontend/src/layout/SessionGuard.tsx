// Idle sign-out (decision 63): a minute before the firm's idle time runs out, ask whether the person
// is still there. Typing or clicking counts as activity even when it sends nothing, so a long draft
// isn't lost; the app's own polling doesn't count.

import { Button, Group, Modal, Stack, Text } from "@mantine/core";
import { useEffect, useRef, useState } from "react";

import { api, lastActive, markActive } from "../api";
import { useApp } from "../context";
import type { SessionInfo } from "../types";

const WARN = 60;

export default function SessionGuard() {
  const { info } = useApp();
  const idle = info.session?.idle_seconds ?? 30 * 60;
  const loadedAt = useRef(Date.now());
  const absoluteEnd = useRef(Date.now() + (info.session?.absolute_left ?? 12 * 3600) * 1000);
  const lastInput = useRef(0);
  const lastPing = useRef(0);
  const [left, setLeft] = useState<number | null>(null);

  useEffect(() => {
    absoluteEnd.current = loadedAt.current + (info.session?.absolute_left ?? 12 * 3600) * 1000;
  }, [info.session?.absolute_left]);

  useEffect(() => {
    const onInput = () => { lastInput.current = Date.now(); };
    window.addEventListener("keydown", onInput, { passive: true });
    window.addEventListener("pointerdown", onInput, { passive: true });
    const tick = window.setInterval(async () => {
      const now = Date.now();
      // Someone typing or clicking without sending anything: tell the server they're here,
      // at most every 2 minutes, once half the idle time has gone.
      if (lastInput.current > lastActive() && now - lastActive() > (idle * 1000) / 2 && now - lastPing.current > 120_000) {
        lastPing.current = now;
        try { await api("auth/session/", { method: "POST" }); } catch { /* signed out: handled by api() */ }
      }
      const idleLeft = Math.round((lastActive() + idle * 1000 - now) / 1000);
      const absLeft = Math.round((absoluteEnd.current - now) / 1000);
      const remaining = Math.min(idleLeft, absLeft);
      if (remaining <= 0) {
        // Let the server decide (it signs out and the app goes back to the sign-in page).
        try {
          const s = await api<SessionInfo>("auth/session/", { background: true });
          if (s.idle_left > 0) {
            markActive();
            setLeft(null);
          }
        } catch { /* handled by api() */ }
        return;
      }
      setLeft(remaining <= WARN ? remaining : null);
    }, 1000);
    return () => {
      window.clearInterval(tick);
      window.removeEventListener("keydown", onInput);
      window.removeEventListener("pointerdown", onInput);
    };
  }, [idle]);

  const absolute = left !== null && absoluteEnd.current - Date.now() <= WARN * 1000 + 500;
  return (
    <Modal opened={left !== null} onClose={() => {}} withCloseButton={false} closeOnClickOutside={false} closeOnEscape={false}
      title={absolute ? "Your 12 hours are nearly up" : "Are you still there?"} centered zIndex={400}>
      <Stack>
        <Text size="sm">
          {absolute
            ? `For safety, everyone signs in again after 12 hours. You'll be signed out in ${left} seconds; save what you're doing.`
            : `You'll be signed out in ${left} seconds because nothing has happened for a while.`}
        </Text>
        {!absolute && (
          <Group justify="flex-end">
            <Button onClick={async () => { try { await api("auth/session/", { method: "POST" }); setLeft(null); } catch { /* handled */ } }}>
              Stay signed in
            </Button>
          </Group>
        )}
      </Stack>
    </Modal>
  );
}
