// Display formatting. The API always sends ISO dates and plain numbers; each user's
// preferences (date format, Indian vs international grouping) are applied only here.

export type Prefs = { date_format: string; number_grouping: string };

export const CURRENCY_SYMBOL: Record<string, string> = {
  INR: "₹", USD: "$", EUR: "€", GBP: "£", SGD: "S$", AED: "AED", JPY: "¥", HKD: "HK$", AUD: "A$",
  CAD: "C$", CHF: "CHF", CNY: "CN¥",
};

/** dayjs-style format string for the user's date preference (used by date pickers). */
export function dayjsFormat(p: Prefs): string {
  return p.date_format; // DD/MM/YYYY, MM/DD/YYYY and YYYY-MM-DD are valid dayjs formats as-is
}

export function formatDate(iso: string | null | undefined, p: Prefs): string {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  if (!y || !m || !d) return iso;
  if (p.date_format === "MM/DD/YYYY") return `${m}/${d}/${y}`;
  if (p.date_format === "YYYY-MM-DD") return `${y}-${m}-${d}`;
  return `${d}/${m}/${y}`;
}

export function formatDateTime(iso: string | null | undefined, p: Prefs): string {
  if (!iso) return "";
  const dt = new Date(iso);
  if (isNaN(dt.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  const local = `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;
  return `${formatDate(local, p)} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
}

/** Group digits: Indian 12,34,56,789 or international 123,456,789. Keeps decimals as given. */
export function groupDigits(value: string | number | null | undefined, grouping: string): string {
  if (value === null || value === undefined || value === "") return "";
  const s = String(value);
  const neg = s.startsWith("-");
  const [intRaw, frac] = (neg ? s.slice(1) : s).split(".");
  const int = intRaw.replace(/^0+(?=\d)/, "");
  let grouped: string;
  if (grouping === "indian" && int.length > 3) {
    const last3 = int.slice(-3);
    const rest = int.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
    grouped = `${rest},${last3}`;
  } else {
    grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }
  return `${neg ? "-" : ""}${grouped}${frac ? "." + frac : ""}`;
}

export function formatMoney(
  m: { amount: string | null; currency: string; unit: string } | null | undefined,
  p: Prefs,
): string {
  if (!m || m.amount === null || m.amount === undefined || m.amount === "") return "";
  const sym = CURRENCY_SYMBOL[m.currency] ?? m.currency;
  const amt = groupDigits(m.amount, p.number_grouping);
  const sep = /^[A-Z]{2,}$/.test(sym) ? " " : "";
  return `${sym}${sep}${amt}${m.unit ? " " + m.unit : ""}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
  "December",
];

/** Birthday "MM-DD" or "YYYY-MM-DD" → "12 Mar 1985" (or "Mar 12, 1985" for MM/DD users). */
export function formatBirthday(value: string | null | undefined, p: Prefs): string {
  if (!value) return "";
  const parts = value.split("-");
  const year = parts.length === 3 ? parts[0] : "";
  const month = Number(parts[parts.length - 2]);
  const day = Number(parts[parts.length - 1]);
  if (!month || !day || month > 12) return value;
  const mon = MONTHS[month - 1];
  if (p.date_format === "MM/DD/YYYY") return year ? `${mon} ${day}, ${year}` : `${mon} ${day}`;
  return year ? `${day} ${mon} ${year}` : `${day} ${mon}`;
}

/** wa.me link for numbers written with a country code (+91 98200 12345). */
export function whatsappLink(phone: string): string | null {
  const t = phone.trim();
  const digits = t.replace(/\D/g, "");
  if (!t.startsWith("+") || digits.length < 10) return null;
  return `https://wa.me/${digits}`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
