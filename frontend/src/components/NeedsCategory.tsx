import { useState } from "react";

import { api, useApi } from "../lib/api";
import { dateLabel, money } from "../lib/format";
import type { UncategorisedList, UncategorisedMerchant } from "../types";
import { Card } from "./ui";

/**
 * Uncategorised spending grouped by merchant, biggest first. Picking a
 * category saves a rule on the merchant's name, so its past payments move now
 * and future ones arrive categorised.
 */
export default function NeedsCategory({ onChange }: { onChange: () => void }) {
  const [version, setVersion] = useState(0);
  const list = useApi<UncategorisedList>("/api/categorise/uncategorised?days=180&limit=12", [version]);
  const categories = useApi<{ categories: string[] }>("/api/categories");
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  if (!list.data || list.data.merchants.length === 0) return null;
  const options = (categories.data?.categories ?? []).filter((name) => name !== "Uncategorised");

  const save = async (row: UncategorisedMerchant) => {
    const category = choice[row.merchant_key];
    if (!category) return;
    setBusy(row.merchant_key);
    try {
      const result = await api.post<{ recategorised: number }>("/api/categorise/merchant", {
        merchant: row.merchant,
        merchant_key: row.merchant_key,
        category,
      });
      setMessage(
        `${row.merchant} is now ${category}: ${result.recategorised} transaction${result.recategorised === 1 ? "" : "s"} moved, and future ones will follow.`,
      );
      setVersion((v) => v + 1);
      onChange();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const { total, merchant_count: count } = list.data;
  return (
    <Card
      title="Needs a category"
      hint={`${money(total, true)} across ${count} merchant${count === 1 ? "" : "s"} in the last 6 months. Each choice also applies to future payments.`}
    >
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
            {list.data.merchants.map((row) => (
              <tr key={row.merchant_key}>
                <td>
                  {row.merchant}
                  <div className="muted tiny">
                    {row.example} · {row.account}
                  </div>
                </td>
                <td className="nowrap secondary">{dateLabel(row.last_date, true)}</td>
                <td className="num">{row.transactions}</td>
                <td className="num">{money(row.total)}</td>
                <td>
                  <div className="row" style={{ flexWrap: "nowrap" }}>
                    <select
                      aria-label={`Category for ${row.merchant}`}
                      value={choice[row.merchant_key] ?? ""}
                      onChange={(event) => setChoice({ ...choice, [row.merchant_key]: event.target.value })}
                    >
                      <option value="">Choose…</option>
                      {options.map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                    </select>
                    <button
                      className="btn btn-primary"
                      disabled={!choice[row.merchant_key] || busy !== null}
                      onClick={() => save(row)}
                    >
                      {busy === row.merchant_key ? "Saving…" : "Save"}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
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
