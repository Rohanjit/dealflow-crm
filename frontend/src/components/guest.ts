import type { QueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { resetSession } from "../context";

/** Sign in as the sample firm's guest (loading the sample the first time). */
export async function startGuest(qc: QueryClient) {
  await api("guest/start/", { body: {} });
  await resetSession(qc);
}

/** Remove the sample and go back to the empty install's set-up screen. */
export async function leaveSample(qc: QueryClient) {
  await api("guest/leave/", { body: {} });
  await resetSession(qc);
}
