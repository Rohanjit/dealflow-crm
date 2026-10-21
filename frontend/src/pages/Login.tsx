import { Alert, Button, PasswordInput, Stack, Text, TextInput } from "@mantine/core";
import { IconCompass } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { resetSession, takeSignedOutReason } from "../context";
import AuthCard from "../components/AuthCard";
import { errorMessage } from "../components/errors";
import { startGuest } from "../components/guest";

export default function Login({ firmName, guest = false }: { firmName: string; guest?: boolean }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice] = useState(takeSignedOutReason);
  const [busy, setBusy] = useState(false);
  const [guestBusy, setGuestBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("auth/login/", { body: { email, password } });
      await resetSession(qc);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  // A demo site has no accounts to sign in to: just the way in as a guest.
  if (guest) {
    return (
      <AuthCard title="Try Dealflow" subtitle="A sample firm with investors, outreach, deals, tasks and documents. No sign-up needed.">
        <Stack>
          {error && <Alert color="red">{error}</Alert>}
          <Button size="md" leftSection={<IconCompass size={18} />} loading={guestBusy}
            onClick={async () => { setGuestBusy(true); try { await startGuest(qc); } catch (e) { setError(errorMessage(e)); setGuestBusy(false); } }}>
            Continue as guest
          </Button>
          <Text size="xs" c="dimmed">Everyone on the demo shares the same sample data, and it resets every day. Don't enter anything real.</Text>
        </Stack>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={firmName ? `Sign in to ${firmName}` : "Sign in"}>
      <form onSubmit={submit}>
        {notice && !error && <Alert color="blue" mb="md">{notice}</Alert>}
        <Stack>
          {error && <Alert color="red">{error}</Alert>}
          <TextInput label="Email" type="email" required autoFocus value={email} onChange={(e) => setEmail(e.currentTarget.value)} />
          <PasswordInput label="Password" required value={password} onChange={(e) => setPassword(e.currentTarget.value)} />
          <Button type="submit" loading={busy}>Sign in</Button>
          <Text size="xs" c="dimmed">Forgot your password? Ask your Dealflow admin to reset it.</Text>
        </Stack>
      </form>
    </AuthCard>
  );
}
