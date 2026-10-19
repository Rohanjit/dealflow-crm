import { describe, expect, it } from "vitest";

import { direction } from "./navigation";

describe("page transition direction", () => {
  it("dissolves between the sidebar's own pages", () => {
    expect(direction("/", "/m/deals", "PUSH", 1)).toBe("same");
    expect(direction("/m/deals", "/m/investors", "PUSH", 1)).toBe("same");
    expect(direction("/settings/firm", "/settings/users", "POP", -1)).toBe("same");
  });
  it("slides forward into a record and back out of it", () => {
    expect(direction("/m/deals", "/m/deals/5", "PUSH", 1)).toBe("forward");
    expect(direction("/m/deals/5", "/m/deals/5/edit", "PUSH", 1)).toBe("forward");
    expect(direction("/m/deals/5/edit", "/m/deals/5", "PUSH", 1)).toBe("back");
    expect(direction("/m/deals/5", "/m/deals", "POP", -1)).toBe("back");
  });
  it("follows the browser's buttons between records", () => {
    expect(direction("/m/deals/5", "/m/companies/3", "PUSH", 1)).toBe("forward");
    expect(direction("/m/companies/3", "/m/deals/5", "POP", -1)).toBe("back");
    expect(direction("/m/deals/5", "/m/companies/3", "POP", 1)).toBe("forward");
  });
});
