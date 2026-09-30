import { Alert, Button, PasswordInput, Stack, TextInput } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import AuthCard from "../components/AuthCard";
import { errorMessage, fieldErrors } from "../components/errors";

export default function Setup() {
  const qc = useQueryClient();
  const [v, setV] = useState({ firm_name: "", full_name: "", email: "", password: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement>) => setV({ ...v, [k]: e.currentTarget.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setErrors({});
    try {
      await api("setup/", { body: v });
      await qc.invalidateQueries({ queryKey: ["app"] });
    } catch (err) {
      setErrors(fieldErrors(err));
      if (!Object.keys(fieldErrors(err)).length) setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Set up Dealflow" subtitle="Name your firm and create the first Admin account. You can add your team next.">
      <form onSubmit={submit}>
        <Stack>
          {error && <Alert color="red">{error}</Alert>}
          <TextInput label="Firm name" required autoFocus value={v.firm_name} onChange={set("firm_name")} error={errors.firm_name} />
          <TextInput label="Your name" required value={v.full_name} onChange={set("full_name")} error={errors.full_name} />
          <TextInput label="Work email" type="email" required value={v.email} onChange={set("email")} error={errors.email} description="You'll sign in with this." />
          <PasswordInput label="Password" required value={v.password} onChange={set("password")} error={errors.password} description="At least 12 characters. Not your name or email, and not a common password." />
          <Button type="submit" loading={busy} mt="xs">Create firm and Admin account</Button>
        </Stack>
      </form>
    </AuthCard>
  );
}
