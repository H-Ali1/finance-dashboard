import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { api, useApi } from "../lib/api";
import { dateLabel, money, moneyShort, monthLabel, percent } from "../lib/format";
import type { Account, IncomeBreakdown, IncomePayer, IncomeSource } from "../types";
import { Card, ChartTooltip, Empty, ErrorBanner, Legend, Loading, Stat } from "./ui";

const AXIS = { tick: { fill: "var(--text-muted)", fontSize: 11 }, tickLine: false, axisLine: { stroke: "var(--axis)" } };
const COLOURS: Record<string, string> = {
  Salary: "var(--series-1)",
  Tutoring: "var(--series-3)",
  Income: "var(--series-5)",
};
// What a payer can be set to: a source of income, or not income at all.
const CHOICES = [
  { value: "Salary", label: "Salary" },
  { value: "Tutoring", label: "Tutoring" },
  { value: "Income", label: "Other income" },
  { value: "Transfers", label: "My own money" },
];

function trendText(source: IncomeSource): string {
  const { change_pct: pct, direction } = source.trend;
  if (pct === null || direction === null) return "Not enough history for a trend";
  if (direction === "steady") return "Steady over the last 3 months";
  return `${direction === "up" ? "Up" : "Down"} ${percent(Math.abs(pct))} on the 3 months before`;
}

function detail(source: IncomeSource): string {
  if (source.months_paid === 0) return source.regularity;
  if (source.source === "Salary") {
    return `${source.regularity}${source.payday ? `, around the ${source.payday}${ordinal(source.payday)}` : ""}`;
  }
  if (source.source === "Tutoring") {
    return `${source.regularity} · ${source.payers_per_month} payer${source.payers_per_month === 1 ? "" : "s"} and ${source.payments_per_month} payment${source.payments_per_month === 1 ? "" : "s"} in a typical month`;
  }
  return source.regularity;
}

function ordinal(day: number): string {
  if (day % 100 >= 11 && day % 100 <= 13) return "th";
  return ["th", "st", "nd", "rd"][day % 10] ?? "th";
}

