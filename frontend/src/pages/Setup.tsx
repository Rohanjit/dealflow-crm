import { Alert, Button, Group, PasswordInput, SegmentedControl, Stack, Text, TextInput, UnstyledButton } from "@mantine/core";
import { IconArrowRight, IconCompass, IconRocket } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import AuthCard from "../components/AuthCard";
import { errorMessage, fieldErrors } from "../components/errors";
import { startGuest } from "../components/guest";
import type { AppInfo } from "../types";

/** A fresh install: look around with sample data first, or set it up straight away. */
export default function Setup({ info }: { info: AppInfo }) {
  const [step, setStep] = useState<"choose" | "form">(info.guest_available ? "choose" : "form");
  return step === "choose" ? <Welcome info={info} onSetup={() => setStep("form")} /> : <SetupForm onBack={info.guest_available && !info.sample ? () => setStep("choose") : undefined} />;
}

function Welcome({ info, onSetup }: { info: AppInfo; onSetup: () => void }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function lookAround() {
    setBusy(true);
    setError("");
    try {
      await startGuest(qc);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Welcome to Dealflow" subtitle={info.sample
      ? "Sample data is loaded for looking around. Carry on exploring, or remove it and set Dealflow up for real."
      : "Look around with a sample firm first, or set Dealflow up for your own work now."}>
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Choice icon={<IconCompass size={22} stroke={1.6} />} title={info.sample ? "Continue looking around" : "Look around first"}
          text="No sign-in needed. A sample firm with investors, outreach, deals, tasks and documents to try everything on."
          onClick={lookAround} loading={busy} />
        {!info.sample && (
          <Choice icon={<IconRocket size={22} stroke={1.6} />} title="Set up Dealflow"
            text="Create your admin account and start with an empty workspace, for your firm or just you." onClick={onSetup} />
        )}
        {info.sample && (
          <Text size="xs" c="dimmed">To set up for real, continue looking around and use “Set up for real” at the top of any page.</Text>
        )}
      </Stack>
    </AuthCard>
  );
}

function Choice({ icon, title, text, onClick, loading }: { icon: React.ReactNode; title: string; text: string; onClick: () => void; loading?: boolean }) {
  return (
    <UnstyledButton onClick={onClick} disabled={loading} className="df-choice" aria-busy={loading}>
      <div className="df-choice-icon">{icon}</div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Text fw={600}>{loading ? "Loading the sample…" : title}</Text>
        <Text size="sm" c="dimmed">{text}</Text>
      </div>
      <IconArrowRight size={16} className="df-choice-arrow" />
    </UnstyledButton>
  );
}

function SetupForm({ onBack }: { onBack?: () => void }) {
  const qc = useQueryClient();
  const [kind, setKind] = useState<"firm" | "individual">("firm");
  const [v, setV] = useState({ firm_name: "", full_name: "", email: "", password: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement>) => setV({ ...v, [k]: e.currentTarget.value });
  const solo = kind === "individual";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setErrors({});
    try {
      await api("setup/", { body: { ...v, workspace_kind: kind } });
      await qc.invalidateQueries({ queryKey: ["app"] });
    } catch (err) {
      setErrors(fieldErrors(err));
      if (!Object.keys(fieldErrors(err)).length) setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Set up Dealflow"
      subtitle={solo ? "Create your account. Everything stays on this computer or server." : "Name your firm and create the first Admin account. You can add your team next."}>
      <form onSubmit={submit}>
        <Stack>
          {error && <Alert color="red">{error}</Alert>}
          <SegmentedControl fullWidth size="sm" value={kind} onChange={(k) => setKind(k as "firm" | "individual")}
            data={[{ value: "firm", label: "For my firm" }, { value: "individual", label: "Just me" }]} />
          {solo ? (
            <TextInput label="Workspace name (optional)" placeholder="e.g. My deals" value={v.firm_name} onChange={set("firm_name")} error={errors.firm_name} />
          ) : (
            <TextInput label="Firm name" required autoFocus value={v.firm_name} onChange={set("firm_name")} error={errors.firm_name} />
          )}
          <TextInput label="Your name" required value={v.full_name} onChange={set("full_name")} error={errors.full_name} />
          <TextInput label={solo ? "Email" : "Work email"} type="email" required value={v.email} onChange={set("email")} error={errors.email} description="You'll sign in with this." />
          <PasswordInput label="Password" required value={v.password} onChange={set("password")} error={errors.password} description="At least 8 characters, not all numbers." />
          <Button type="submit" loading={busy} mt="xs">{solo ? "Create my account" : "Create firm and Admin account"}</Button>
          {onBack && <Group justify="center"><Button variant="subtle" color="gray" size="xs" onClick={onBack}>Back</Button></Group>}
        </Stack>
      </form>
    </AuthCard>
  );
}
