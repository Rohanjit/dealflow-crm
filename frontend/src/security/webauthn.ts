// Passkeys in the browser: turn the server's JSON options into what navigator.credentials wants,
// and the browser's answer back into JSON (base64url everywhere).

/* eslint-disable @typescript-eslint/no-explicit-any */

const toBytes = (s: string) => {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
};
const toB64 = (buf: ArrayBuffer | null) => {
  if (!buf) return null;
  let s = "";
  new Uint8Array(buf).forEach((b) => { s += String.fromCharCode(b); });
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};

export function passkeysSupported(): boolean {
  return typeof window !== "undefined" && !!window.PublicKeyCredential && window.isSecureContext;
}

export async function createPasskey(options: any): Promise<any> {
  const publicKey = {
    ...options,
    challenge: toBytes(options.challenge),
    user: { ...options.user, id: toBytes(options.user.id) },
    excludeCredentials: (options.excludeCredentials ?? []).map((c: any) => ({ ...c, id: toBytes(c.id) })),
  };
  const cred = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential | null;
  if (!cred) throw new Error("No passkey was made.");
  const r = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id, rawId: toB64(cred.rawId), type: cred.type,
    response: {
      clientDataJSON: toB64(r.clientDataJSON), attestationObject: toB64(r.attestationObject),
      transports: typeof r.getTransports === "function" ? r.getTransports() : [],
    },
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
    authenticatorAttachment: (cred as any).authenticatorAttachment ?? null,
  };
}

export async function getPasskey(options: any): Promise<any> {
  const publicKey = {
    ...options,
    challenge: toBytes(options.challenge),
    allowCredentials: (options.allowCredentials ?? []).map((c: any) => ({ ...c, id: toBytes(c.id) })),
  };
  const cred = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential | null;
  if (!cred) throw new Error("No passkey was used.");
  const r = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id, rawId: toB64(cred.rawId), type: cred.type,
    response: {
      clientDataJSON: toB64(r.clientDataJSON), authenticatorData: toB64(r.authenticatorData),
      signature: toB64(r.signature), userHandle: toB64(r.userHandle),
    },
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
  };
}

/** Plain words for the browser's refusals. */
export function passkeyError(e: unknown): string {
  const name = (e as { name?: string })?.name;
  if (name === "NotAllowedError") return "The passkey request was cancelled or timed out. Try again.";
  if (name === "InvalidStateError") return "This device already has a passkey for your account.";
  if (name === "SecurityError") return "Passkeys need Dealflow to be opened over https (or on localhost).";
  return (e as Error)?.message || "The passkey didn't work. Try again.";
}