export default function Income() {
  const [version, setVersion] = useState(0);
  const { data, error, loading, reload } = useApi<IncomeBreakdown>("/api/income?months=12", [version]);
  const accounts = useApi<{ accounts: Account[] }>("/api/accounts", [version]);
  const changed = () => setVersion((v) => v + 1);

  if (loading && !data) return <Loading />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!data) return null;

  const hasIncome = data.sources.some((s) => s.total > 0);

  return (
    <div className="stack">
      <div className="grid cols-4">
        {data.sources.map((source) => (
          <Stat
            key={source.source}
            label={`${source.label} · typical month`}
            value={money(source.typical, true)}
            tone={source.trend.direction === "up" ? "up" : source.trend.direction === "down" ? "down" : undefined}
            note={`${money(source.this_month, true)} so far this month. ${trendText(source)}.`}
          />
        ))}
        <Stat
          label="All income · typical month"
          value={money(data.total.typical, true)}
          note={`${money(data.total.this_month, true)} so far this month`}
        />
      </div>

      <Card
        title="Income by month"
        hint="Refunds and money from your own accounts aren't income, so they're left out"
        actions={
          <Legend items={data.sources.map((s) => ({ label: s.label, color: COLOURS[s.source] ?? "var(--series-8)" }))} />
        }
      >
        {!hasIncome ? (
          <Empty>No income recorded in the last 12 months.</Empty>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={data.series} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="month" {...AXIS} tickFormatter={(v) => monthLabel(v)} />
              <YAxis {...AXIS} width={58} tickFormatter={moneyShort} />
              <Tooltip
                content={<ChartTooltip labelFormatter={(v) => monthLabel(v, true)} />}
                cursor={{ fill: "var(--surface-2)" }}
              />
              {data.sources.map((source, index) => (
                <Bar
                  key={source.source}
                  dataKey={source.source}
                  name={source.label}
                  stackId="income"
                  fill={COLOURS[source.source] ?? "var(--series-8)"}
                  radius={index === data.sources.length - 1 ? [4, 4, 0, 0] : undefined}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        )}
        <div className="muted tiny" style={{ marginTop: 8 }}>
          This month: {money(data.not_income.refunds_this_month)} of refunds (taken off spending instead) and{" "}
          {money(data.not_income.own_money_this_month)} from your own accounts.
        </div>
      </Card>

      <Card title="Each source" hint="Typical is the median of complete months, so one big month doesn't set it">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>How regular</th>
                <th className="num">This month</th>
                <th className="num">Typical month</th>
                <th className="num">Last 3 months</th>
                <th className="num">3 months before</th>
                <th className="num">Varies by</th>
              </tr>
            </thead>
            <tbody>
              {data.sources.map((source) => (
                <tr key={source.source}>
                  <td>
                    <span className="swatch" style={{ background: COLOURS[source.source] }} />
                    {source.label}
                  </td>
                  <td className="secondary">{detail(source)}</td>
                  <td className="num">{money(source.this_month)}</td>
                  <td className="num">{money(source.typical)}</td>
                  <td className="num">{money(source.trend.recent)}/mo</td>
                  <td className="num">{money(source.trend.previous)}/mo</td>
                  <td className="num">{source.variation_pct === null ? "—" : percent(source.variation_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <TutoringAccount accounts={accounts.data?.accounts ?? []} onChange={changed} />

      <Payers payers={data.payers} onChange={changed} />
    </div>
  );
}

function TutoringAccount({ accounts, onChange }: { accounts: Account[]; onChange: () => void }) {
  const current = accounts.filter((a) => a.kind === "current");
  const marked = current.find((a) => a.income_default === "Tutoring");
  const [choice, setChoice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const value = choice ?? (marked ? String(marked.id) : "");

  const save = async () => {
    setSaving(true);
    try {
      if (marked && String(marked.id) !== value) {
        await api.patch(`/api/accounts/${marked.id}`, { income_default: "" });
      }
      if (value) await api.patch(`/api/accounts/${value}`, { income_default: "Tutoring" });
      setChoice(null);
      onChange();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title="Where students pay you"
      hint="Bank transfers from people into this account count as tutoring, unless you say otherwise below"
    >
      <div className="row">
        <select value={value} onChange={(event) => setChoice(event.target.value)} aria-label="Tutoring account">
          <option value="">No account: only payments that mention lessons or tutoring</option>
          {current.map((account) => (
            <option key={account.id} value={account.id}>
              {account.name} ({account.institution})
            </option>
          ))}
        </select>
        <button className="btn btn-primary" disabled={saving || choice === null} onClick={save}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </Card>
  );
}

function Payers({ payers, onChange }: { payers: IncomePayer[]; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const assign = async (payer: IncomePayer, category: string) => {
    setBusy(payer.merchant_key);
    try {
      const result = await api.post<{ recategorised: number }>("/api/categorise/merchant", {
        merchant: payer.merchant_key,
        category,
      });
      const label = CHOICES.find((c) => c.value === category)?.label ?? category;
      setMessage(`${payer.payer} is now ${label}: ${result.recategorised} payment(s) moved, and future ones will follow.`);
      onChange();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title="Who pays you" hint="Last 12 months. Changing one applies to their past and future payments">
      {payers.length === 0 ? (
        <Empty>No income in the last 12 months.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Payer</th>
                <th>Last paid</th>
                <th className="num">Months</th>
                <th className="num">Payments</th>
                <th className="num">Total</th>
                <th>Counts as</th>
              </tr>
            </thead>
            <tbody>
              {payers.map((payer) => (
                <tr key={payer.merchant_key}>
                  <td>
                    {payer.payer}
                    <div className="muted tiny">{payer.accounts.join(", ")}</div>
                  </td>
                  <td className="nowrap secondary">
                    {dateLabel(payer.last_date, true)} · {money(payer.last_amount)}
                  </td>
                  <td className="num">{payer.months}</td>
                  <td className="num">{payer.payments}</td>
                  <td className="num">{money(payer.total)}</td>
                  <td>
                    <select
                      aria-label={`What ${payer.payer} pays you counts as`}
                      value={payer.source}
                      disabled={busy !== null}
                      title={payer.source_set_by === "auto" ? "Worked out automatically" : "Set by you"}
                      onChange={(event) => assign(payer, event.target.value)}
                    >
                      {CHOICES.map((choice) => (
                        <option key={choice.value} value={choice.value}>
                          {choice.label}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {message && (
        <div className="secondary tiny" style={{ marginTop: 10 }} role="status">
          {message}
        </div>
      )}
    </Card>
  );
}
