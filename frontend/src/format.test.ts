import { describe, expect, it } from "vitest";

import { formatBirthday, formatDate, formatDateAndTime, formatMoney, groupDigits, whatsappLink } from "./format";

const IN = { date_format: "DD/MM/YYYY", number_grouping: "indian" };
const US = { date_format: "MM/DD/YYYY", number_grouping: "international" };

describe("groupDigits", () => {
  it("groups the Indian way", () => {
    expect(groupDigits("123456789", "indian")).toBe("12,34,56,789");
    expect(groupDigits("1000", "indian")).toBe("1,000");
    expect(groupDigits("999", "indian")).toBe("999");
    expect(groupDigits("-1234567.5", "indian")).toBe("-12,34,567.5");
  });
  it("groups internationally", () => {
    expect(groupDigits("123456789", "international")).toBe("123,456,789");
    expect(groupDigits(1234.25, "international")).toBe("1,234.25");
  });
  it("handles empties", () => {
    expect(groupDigits(null, "indian")).toBe("");
    expect(groupDigits("", "indian")).toBe("");
  });
});

describe("formatDate", () => {
  it("follows the user's preference", () => {
    expect(formatDate("2026-09-27", IN)).toBe("27/09/2026");
    expect(formatDate("2026-09-27", US)).toBe("09/27/2026");
    expect(formatDate("2026-09-27", { ...IN, date_format: "YYYY-MM-DD" })).toBe("2026-09-27");
    expect(formatDate(null, IN)).toBe("");
  });
});

describe("formatMoney", () => {
  it("shows symbol, grouped amount and unit", () => {
    expect(formatMoney({ amount: "50", currency: "INR", unit: "Cr" }, IN)).toBe("₹50 Cr");
    expect(formatMoney({ amount: "1500000", currency: "USD", unit: "" }, US)).toBe("$1,500,000");
    expect(formatMoney({ amount: "1500000", currency: "INR", unit: "" }, IN)).toBe("₹15,00,000");
    expect(formatMoney({ amount: "12.5", currency: "AED", unit: "Mn" }, IN)).toBe("AED 12.5 Mn");
    expect(formatMoney({ amount: null, currency: "", unit: "" }, IN)).toBe("");
  });
});

describe("formatBirthday", () => {
  it("shows day and month, and the year when known", () => {
    expect(formatBirthday("03-12", IN)).toBe("12 Mar");
    expect(formatBirthday("1985-03-12", IN)).toBe("12 Mar 1985");
    expect(formatBirthday("1985-03-12", US)).toBe("Mar 12, 1985");
    expect(formatBirthday("11-02", US)).toBe("Nov 2");
    expect(formatBirthday(null, IN)).toBe("");
  });
});

describe("whatsappLink", () => {
  it("needs a country code", () => {
    expect(whatsappLink("+91 90000 00011")).toBe("https://wa.me/919000000011");
    expect(whatsappLink("90000 00011")).toBeNull();
    expect(whatsappLink("+91 22")).toBeNull();
  });
});

describe("formatDateAndTime", () => {
  it("adds the time when there is one", () => {
    const p = { date_format: "DD/MM/YYYY", number_grouping: "indian" };
    expect(formatDateAndTime("2026-10-12T14:30", p)).toBe("12/10/2026 14:30");
    expect(formatDateAndTime("2026-10-12", p)).toBe("12/10/2026");
    expect(formatDateAndTime(null, p)).toBe("");
  });
});
