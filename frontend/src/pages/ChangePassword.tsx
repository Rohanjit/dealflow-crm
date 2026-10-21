import { Alert, Button, Group, Paper, PasswordInput, Stack, Title } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { resetSession } from "../context";
import AuthCard from "../components/AuthCard";
import { errorMessage, fieldErrors, notifyOk } from "../components/errors";

export default function ChangePassword({ forced = false }: { forced?: boolean }) {
  const qc = useQueryClient();
  const [v, setV] = useState({ current_password: "", new_password: "", confirm: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement>) => setV({ ...v, [k]: e.currentTarget.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErrors({});
    setError("");
    if (v.new_password !== v.confirm) {
      setErrors({ confirm: "The two new passwords don't match." });
      return;
    }
    setBusy(true);
    try {
      await api("auth/change-password/", { body: { current_password: v.current_password, new_password: v.new_password } });
      setV({ current_password: "", new_password: "", confirm: "" });
      notifyOk("Password changed.");
      await qc.invalidateQueries({ queryKey: ["app"] });
    } catch (err) {
      setErrors(fieldErrors(err));
      if (!Object.keys(fieldErrors(err)).length) setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    await api("auth/logout/", { method: "POST" });
    await resetSession(qc);
  }

  const form = (
    <form onSubmit={submit}>
      <Stack>
        {error && <Alert color="red">{error}</Alert>}
        <PasswordInput label={forced ? "Temporary password" : "Current password"} required value={v.current_password} onChange={set("current_password")} error={errors.current_password} />
        <PasswordInput label="New password" required value={v.new_password} onChange={set("new_password")} error={errors.new_password} description="At least 12 characters. A few words strung together works well." />
        <PasswordInput label="Repeat new password" required value={v.confirm} onChange={set("confirm")} error={errors.confirm} />
        <Group>
          <Button type="submit" loading={busy}>Change password</Button>
          {forced && <Button variant="subtle" onClick={signOut}>Sign out</Button>}
        </Group>
      </Stack>
    </form>
  );

  if (forced) {
    return <AuthCard title="Choose your own password" subtitle="Your admin gave you a temporary password. Set one only you know to continue.">{form}</AuthCard>;
  }
  return (
    <Paper withBorder p="lg" maw={460}>
      <Title order={4} mb="md">Change password</Title>
      {form}
    </Paper>
  );
}
