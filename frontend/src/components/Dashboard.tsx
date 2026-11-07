import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api, useApi } from "../lib/api";
import { categoryColor, dateLabel, money, moneyShort, monthLabel, percent, relativeDays } from "../lib/format";
import type { Dashboard as DashboardData } from "../types";
import { BarList, Card, ChartTooltip, Empty, ErrorBanner, InsightRow, Legend, Loading, Stat } from "./ui";

const AXIS = { tick: { fill: "var(--text-muted)", fontSize: 11 }, tickLine: false, axisLine: { stroke: "var(--axis)" } };

export default function Dashboard({ onNavigate }: { onNavigate: (tab: string) => void }) {
  const { data, error, loading, reload } = useApi<DashboardData>("/api/dashboard");

  if (loading && !data) return <Loading label="Crunching your numbers" />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!data) return null;

  const { headline, networth, networth_history, cashflow, spending, trend, insights, upcoming } = data;

  const dismiss = async (key: string) => {
    await api.post("/api/insights/dismiss", { key });
    void reload();
  };

  // Snapshots are taken per sync, so the window this covers depends on how long
  // you've been running it — label it with the actual first date, not "90 days".
  const netChange =
    networth_history.length >= 2
      ? networth_history[networth_history.length - 1].net - networth_history[0].net
      : null;
  const historySince = networth_history.length ? networth_history[0].date : null;

  const topCategories = spending.categories.slice(0, 7);
  const otherTotal = spending.categories.slice(7).reduce((sum, row) => sum + row.total, 0);
  const barRows = [
    ...topCategories.map((row) => ({
      label: row.category,
      value: row.total,
      color: categoryColor(row.category),
      sub: `${row.share}%`,
    })),
    ...(otherTotal > 0
      ? [{ label: "Other", value: otherTotal, color: "var(--axis)", sub: "grouped" }]
      : []),
  ];

  return (
    <div className="stack">
      <Card className={`headline-${headline.status}`}>
        <div className="row-between">
          <div className="row" style={{ gap: 14 }}>
            <span className={`icon icon-${headline.status === "bad" ? "critical" : headline.status === "watch" ? "warning" : "good"}`}
              style={{ width: 30, height: 30, borderRadius: 9, fontSize: 15 }}>
              {headline.status === "good" ? "✓" : "!"}
            </span>
            <div>
              <div style={{ fontSize: 17, fontWeight: 650 }}>{headline.message}</div>
              <div className="muted tiny">
                {monthLabel(headline.month, true)} · {headline.counts.critical} critical ·{" "}
                {headline.counts.warning} warnings · {headline.counts.info} to review
              </div>
            </div>
          </div>
          <button className="btn" onClick={() => onNavigate("insights")}>
            See everything that needs fixing
          </button>
        </div>
      </Card>

      <div className="grid cols-4">
        <Stat
          label="Net worth"
          value={money(networth.net, true)}
          note={
            netChange === null ? (
              `${money(networth.assets, true)} assets · ${money(networth.liabilities, true)} owed`
            ) : (
              <span className={netChange >= 0 ? "delta-up" : "delta-down"}>
                {netChange >= 0 ? "▲" : "▼"} {money(Math.abs(netChange), true)}
                {historySince ? ` since ${dateLabel(historySince, true)}` : ""}
              </span>
            )
          }
        />
        <Stat
          label="Projected this month"
          value={money(cashflow.projected_net, true)}
          tone={cashflow.projected_net >= 0 ? "up" : "down"}
          note={`${money(cashflow.projected_income, true)} in · ${money(cashflow.projected_spend, true)} out`}
        />
        <Stat
          label="Spent so far"
          value={money(cashflow.spend_so_far, true)}
          note={`day ${cashflow.days_elapsed} of ${cashflow.days_in_month} · ${money(cashflow.committed_upcoming, true)} still committed`}
        />
        <Stat
          label="Cash runway"
          value={cashflow.runway_months === null ? "—" : `${cashflow.runway_months} mo`}
          note={`${money(cashflow.liquid, true)} liquid vs ${money(cashflow.typical_essential, true)}/mo essentials`}
        />
      </div>

      <div className="grid split-2-1">
        <Card
          title="Net worth"
          hint={
            historySince
              ? `${networth_history.length} snapshots since ${dateLabel(historySince, true)}`
              : "One snapshot per sync"
          }
        >
          {networth_history.length < 2 ? (
            <Empty>Net worth history builds up as you sync — check back tomorrow.</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={230}>
              <AreaChart data={networth_history} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="netFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--series-1)" stopOpacity={0.28} />
                    <stop offset="100%" stopColor="var(--series-1)" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="date" {...AXIS} minTickGap={40} tickFormatter={(v) => dateLabel(v)} />
                <YAxis {...AXIS} width={58} tickFormatter={moneyShort} />
                <Tooltip
                  content={<ChartTooltip labelFormatter={(v) => dateLabel(v, true)} />}
                  cursor={{ stroke: "var(--axis)", strokeWidth: 1 }}
                />
                <Area
                  type="monotone"
                  dataKey="net"
                  name="Net worth"
                  stroke="var(--series-1)"
                  strokeWidth={2}
                  fill="url(#netFill)"
                  activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-1)" }}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card title="Where it sits" hint="Assets minus what you owe">
          <BarList
            rows={[
              { label: "Cash", value: networth.liquid, color: "var(--series-1)" },
              { label: "Investments", value: networth.investments, color: "var(--series-3)" },
              { label: "Pension", value: networth.pension, color: "var(--series-7)" },
              { label: "Card debt", value: networth.card_debt, color: "var(--series-8)" },
            ].filter((row) => row.value !== 0)}
          />
          <button className="btn" style={{ marginTop: 12 }} onClick={() => onNavigate("networth")}>
            Full breakdown
          </button>
        </Card>
      </div>

      <div className="grid split-2-1">
        <Card title="Income vs spending" hint="Last 12 months">
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={trend.months} margin={{ top: 6, right: 8, left: 0, bottom: 0 }} barGap={2}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="month" {...AXIS} tickFormatter={(v) => monthLabel(v)} />
              <YAxis {...AXIS} width={58} tickFormatter={moneyShort} />
              <Tooltip
                content={<ChartTooltip labelFormatter={(v) => monthLabel(v, true)} />}
                cursor={{ fill: "var(--surface-2)" }}
              />
              <Bar dataKey="income" name="Income" fill="var(--series-3)" radius={[4, 4, 0, 0]} />
              <Bar dataKey="spend" name="Spending" fill="var(--series-2)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
          <Legend
            items={[
              { label: "Income", color: "var(--series-3)" },
              { label: "Spending", color: "var(--series-2)" },
            ]}
          />
        </Card>

        <Card
          title="This month's spending"
          hint={`${money(spending.total_spend, true)} across ${spending.categories.length} categories`}
        >
          {barRows.length === 0 ? (
            <Empty>Nothing spent yet this month.</Empty>
          ) : (
            <BarList rows={barRows} />
          )}
        </Card>
      </div>

      <Card
        title="What needs fixing"
        hint="Ranked by severity, then by what it costs you"
        actions={
          <button className="btn" onClick={() => onNavigate("insights")}>
            View all
          </button>
        }
      >
        {insights.length === 0 ? (
          <Empty>Nothing flagged. Either everything's in order or there isn't enough history yet.</Empty>
        ) : (
          insights.map((insight) => (
            <InsightRow key={insight.key} insight={insight} onDismiss={dismiss} />
          ))
        )}
      </Card>

      <div className="grid cols-2">
        <Card title="Due in the next 30 days" hint="From detected recurring payments">
          {upcoming.length === 0 ? (
            <Empty>No recurring payments detected yet.</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Payment</th>
                    <th>When</th>
                    <th className="num">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {upcoming.map((item) => (
                    <tr key={`${item.merchant}-${item.next_expected}`}>
                      <td>
                        {item.merchant}
                        <div className="muted tiny">{item.account}</div>
                      </td>
                      <td className="nowrap">
                        {dateLabel(item.next_expected)}
                        <div className="muted tiny">{relativeDays(item.days_until_due)}</div>
                      </td>
                      <td className="num">{money(item.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Top merchants this month" hint="Where the money actually went">
          {data.top_merchants.length === 0 ? (
            <Empty>No spending recorded this month.</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Merchant</th>
                    <th className="num">Visits</th>
                    <th className="num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {data.top_merchants.map((row) => (
                    <tr key={row.merchant}>
                      <td>
                        <span className="swatch" style={{ background: categoryColor(row.category) }} />
                        {row.merchant}
                        <div className="muted tiny" style={{ marginLeft: 18 }}>
                          {row.category} · {money(row.average)} avg
                        </div>
                      </td>
                      <td className="num">{row.transactions}</td>
                      <td className="num">{money(row.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <Card title="Budgets" hint={`${percent(budgetUsage(data))} of your budgeted total used`}>
        {data.budgets.budgets.length === 0 ? (
          <Empty>
            No budgets set. Add one on the Spending tab to turn tracking into control.
          </Empty>
        ) : (
          <BarList
            rows={data.budgets.budgets.map((row) => ({
              label: row.category,
              value: row.spent,
              color: row.on_track ? "var(--series-3)" : "var(--status-critical)",
              sub: `of ${money(row.limit, true)} · projected ${money(row.projected, true)}`,
            }))}
            max={Math.max(...data.budgets.budgets.map((r) => Math.max(r.limit, r.spent)), 1)}
          />
        )}
      </Card>
    </div>
  );
}

function budgetUsage(data: DashboardData): number {
  const limit = data.budgets.budgets.reduce((sum, row) => sum + row.limit, 0);
  const spent = data.budgets.budgets.reduce((sum, row) => sum + row.spent, 0);
  return limit ? (spent / limit) * 100 : 0;
}
