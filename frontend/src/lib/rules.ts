import type { Account, MatchType, Rule } from "../types";
import { money } from "./format";

export const MATCH_TYPES: { value: MatchType; label: string }[] = [
  { value: "words", label: "has the words" },
  { value: "contains", label: "contains" },
  { value: "exact", label: "is exactly" },
  { value: "regex", label: "matches the pattern" },
];

/** The kinds of payment the banks tell apart, in the backend's words. */
export const PAYMENT_TYPES: { value: string; label: string }[] = [
  { value: "card", label: "Card" },
  { value: "direct_debit", label: "Direct debit" },
  { value: "standing_order", label: "Standing order" },
  { value: "transfer", label: "Bank transfer" },
  { value: "cash", label: "Cash machine" },
  { value: "interest", label: "Interest" },
  { value: "fee", label: "Bank fee" },
  { value: "internal", label: "Between your own pots" },
  { value: "dividend", label: "Dividend" },
];

const label = (list: { value: string; label: string }[], value: string) =>
  list.find((item) => item.value === value)?.label ?? value;

function amountRange(min: number | null, max: number | null): string | null {
  if (min !== null && max !== null) return `${money(min)} to ${money(max)}`;
  if (min !== null) return `${money(min)} or more`;
  if (max !== null) return `up to ${money(max)}`;
  return null;
}

/** A rule in a sentence each for its conditions and its actions. */
export function describeRule(rule: Rule, accounts: Account[]): { when: string; then: string } {
  const when: string[] = [];
  if (rule.pattern) {
    const subject = rule.field === "merchant" ? "Merchant" : "Description";
    when.push(`${subject} ${label(MATCH_TYPES, rule.match_type)} “${rule.pattern}”`);
  }
  if (rule.direction) when.push(rule.direction === "in" ? "money in" : "money out");
  const range = amountRange(rule.amount_min, rule.amount_max);
  if (range) when.push(range);
  if (rule.account_id !== null) {
    const account = accounts.find((a) => a.id === rule.account_id);
    when.push(`in ${account ? account.name : "an account that's gone"}`);
  }
  if (rule.payment_type) when.push(label(PAYMENT_TYPES, rule.payment_type).toLowerCase());

  const then: string[] = [];
  if (rule.category) then.push(rule.category);
  if (rule.rename_to) then.push(`rename to “${rule.rename_to}”`);
  if (rule.exclude) then.push("leave out of the totals");
  const sentence = (parts: string[]) => {
    const text = parts.join(" · ");
    return text.charAt(0).toUpperCase() + text.slice(1);
  };
  return { when: sentence(when), then: sentence(then) };
}
