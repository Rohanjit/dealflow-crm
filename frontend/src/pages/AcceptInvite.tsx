import { Alert, Button, Center, Loader, PasswordInput, Stack, TextInput } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import AuthCard from "../components/AuthCard";
import { errorMessage, fieldErrors } from "../components/errors";

export default function AcceptInvite() {
  const { token } = useParams();
  const qc = useQueryClient();
  const info = useQuery({
    queryKey: ["invite", token],
    queryFn: () => api<{ firm_name: string; role: string; note: string }>(`invites/accept/${token}/`),
    retry: false,
  });
  const [v, setV] = useState({ full_name: "", email: "", password: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (info.isLoading) return <Center h="100vh"><Loader /></Center>;
  if (info.error) {
    return (
      <AuthCard title="Invite link not valid">
        <Alert color="orange">{errorMessage(info.error)}</Alert>
        <Button component={Link} to="/" variant="light">Go to sign in</Button>
      </AuthCard>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setErrors({});
    try {
      await api(`invites/accept/${token}/`, { body: v });
      window.history.replaceState(null, "", "/");
      await qc.invalidateQueries({ queryKey: ["app"] });
    } catch (err) {
      setErrors(fieldErrors(err));
      if (!Object.keys(fieldErrors(err)).length) setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement>) => setV({ ...v, [k]: e.currentTarget.value });
  return (
    <AuthCard title={`Join ${info.data!.firm_name} on Dealflow`} subtitle={`You'll join with the ${info.data!.role} role.`}>
      <form onSubmit={submit}>
        <Stack>
          {error && <Alert color="red">{error}</Alert>}
          <TextInput label="Your name" required autoFocus value={v.full_name} onChange={set("full_name")} error={errors.full_name} />
          <TextInput label="Work email" type="email" required value={v.email} onChange={set("email")} error={errors.email} />
          <PasswordInput label="Choose a password" required value={v.password} onChange={set("password")} error={errors.password} description="At least 12 characters. Not your name or email, and not a common password." />
          <Button type="submit" loading={busy}>Create my account</Button>
        </Stack>
      </form>
    </AuthCard>
  );
}
