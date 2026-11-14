import { useState } from "react";

import { api, useApi } from "../lib/api";
import { dateLabel, money } from "../lib/format";
import type { UncategorisedList, UncategorisedMerchant } from "../types";
import { Card } from "./ui";

/**
 * Uncategorised spending grouped by merchant, biggest first. Picking a
 * category saves a rule on the merchant's name, so its past payments move now
 * and future ones arrive categorised. A category guessed from similar
 * merchants is chosen in advance; merchants filed by that guessing are listed
 * after, to confirm or change.
 */
export default function NeedsCategory({ onChange }: { onChange: () => void }) {
  const [version, setVersion] = useState(0);
  const list = useApi<UncategorisedList>("/api/categorise/uncategorised?days=180&limit=12", [version]);
  const categories = useApi<{ categories: string[] }>("/api/categories");
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  if (!list.data || (list.data.merchants.length === 0 && list.data.learned.length === 0)) return null;
  const options = (categories.data?.categories ?? []).filter((name) => name !== "Uncategorised");

  const chosen = (row: UncategorisedMerchant, learned: boolean) =>
    choice[row.merchant_key] ?? (learned ? row.category : (row.suggestion?.category ?? ""));

  const save = async (row: UncategorisedMerchant, learned: boolean) => {
    const category = chosen(row, learned);
    if (!category) return;
    setBusy(row.merchant_key);
    try {
      const result = await api.post<{ recategorised: number }>("/api/categorise/merchant", {
        merchant: row.merchant,
        merchant_key: row.merchant_key,
        category,
      });
      const moved = result.recategorised;
      setMessage(
        learned && category === row.category
          ? `${row.merchant} stays in ${category}, now as a rule.`
          : `${row.merchant} is now ${category}: ${moved} transaction${moved === 1 ? "" : "s"} moved, and future ones will follow.`,
      );
      setVersion((v) => v + 1);
      onChange();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const row = (item: UncategorisedMerchant, learned: boolean) => {
    const value = chosen(item, learned);
    const suggested = !learned && item.suggestion && choice[item.merchant_key] === undefined;
    return (
      <tr key={`${learned ? "learned" : "new"}-${item.merchant_key}`}>
        <td>
          {item.merchant}
          {learned && (
            <span className="badge" style={{ marginLeft: 8 }} title="Filed like similar merchants you've categorised">
              Learned
            </span>
          )}
          <div className="muted tiny">
            {item.example} · {item.account}
          </div>
        </td>
        <td className="nowrap secondary">{dateLabel(item.last_date, true)}</td>
        <td className="num">{item.transactions}</td>
        <td className="num">{money(item.total)}</td>
        <td>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <select
              aria-label={`Category for ${item.merchant}`}
              value={value}
              onChange={(event) => setChoice({ ...choice, [item.merchant_key]: event.target.value })}
            >
              <option value="">Choose…</option>
              {options.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <button className="btn btn-primary" disabled={!value || busy !== null} onClick={() => save(item, learned)}>
              {busy === item.merchant_key ? "Saving…" : learned && value === item.category ? "Confirm" : "Save"}
            </button>
          </div>
          {suggested && item.suggestion && (
            <div className="muted tiny" style={{ marginTop: 4 }}>
              Suggested
              {item.suggestion.because.length > 0 &&
                `, like other merchants with “${item.suggestion.because.join("”, “")}”`}
            </div>
          )}
        </td>
      </tr>
    );
  };

  const { total, merchant_count: count, learned_from: learnedFrom } = list.data;
  const hint = [
    count > 0
      ? `${money(total, true)} across ${count} merchant${count === 1 ? "" : "s"} in the last 6 months. Each choice also applies to future payments.`
      : "Nothing uncategorised in the last 6 months.",
    learnedFrom > 0 ? `Learning from ${learnedFrom} merchant${learnedFrom === 1 ? "" : "s"} you've categorised.` : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <Card title="Needs a category" hint={hint}>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Merchant</th>
              <th>Last paid</th>
              <th className="num">Payments</th>
              <th className="num">Total</th>
              <th>Category</th>
            </tr>
          </thead>
          <tbody>
            {list.data.merchants.map((item) => row(item, false))}
            {list.data.learned.length > 0 && (
              <tr>
                <td colSpan={5} className="secondary tiny" style={{ paddingTop: 16 }}>
                  Filed like similar merchants you've categorised. Confirm each one, or choose another category.
                </td>
              </tr>
            )}
            {list.data.learned.map((item) => row(item, true))}
          </tbody>
        </table>
      </div>
      {message && (
        <div className="secondary tiny" style={{ marginTop: 10 }} role="status">
          {message}
        </div>
      )}
    </Card>
  );
}
