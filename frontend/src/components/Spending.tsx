import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api, useApi } from "../lib/api";
import { categoryColor, money, moneyShort, monthLabel, percent } from "../lib/format";
import type { BudgetStatus, CategoryTrend, SpendingBreakdown } from "../types";
import { BarList, Card, ChartTooltip, Empty, ErrorBanner, Loading } from "./ui";

const AXIS = { tick: { fill: "var(--text-muted)", fontSize: 11 }, tickLine: false, axisLine: { stroke: "var(--axis)" } };

function recentMonths(count: number): string[] {
  const out: string[] = [];
  const now = new Date();
  for (let i = 0; i < count; i += 1) {
    const date = new Date(now.getFullYear(), now.getMonth() - i, 1);
    out.push(`${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`);
  }
  return out;
}

export default function Spending() {
  const months = recentMonths(13);
  const [month, setMonth] = useState(months[0]);
  const [reloadKey, setReloadKey] = useState(0);

  const breakdown = useApi<SpendingBreakdown>(`/api/spending/categories?month=${month}`, [month]);
  const merchants = useApi<{ merchants: { merchant: string; category: string; total: number; transactions: number; average: number }[] }>(
    `/api/spending/merchants?month=${month}&limit=15`,
    [month],
  );
  const trend = useApi<CategoryTrend>("/api/spending/by-category-trend?months=6&limit=10");
  const budgets = useApi<BudgetStatus>(`/api/spending/budgets?month=${month}`, [month, reloadKey]);
  const accounts = useApi<{ accounts: { account: string; institution: string; total: number }[] }>(
    `/api/spending/accounts?month=${month}`,
    [month],
  );

  if (breakdown.error) return <ErrorBanner message={breakdown.error} onRetry={breakdown.reload} />;
  if (!breakdown.data) return <Loading />;

  const rows = breakdown.data.categories;
  const chartData = (trend.data?.categories ?? []).slice(0, 6);

  return (
    <div className="stack">
      <div className="row-between">
        <div className="row">
          <label className="muted tiny" htmlFor="month">
            Month
          </label>
          <select id="month" value={month} onChange={(event) => setMonth(event.target.value)}>
            {months.map((key) => (
              <option key={key} value={key}>
                {monthLabel(key, true)}
              </option>
            ))}
          </select>
        </div>
        <div className="secondary">
          <strong>{money(breakdown.data.total_spend)}</strong> spent in {monthLabel(month, true)}
        </div>
      </div>

      <div className="grid split-2-1">
        <Card title="By category" hint="Value shown on every row, so colour is never the only cue">
          {rows.length === 0 ? (
            <Empty>No spending recorded for this month.</Empty>
          ) : (
            <BarList
              rows={rows.map((row) => ({
                label: row.category,
                value: row.total,
                color: categoryColor(row.category),
                sub: `${row.share}% · ${row.transactions} txns`,
              }))}
            />
          )}
        </Card>

        <Card title="By account" hint="Which card or account it came off">
          {!accounts.data || accounts.data.accounts.length === 0 ? (
            <Empty>Nothing to show.</Empty>
          ) : (
            <BarList
              rows={accounts.data.accounts.map((row, index) => ({
                label: row.account,
                value: row.total,
                color: `var(--series-${(index % 8) + 1})`,
                sub: row.institution,
              }))}
            />
          )}
        </Card>
      </div>

      <Card
        title="Category trend"
        hint={
          trend.data?.comparison === "month-to-date"
            ? `This month through day ${trend.data.through_day}, against the same stretch of previous months`
            : "Full months"
        }
      >
        {chartData.length === 0 ? (
          <Empty>Not enough history yet.</Empty>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart
                data={(trend.data?.months ?? []).map((key) => {
                  const point: Record<string, string | number> = { month: key };
                  chartData.forEach((row) => {
                    point[row.category] = row.series.find((s) => s.month === key)?.total ?? 0;
                  });
                  return point;
                })}
                margin={{ top: 6, right: 8, left: 0, bottom: 0 }}
                barGap={2}
              >
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="month" {...AXIS} tickFormatter={(v) => monthLabel(v)} />
                <YAxis {...AXIS} width={58} tickFormatter={moneyShort} />
                <Tooltip
                  content={<ChartTooltip labelFormatter={(v) => monthLabel(v, true)} />}
                  cursor={{ fill: "var(--surface-2)" }}
                />
                {chartData.map((row) => (
                  <Bar
                    key={row.category}
                    dataKey={row.category}
                    name={row.category}
                    fill={categoryColor(row.category)}
                    radius={[4, 4, 0, 0]}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>

            <div className="table-wrap" style={{ marginTop: 14 }}>
              <table>
                <thead>
                  <tr>
                    <th>Category</th>
                    <th className="num">This month</th>
                    <th className="num">Typical</th>
                    <th className="num">Change</th>
                  </tr>
                </thead>
                <tbody>
                  {(trend.data?.categories ?? []).map((row) => (
                    <tr key={row.category}>
                      <td>
                        <span className="swatch" style={{ background: categoryColor(row.category) }} />
                        {row.category}
                      </td>
                      <td className="num">{money(row.current)}</td>
                      <td className="num">{money(row.average)}</td>
                      <td className={`num ${row.change > 0 ? "delta-down" : row.change < 0 ? "delta-up" : ""}`}>
                        {row.change === 0 ? "—" : `${row.change > 0 ? "+" : ""}${money(row.change)}`}
                        {row.change_pct !== null && (
                          <span className="muted tiny"> ({percent(row.change_pct)})</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>

      <BudgetEditor
        status={budgets.data}
        categories={rows.map((row) => row.category)}
        onChange={() => setReloadKey((key) => key + 1)}
      />

      <Card title="Top merchants" hint={`Biggest single destinations in ${monthLabel(month, true)}`}>
        {!merchants.data || merchants.data.merchants.length === 0 ? (
          <Empty>Nothing to show.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Merchant</th>
                  <th>Category</th>
                  <th className="num">Visits</th>
                  <th className="num">Average</th>
                  <th className="num">Total</th>
                </tr>
              </thead>
              <tbody>
                {merchants.data.merchants.map((row) => (
                  <tr key={row.merchant}>
                    <td>{row.merchant}</td>
                    <td className="secondary">
                      <span className="swatch" style={{ background: categoryColor(row.category) }} />
                      {row.category}
                    </td>
                    <td className="num">{row.transactions}</td>
                    <td className="num">{money(row.average)}</td>
                    <td className="num">{money(row.total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function BudgetEditor({
  status,
  categories,
  onChange,
}: {
  status: BudgetStatus | null;
  categories: string[];
  onChange: () => void;
}) {
  const [category, setCategory] = useState("");
  const [limit, setLimit] = useState("");
  const [saving, setSaving] = useState(false);

  const options = Array.from(
    new Set([...categories, ...(status?.unbudgeted ?? []).map((row) => row.category)]),
  ).sort();

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!category || !limit) return;
    setSaving(true);
    try {
      await api.put("/api/budgets", { category, monthly_limit: Number(limit) });
      setCategory("");
      setLimit("");
      onChange();
    } finally {
      setSaving(false);
    }
  };

  const remove = async (name: string) => {
    await api.del(`/api/budgets/${encodeURIComponent(name)}`);
    onChange();
  };

  return (
    <Card
      title="Budgets"
      hint="Projections blend your pace this month with what the category normally costs"
    >
      {status && status.budgets.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Category</th>
                <th className="num">Spent</th>
                <th className="num">Limit</th>
                <th className="num">Projected</th>
                <th className="num">Left</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {status.budgets.map((row) => (
                <tr key={row.category}>
                  <td>
                    <span className="swatch" style={{ background: categoryColor(row.category) }} />
                    {row.category}
                    <div className="bar-track" style={{ marginTop: 6, maxWidth: 220 }}>
                      <div
                        className="bar-fill"
                        style={{
                          width: `${Math.min((row.spent / Math.max(row.limit, 1)) * 100, 100)}%`,
                          background: row.on_track ? "var(--series-3)" : "var(--status-critical)",
                        }}
                      />
                    </div>
                  </td>
                  <td className="num">{money(row.spent)}</td>
                  <td className="num">{money(row.limit)}</td>
                  <td className={`num ${row.on_track ? "" : "delta-down"}`}>{money(row.projected)}</td>
                  <td className={`num ${row.remaining < 0 ? "delta-down" : ""}`}>{money(row.remaining)}</td>
                  <td className="num">
                    <button className="btn btn-ghost" onClick={() => remove(row.category)} title="Remove budget">
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>No budgets yet. Add one below.</Empty>
      )}

      <form className="row" style={{ marginTop: 14 }} onSubmit={save}>
        <select value={category} onChange={(event) => setCategory(event.target.value)}>
          <option value="">Choose a category…</option>
          {options.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <input
          type="number"
          min="0"
          step="5"
          placeholder="Monthly limit (£)"
          value={limit}
          onChange={(event) => setLimit(event.target.value)}
        />
        <button className="btn btn-primary" disabled={saving || !category || !limit}>
          {saving ? "Saving…" : "Set budget"}
        </button>
      </form>

      {status && status.unbudgeted.length > 0 && (
        <div className="muted tiny" style={{ marginTop: 10 }}>
          Unbudgeted this month:{" "}
          {status.unbudgeted
            .slice(0, 6)
            .map((row) => `${row.category} ${money(row.spent, true)}`)
            .join(" · ")}
        </div>
      )}
    </Card>
  );
}
