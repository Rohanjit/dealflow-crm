import { describe, expect, it } from "vitest";

import { dueState } from "./due";

describe("dueState", () => {
  const now = "2026-10-12T15:00";
  it("judges dates and times against the clock", () => {
    expect(dueState("2026-10-11", now)).toBe("overdue");
    expect(dueState("2026-10-12T10:00", now)).toBe("overdue");
    expect(dueState("2026-10-12T18:00", now)).toBe("today");
    expect(dueState("2026-10-12", now)).toBe("today");
    expect(dueState("2026-10-13T09:00", now)).toBe("upcoming");
    expect(dueState(null, now)).toBe("none");
  });
});
