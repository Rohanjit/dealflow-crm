// When a task falls due, judged against the firm's clock (sent by the server as "YYYY-MM-DDTHH:MM"
// so every screen groups tasks the same way).

export type DueState = "overdue" | "today" | "upcoming" | "none";

export function localNow(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function dueState(due: string | null | undefined, now: string): DueState {
  if (!due) return "none";
  const [day, time] = due.split("T");
  const [today, clock] = now.split("T");
  if (day < today) return "overdue";
  if (day > today) return "upcoming";
  return time && clock && time < clock ? "overdue" : "today";
}
