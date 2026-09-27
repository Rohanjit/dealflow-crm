import { notifications } from "@mantine/notifications";

import { ApiError } from "../api";

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong. Try again.";
}

export function fieldErrors(e: unknown): Record<string, string> {
  return e instanceof ApiError ? e.fields : {};
}

export function notifyError(e: unknown, title = "Couldn't save") {
  notifications.show({ color: "red", title, message: errorMessage(e) });
}

export function notifyOk(message: string) {
  notifications.show({ color: "teal", message });
}
