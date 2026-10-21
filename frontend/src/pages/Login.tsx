import { Alert, Anchor, Button, Divider, PasswordInput, PinInput, Stack, Text, TextInput } from "@mantine/core";
import { IconCompass, IconFingerprint } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { resetSession, takeSignedOutReason } from "../context";
import AuthCard from "../components/AuthCard";
import { errorMessage } from "../components/errors";
import { startGuest } from "../components/guest";
import { passkeyError, passkeysSupported, getPasskey } from "../security/webauthn";

type Step = { methods: string[] };

export default function Login({ firmName, guest = false }: { firmName: string; guest?: boolean }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice] = useState(takeSignedOutReason);
  const [busy, setBusy] = useState(false);
  const [guestBusy, setGuestBusy] = useState(false);

  const [step, setStep] = useState<Step | null>(null);
  const [way, setWay] = useState<"app" | "recovery">("app");
  const [code, setCode] = useState("");
  const [passkeyBusy, setPasskeyBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api<{ mfa_required?: boolean; methods?: string[] }>("auth/login/", { body: { email, password } });
      if (r.mfa_required) {
        setPassword("");
        setStep({ methods: r.methods ?? [] });
        setWay(r.methods?.includes("app") ? "app" : "recovery");
        // A passkey is the quickest second step: offer it straight away.
        if (r.methods?.includes("passkey") && passkeysSupported()) void passkey();
      } else {
        await resetSession(qc);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function passkey() {
    setPasskeyBusy(true);
    setError("");
    try {
      const options = await api("auth/passkey/options/", { body: {} });
      let credential;
      try { credential = await getPasskey(options); } catch (e) { throw new Error(passkeyError(e)); }
      await api("auth/passkey/sign-in/", { body: { credential } });
      await resetSession(qc);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPasskeyBusy(false);
    }
  }

  async function verify(value = code) {
    setBusy(true);
    setError("");
    try {
      await api("auth/mfa/verify/", { body: { method: way, code: value } });
      await resetSession(qc);
    } catch (err) {
      setError(errorMessage(err));
      setCode("");
      if ((err as { code?: string }).code === "mfa_expired") setStep(null);
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

  if (step) {
    return (
      <AuthCard title="Second step" subtitle="Your password was right. Now prove it's you with your phone or passkey.">
        <Stack>
          {error && <Alert color="red">{error}</Alert>}
          {step.methods.includes("passkey") && (
            <Button size="md" leftSection={<IconFingerprint size={18} />} loading={passkeyBusy} onClick={passkey} disabled={!passkeysSupported()}>
              Use your passkey
            </Button>
          )}
          {step.methods.includes("passkey") && (step.methods.includes("app") || step.methods.includes("recovery")) && <Divider label="or" />}
          {way === "app" && step.methods.includes("app") ? (
            <form onSubmit={(e) => { e.preventDefault(); verify(); }}>
              <Stack gap="xs">
                <Text size="sm" fw={500}>Code from your authenticator app</Text>
                <PinInput length={6} type="number" oneTimeCode value={code} onChange={setCode} onComplete={(v) => verify(v)}
                  aria-label="6-digit code" autoFocus={!step.methods.includes("passkey")} />
                <Button type="submit" variant={step.methods.includes("passkey") ? "default" : "filled"} loading={busy} disabled={code.length < 6}>Continue</Button>
              </Stack>
            </form>
          ) : (
            <form onSubmit={(e) => { e.preventDefault(); verify(); }}>
              <Stack gap="xs">
                <TextInput label="Recovery code" placeholder="xxxxx-xxxxx" value={code} onChange={(e) => setCode(e.currentTarget.value)} autoComplete="off" />
                <Button type="submit" variant="default" loading={busy} disabled={code.replace(/[^a-z0-9]/gi, "").length < 10}>Continue</Button>
              </Stack>
            </form>
          )}
          {step.methods.includes("recovery") && step.methods.includes("app") && (
            <Anchor component="button" size="sm" onClick={() => { setWay(way === "app" ? "recovery" : "app"); setCode(""); }}>
              {way === "app" ? "Lost your phone? Use a recovery code" : "Use your authenticator app instead"}
            </Anchor>
          )}
          <Anchor component="button" size="sm" c="dimmed" onClick={() => { setStep(null); setError(""); setCode(""); }}>Start again</Anchor>
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
          {passkeysSupported() && (
            <>
              <Divider label="or" />
              <Button variant="default" leftSection={<IconFingerprint size={18} />} loading={passkeyBusy} onClick={passkey}>
                Sign in with a passkey
              </Button>
            </>
          )}
          <Text size="xs" c="dimmed">Forgot your password? Ask your Dealflow admin to reset it.</Text>
        </Stack>
      </form>
    </AuthCard>
  );
}
