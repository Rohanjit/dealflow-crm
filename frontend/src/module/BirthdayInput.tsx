import { Group, NumberInput, Select, Text } from "@mantine/core";
import { useState } from "react";

import { MONTH_NAMES } from "../format";

const DAYS_IN_MONTH = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

function parse(value: string | null): { day: string | null; month: string | null; year: number | string } {
  if (!value) return { day: null, month: null, year: "" };
  const parts = value.split("-");
  return {
    year: parts.length === 3 ? Number(parts[0]) : "",
    month: String(Number(parts[parts.length - 2])),
    day: String(Number(parts[parts.length - 1])),
  };
}

/** Day + month, year optional. Emits "MM-DD" or "YYYY-MM-DD" (or null until day and month are both set). */
export default function BirthdayInput({
  label, value, onChange, error, dayFirst,
}: {
  label: string; value: string | null; onChange: (v: string | null) => void; error?: string; dayFirst: boolean;
}) {
  const [parts, setParts] = useState(() => parse(value));

  function update(next: Partial<typeof parts>) {
    const p = { ...parts, ...next };
    if (p.month && p.day && Number(p.day) > DAYS_IN_MONTH[Number(p.month) - 1]) p.day = null;
    setParts(p);
    if (p.day && p.month) {
      const md = `${p.month.padStart(2, "0")}-${p.day.padStart(2, "0")}`;
      onChange(p.year ? `${String(p.year).padStart(4, "0")}-${md}` : md);
    } else {
      onChange(null);
    }
  }

  const maxDay = parts.month ? DAYS_IN_MONTH[Number(parts.month) - 1] : 31;
  const day = (
    <Select aria-label={`${label} day`} placeholder="Day" w={84} clearable searchable
      data={Array.from({ length: maxDay }, (_, i) => String(i + 1))}
      value={parts.day} onChange={(d) => update({ day: d })} error={!!error} />
  );
  const month = (
    <Select aria-label={`${label} month`} placeholder="Month" w={140} clearable searchable
      data={MONTH_NAMES.map((m, i) => ({ value: String(i + 1), label: m }))}
      value={parts.month} onChange={(m) => update({ month: m })} error={!!error} />
  );
  return (
    <div>
      <Text size="sm" fw={500} mb={4}>{label}</Text>
      <Group gap={6} wrap="nowrap">
        {dayFirst ? <>{day}{month}</> : <>{month}{day}</>}
        <NumberInput aria-label={`${label} year`} placeholder="Year (optional)" w={140} hideControls allowDecimal={false}
          min={1900} max={new Date().getFullYear()} value={parts.year}
          onChange={(y) => update({ year: y === "" ? "" : Number(y) })} />
      </Group>
      {error && <Text size="xs" c="red" mt={4}>{error}</Text>}
    </div>
  );
}
