import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useApi } from "../lib/api";
import { dateLabel, money, moneyShort, monthLabel, percent } from "../lib/format";
import type { Cashflow as CashflowData } from "../types";
import { Card, ChartTooltip, Empty, ErrorBanner, Legend, Loading, Stat } from "./ui";

const AXIS = { tick: { fill: "var(--text-muted)", fontSize: 11 }, tickLine: false, axisLine: { stroke: "var(--axis)" } };

export default function Cashflow() {
  const { data, error, loading, reload } = useApi<CashflowData>("/api/cashflow?months=12");

  if (loading && !data) return <Loading />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!data) return null;

  const progress = Math.round((data.days_elapsed / data.days_in_month) * 100);

  return (
    <div className="stack">
      <div className="grid cols-4">
        <Stat
          label="Projected month end"
          value={money(data.projected_net, true)}
          tone={data.projected_net >= 0 ? "up" : "down"}
          note={`${money(data.projected_income, true)} in − ${money(data.projected_spend, true)} out`}
        />
        <Stat
          label="Savings rate"
          value={percent(data.savings_rate)}
          tone={(data.savings_rate ?? 0) >= 20 ? "up" : (data.savings_rate ?? 0) < 10 ? "down" : undefined}
          note={`Typical month: ${money(data.typical_income, true)} in, ${money(data.typical_spend, true)} out`}
        />
        <Stat
          label="Cash runway"
          value={data.runway_months === null ? "—" : `${data.runway_months} months`}
          note={`${money(data.liquid, true)} liquid ÷ ${money(data.typical_essential, true)} essentials/mo`}
        />
        <Stat
          label="Still committed"
          value={money(data.committed_upcoming, true)}
          note={`${data.upcoming.length} known payments before month end`}
        />
      </div>

      <Card
        title="This month so far"
        hint={`Day ${data.days_elapsed} of ${data.days_in_month} (${progress}% through)`}
      >
        <div className="grid cols-4" style={{ gap: 12 }}>
          <div>
            <div className="muted tiny">Income</div>
            <div style={{ fontSize: 19, fontWeight: 650 }}>{money(data.income_so_far, true)}</div>
          </div>
          <div>
            <div className="muted tiny">Fixed commitments paid</div>
            <div style={{ fontSize: 19, fontWeight: 650 }}>{money(data.fixed_so_far, true)}</div>
          </div>
          <div>
            <div className="muted tiny">Everyday spending</div>
            <div style={{ fontSize: 19, fontWeight: 650 }}>{money(data.variable_so_far, true)}</div>
            <div className="muted tiny">projected {money(data.projected_variable, true)}</div>
          </div>
          <div>
            <div className="muted tiny">Net so far</div>
            <div
              className={data.net_so_far >= 0 ? "delta-up" : "delta-down"}
              style={{ fontSize: 19, fontWeight: 650 }}
            >
              {money(data.net_so_far, true)}
            </div>
          </div>
        </div>
        <div className="bar-track" style={{ marginTop: 14, height: 8 }}>
          <div className="bar-fill" style={{ width: `${progress}%`, background: "var(--series-1)" }} />
        </div>
        <div className="muted tiny" style={{ marginTop: 6 }}>
          Projection blends your actual pace with what a typical month costs, and adds the recurring
          payments still due — it doesn't just multiply today's spending by the days left.
        </div>
      </Card>

      <Card title="Income vs spending" hint={`${data.months_of_history} complete months`}>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={data.history} margin={{ top: 6, right: 8, left: 0, bottom: 0 }} barGap={2}>
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

      <div className="grid cols-2">
        <Card title="Fixed vs everyday spending" hint="Fixed = detected recurring commitments">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={data.history} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="month" {...AXIS} tickFormatter={(v) => monthLabel(v)} />
              <YAxis {...AXIS} width={58} tickFormatter={moneyShort} />
              <Tooltip
                content={<ChartTooltip labelFormatter={(v) => monthLabel(v, true)} />}
                cursor={{ fill: "var(--surface-2)" }}
              />
              {/* 2px surface gap between stacked segments, per the mark spec. */}
              <Bar
                dataKey="fixed"
                name="Fixed"
                stackId="spend"
                fill="var(--series-1)"
                stroke="var(--surface-1)"
                strokeWidth={2}
              />
              <Bar
                dataKey="variable"
                name="Everyday"
                stackId="spend"
                fill="var(--series-4)"
                stroke="var(--surface-1)"
                strokeWidth={2}
                radius={[4, 4, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
          <Legend
            items={[
              { label: "Fixed commitments", color: "var(--series-1)" },
              { label: "Everyday spending", color: "var(--series-4)" },
            ]}
          />
        </Card>

        <Card title="Spending pace" hint="Cumulative spend over the last 60 days">
          {!data.burn || data.burn.length === 0 ? (
            <Empty>No recent transactions.</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={data.burn} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="date" {...AXIS} minTickGap={40} tickFormatter={(v) => dateLabel(v)} />
                <YAxis {...AXIS} width={58} tickFormatter={moneyShort} />
                <Tooltip
                  content={<ChartTooltip labelFormatter={(v) => dateLabel(v, true)} />}
                  cursor={{ stroke: "var(--axis)" }}
                />
                <Line
                  type="monotone"
                  dataKey="cumulative"
                  name="Cumulative spend"
                  stroke="var(--series-2)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-1)" }}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      <Card title="Monthly detail" hint="Everything the projection is built from">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Month</th>
                <th className="num">Income</th>
                <th className="num">Spending</th>
                <th className="num">Essentials</th>
                <th className="num">Discretionary</th>
                <th className="num">Net</th>
              </tr>
            </thead>
            <tbody>
              {[...data.history].reverse().map((row) => (
                <tr key={row.month}>
                  <td>{monthLabel(row.month, true)}</td>
                  <td className="num">{money(row.income)}</td>
                  <td className="num">{money(row.spend)}</td>
                  <td className="num secondary">{money(row.essential)}</td>
                  <td className="num secondary">{money(row.discretionary)}</td>
                  <td className={`num ${row.net >= 0 ? "delta-up" : "delta-down"}`}>{money(row.net)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
