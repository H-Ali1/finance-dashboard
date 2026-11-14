import { useEffect, useState } from "react";

import { api } from "../lib/api";
import { dateLabel, money } from "../lib/format";
import { MATCH_TYPES, PAYMENT_TYPES } from "../lib/rules";
import type { Account, MatchType, Rule, RulePreview } from "../types";
import { Card } from "./ui";

interface Form {
  pattern: string;
  match_type: MatchType;
  field: "merchant" | "description";
  direction: "" | "in" | "out";
  amountMin: string;
  amountMax: string;
  accountId: string;
  paymentType: string;
  category: string;
  renameTo: string;
  exclude: boolean;
}

function toForm(rule: Partial<Rule>): Form {
  return {
    pattern: rule.pattern ?? "",
    match_type: rule.match_type ?? "words",
    field: rule.field ?? "merchant",
    direction: rule.direction ?? "",
    amountMin: rule.amount_min != null ? String(rule.amount_min) : "",
    amountMax: rule.amount_max != null ? String(rule.amount_max) : "",
    accountId: rule.account_id != null ? String(rule.account_id) : "",
    paymentType: rule.payment_type ?? "",
    category: rule.category ?? "",
    renameTo: rule.rename_to ?? "",
    exclude: rule.exclude ?? false,
  };
}

function toBody(form: Form) {
  const amount = (text: string) => (text.trim() === "" ? null : Number(text));
  return {
    pattern: form.pattern,
    match_type: form.match_type,
    field: form.field,
    direction: form.direction || null,
    amount_min: amount(form.amountMin),
    amount_max: amount(form.amountMax),
    account_id: form.accountId ? Number(form.accountId) : null,
    payment_type: form.paymentType || null,
    category: form.category || null,
    rename_to: form.renameTo.trim() || null,
    exclude: form.exclude,
  };
}

/**
 * Make or change a rule. Every condition filled in must hold; then every
 * action applies, to past transactions and new ones. A preview shows what it
 * would match and change as you type.
 */
