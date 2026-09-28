import { Alert, Button, PasswordInput, Stack, Text, TextInput } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { resetSession } from "../context";
import AuthCard from "../components/AuthCard";
import { errorMessage } from "../components/errors";

export default function Login({ firmName }: { firmName: string }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

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

  return (
    <AuthCard title={firmName ? `Sign in to ${firmName}` : "Sign in"}>
      <form onSubmit={submit}>
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
