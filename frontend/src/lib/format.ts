const GBP = new Intl.NumberFormat("en-GB", {
  style: "currency",
  currency: "GBP",
  maximumFractionDigits: 2,
});

const GBP_WHOLE = new Intl.NumberFormat("en-GB", {
  style: "currency",
  currency: "GBP",
  maximumFractionDigits: 0,
});

export function money(value: number | null | undefined, whole = false): string {
  if (value === null || value === undefined) return "—";
  return (whole ? GBP_WHOLE : GBP).format(value);
}

/** Compact form for axis ticks: £1.2k, £14k, £1.1m */
export function moneyShort(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1_000_000) return `${sign}£${(abs / 1_000_000).toFixed(1)}m`;
  if (abs >= 1_000) return `${sign}£${(abs / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}k`;
  return `${sign}£${Math.round(abs)}`;
}

export function percent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(digits)}%`;
}

export function monthLabel(key: string, long = false): string {
  const [year, month] = key.split("-").map(Number);
  const date = new Date(year, month - 1, 1);
  return date.toLocaleDateString("en-GB", {
    month: "short",
    year: long ? "numeric" : "2-digit",
  });
}

export function dateLabel(iso: string, withYear = false): string {
  const date = new Date(iso);
  return date.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    ...(withYear ? { year: "numeric" } : {}),
  });
}

export function relativeDays(days: number): string {
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days < 0) return `${Math.abs(days)}d overdue`;
  return `in ${days}d`;
}

export function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** Categorical slots, assigned in fixed order and never cycled. */
export const SERIES = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
  "var(--series-7)",
  "var(--series-8)",
];

/** Stable colour per category name, so a filter never repaints the survivors. */
const CATEGORY_ORDER = [
  "Rent & Mortgage",
  "Groceries",
  "Bills & Utilities",
  "Eating Out",
  "Shopping",
  "Transport",
  "Fuel",
  "Subscriptions",
  "Travel",
  "Health & Fitness",
  "Entertainment",
  "Insurance",
  "Holidays",
  "Cash",
  "Fees & Interest",
  "Education",
  "Gifts & Charity",
  "Savings & Investments",
  "Income",
  "Transfers",
  "Uncategorised",
];

export function categoryColor(category: string): string {
  const index = CATEGORY_ORDER.indexOf(category);
  return SERIES[(index < 0 ? CATEGORY_ORDER.length : index) % SERIES.length];
}