export default function RuleEditor({
  rule,
  categories,
  accounts,
  onClose,
  onSaved,
}: {
  /** The rule to change (with an id), or a starting point for a new one. */
  rule: Partial<Rule>;
  categories: string[];
  accounts: Account[];
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const [form, setForm] = useState<Form>(() => toForm(rule));
  const [preview, setPreview] = useState<{ key: string; result: RulePreview | null; problem: string | null } | null>(
    null,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editing = rule.id !== undefined;

  const set = <K extends keyof Form>(name: K, value: Form[K]) => setForm((current) => ({ ...current, [name]: value }));
  const bodyKey = JSON.stringify(toBody(form));

  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      api
        .post<RulePreview>("/api/rules/preview", { ...JSON.parse(bodyKey), rule_id: rule.id ?? null })
        .then(
          (result) => live && setPreview({ key: bodyKey, result, problem: null }),
          (err) =>
            live && setPreview({ key: bodyKey, result: null, problem: err instanceof Error ? err.message : String(err) }),
        );
    }, 350);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [bodyKey, rule.id]);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const body = { ...toBody(form), apply_now: true };
      const result = editing
        ? await api.put<{ recategorised: number }>(`/api/rules/${rule.id}`, body)
        : await api.post<{ recategorised: number }>("/api/rules", body);
      const moved = result.recategorised;
      onSaved(`Rule saved: ${moved} transaction${moved === 1 ? "" : "s"} changed.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const current = preview?.key === bodyKey ? preview : null;
  const hasAction = Boolean(form.category || form.renameTo.trim() || form.exclude);
  const choices = categories.filter((name) => name !== "Uncategorised");

  return (
    <Card
      title={editing ? "Change rule" : "New rule"}
      hint="Applies to every past and future transaction that meets all the conditions you fill in"
    >
      <form className="stack" onSubmit={save}>
        <div className="form-grid">
          <span className="form-label">When</span>
          <div className="row">
            <select value={form.field} onChange={(e) => set("field", e.target.value as Form["field"])} aria-label="Look at">
              <option value="merchant">Merchant name</option>
              <option value="description">Description</option>
            </select>
            <select
              value={form.match_type}
              onChange={(e) => set("match_type", e.target.value as MatchType)}
              aria-label="How it matches"
            >
              {MATCH_TYPES.map((type) => (
                <option key={type.value} value={type.value}>
                  {type.label}
                </option>
              ))}
            </select>
            <input
              value={form.pattern}
              onChange={(e) => set("pattern", e.target.value)}
              placeholder={form.match_type === "regex" ? "a regular expression" : "text (leave empty for any)"}
              aria-label="Text to match"
              style={{ minWidth: 220, flex: 1 }}
            />
          </div>

          <span className="form-label">And</span>
          <div className="row">
            <select
              value={form.direction}
              onChange={(e) => set("direction", e.target.value as Form["direction"])}
              aria-label="Money in or out"
            >
              <option value="">Money in or out</option>
              <option value="out">Money out</option>
              <option value="in">Money in</option>
            </select>
            <span className="secondary tiny">amount from £</span>
            <input
              type="number"
              min="0"
              step="0.01"
              inputMode="decimal"
              value={form.amountMin}
              onChange={(e) => set("amountMin", e.target.value)}
              aria-label="Smallest amount"
              placeholder="any"
              style={{ width: 90 }}
            />
            <span className="secondary tiny">to £</span>
            <input
              type="number"
              min="0"
              step="0.01"
              inputMode="decimal"
              value={form.amountMax}
              onChange={(e) => set("amountMax", e.target.value)}
              aria-label="Largest amount"
              placeholder="any"
              style={{ width: 90 }}
            />
          </div>

          <span className="form-label" />
          <div className="row">
            <select value={form.accountId} onChange={(e) => set("accountId", e.target.value)} aria-label="Account">
              <option value="">Any account</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
            <select value={form.paymentType} onChange={(e) => set("paymentType", e.target.value)} aria-label="Kind of payment">
              <option value="">Any kind of payment</option>
              {PAYMENT_TYPES.map((type) => (
                <option key={type.value} value={type.value}>
                  {type.label}
                </option>
              ))}
            </select>
          </div>

          <span className="form-label">Then</span>
          <div className="row">
            <select value={form.category} onChange={(e) => set("category", e.target.value)} aria-label="Category">
              <option value="">Leave the category</option>
              {choices.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <input
              value={form.renameTo}
              onChange={(e) => set("renameTo", e.target.value)}
              placeholder="Rename the merchant to…"
              aria-label="New merchant name"
              style={{ minWidth: 200 }}
            />
            <label className="row tiny secondary" style={{ gap: 6 }}>
              <input type="checkbox" checked={form.exclude} onChange={(e) => set("exclude", e.target.checked)} />
              Leave out of the totals
            </label>
          </div>
        </div>

        <PreviewTable preview={current?.result ?? null} problem={current?.problem ?? null} />

        <div className="row">
          <button className="btn btn-primary" disabled={saving || !current?.result || !hasAction}>
            {saving ? "Saving…" : "Save rule"}
          </button>
          {current?.result && !hasAction && (
            <span className="muted tiny">Choose what it does: a category, a new name, or leaving it out.</span>
          )}
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          {error && (
            <span className="secondary" role="alert">
              {error}
            </span>
          )}
        </div>
      </form>
    </Card>
  );
}

function PreviewTable({ preview, problem }: { preview: RulePreview | null; problem: string | null }) {
  if (problem) return <div className="secondary tiny">{problem}</div>;
  if (!preview) return <div className="muted tiny">Checking what it matches…</div>;
  const { matches, changes, examples } = preview;
  if (matches === 0 && changes === 0)
    return <div className="secondary tiny">Matches nothing yet. It will apply to new transactions that do.</div>;
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="secondary tiny" role="status">
        Matches {matches.toLocaleString("en-GB")} transaction{matches === 1 ? "" : "s"}
        {" · "}
        {changes === 0 ? "none would change" : `${changes.toLocaleString("en-GB")} would change`}
        {matches + changes > examples.length ? `. The latest ${examples.length}:` : ":"}
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Merchant</th>
              <th>Category</th>
              <th className="num">Amount</th>
            </tr>
          </thead>
          <tbody>
            {examples.map((row) => (
              <tr key={row.id}>
                <td className="nowrap secondary">{dateLabel(row.date, true)}</td>
                <td>
                  <Change before={row.merchant} after={row.new_merchant} />
                  {!row.matched && (
                    <span className="badge" style={{ marginLeft: 8 }} title="The rule matched it before this change">
                      No longer matches
                    </span>
                  )}
                  <div className="muted tiny">
                    {row.description} · {row.account}
                  </div>
                </td>
                <td>
                  <Change before={row.category} after={row.new_category} />
                  {row.new_excluded !== row.excluded && (
                    <div className="muted tiny">{row.new_excluded ? "Left out of the totals" : "Counted again"}</div>
                  )}
                </td>
                <td className={`num ${row.amount >= 0 ? "delta-up" : ""}`}>{money(row.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Change({ before, after }: { before: string; after: string }) {
  if (before === after) return <span>{before}</span>;
  return (
    <span>
      <span className="muted" style={{ textDecoration: "line-through" }}>
        {before}
      </span>{" "}
      → <strong>{after}</strong>
    </span>
  );
}
