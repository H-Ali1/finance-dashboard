import { useState } from "react";

import { useApi } from "../lib/api";
import { categoryColor, dateLabel, money, relativeDays } from "../lib/format";
import type { Subscription, SubscriptionSummary } from "../types";
import { Card, Empty, ErrorBanner, Loading, Stat } from "./ui";

interface Response {
  summary: SubscriptionSummary;
  items: Subscription[];
  upcoming: Subscription[];
}

type Filter = "all" | "subscription" | "bill" | "attention";

const STATUS_LABEL: Record<Subscription["status"], string> = {
  active: "Active",
  overdue: "Overdue",
  stopped: "Stopped",
};

export default function Subscriptions() {
  const { data, error, loading, reload } = useApi<Response>("/api/subscriptions");
  const [filter, setFilter] = useState<Filter>("all");

  if (loading && !data) return <Loading label="Looking for recurring payments" />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!data) return null;

  const { summary, items, upcoming } = data;
  const needsAttention = items.filter(
    (item) => item.price_change?.direction === "up" || item.duplicate_of || item.status !== "active",
  );

  const visible =
    filter === "all"
      ? items
      : filter === "attention"
        ? needsAttention
        : items.filter((item) => item.type === filter);

  return (
    <div className="stack">
      <div className="grid cols-4">
        <Stat
          label="Subscriptions"
          value={money(summary.subscription_monthly, true)}
          note={`${summary.subscription_count} cancellable · ${money(summary.subscription_annual, true)}/year`}
        />
        <Stat
          label="Fixed bills"
          value={money(summary.bills_monthly, true)}
          note={`${money(summary.bills_annual, true)}/year · rent, utilities, insurance`}
        />
        <Stat
          label="All commitments"
          value={money(summary.monthly_total, true)}
          note={`${summary.active_count} active recurring payments`}
        />
        <Stat
          label="Needs attention"
          value={needsAttention.length}
          note={`${summary.price_rises.length} price rises · ${summary.duplicates.length} duplicates`}
        />
      </div>

      {(summary.price_rises.length > 0 || summary.duplicates.length > 0 || summary.stopped.length > 0) && (
        <Card title="Flagged" hint="Detected from your payment history, not a fixed list of brands">
          {summary.price_rises.map((item) => (
            <div className="insight" key={`rise-${item.merchant_key}-${item.account_id}`}>
              <span className="icon icon-warning">↑</span>
              <div className="body">
                <div className="title">
                  {item.merchant} went up {item.price_change?.pct.toFixed(0)}%
                </div>
                <div className="detail">
                  {money(item.price_change?.from ?? 0)} → {money(item.price_change?.to ?? 0)} per{" "}
                  {item.cadence.replace("ly", "")} on {item.account}.
                </div>
              </div>
              <div className="impact">
                {money(item.price_change?.annual_impact ?? 0, true)}
                <small>per year</small>
              </div>
            </div>
          ))}
          {/* Both sides of a duplicate are in the list — show the pair once. */}
          {summary.duplicates
            .filter(
              (item, index) =>
                summary.duplicates.findIndex((other) => other.family === item.family) === index,
            )
            .map((item) => (
            <div className="insight" key={`dupe-${item.merchant_key}-${item.account_id}`}>
              <span className="icon icon-warning">⧉</span>
              <div className="body">
                <div className="title">{item.merchant} is billed on more than one account</div>
                <div className="detail">
                  {item.account} and {item.duplicate_of?.join(", ")}. Cancel whichever you don't use.
                </div>
              </div>
              <div className="impact">
                {money(item.annual_cost, true)}
                <small>per year</small>
              </div>
            </div>
          ))}
          {summary.stopped.map((item) => (
            <div className="insight" key={`stopped-${item.merchant_key}-${item.account_id}`}>
              <span className="icon icon-info">i</span>
              <div className="body">
                <div className="title">{item.merchant} has stopped charging you</div>
                <div className="detail">
                  Last seen {dateLabel(item.last_seen, true)} at {money(item.typical_amount)}{" "}
                  {item.cadence}. Either cancelled, or a payment failed.
                </div>
              </div>
            </div>
          ))}
        </Card>
      )}

      <Card title="Due in the next 30 days" hint={`${money(upcoming.reduce((s, i) => s + i.amount, 0))} committed`}>
        {upcoming.length === 0 ? (
          <Empty>Nothing due in the next 30 days.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Payment</th>
                  <th>Account</th>
                  <th>Expected</th>
                  <th className="num">Amount</th>
                </tr>
              </thead>
              <tbody>
                {upcoming.map((item) => (
                  <tr key={`${item.merchant_key}-${item.account_id}`}>
                    <td>{item.merchant}</td>
                    <td className="secondary">{item.account}</td>
                    <td className="nowrap">
                      {dateLabel(item.next_expected)}
                      <span className="muted tiny"> · {relativeDays(item.days_until_due)}</span>
                    </td>
                    <td className="num">{money(item.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="Everything recurring"
        hint="A payment counts as recurring when the cadence and amount are both stable"
        actions={
          <div className="row">
            {(["all", "subscription", "bill", "attention"] as Filter[]).map((key) => (
              <button
                key={key}
                className="btn"
                onClick={() => setFilter(key)}
                style={
                  filter === key
                    ? { borderColor: "var(--series-1)", color: "var(--series-1)", fontWeight: 600 }
                    : undefined
                }
              >
                {key === "all"
                  ? "All"
                  : key === "subscription"
                    ? "Subscriptions"
                    : key === "bill"
                      ? "Bills"
                      : "Needs attention"}
              </button>
            ))}
          </div>
        }
      >
        {visible.length === 0 ? (
          <Empty>Nothing matches that filter.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Payment</th>
                  <th>Cadence</th>
                  <th>Status</th>
                  <th className="num">Each</th>
                  <th className="num">Per year</th>
                  <th className="num">Paid so far</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((item) => (
                  <tr key={`${item.merchant_key}-${item.account_id}`}>
                    <td>
                      <span className="swatch" style={{ background: categoryColor(item.category) }} />
                      {item.merchant}
                      <div className="muted tiny" style={{ marginLeft: 18 }}>
                        {item.category} · {item.account} · since {dateLabel(item.first_seen, true)}
                      </div>
                    </td>
                    <td className="secondary nowrap">
                      {item.cadence}
                      <div className="muted tiny">{item.occurrences} payments</div>
                    </td>
                    <td>
                      <span className="badge">
                        <span
                          className={`dot ${
                            item.status === "active"
                              ? "dot-good"
                              : item.status === "overdue"
                                ? "dot-warning"
                                : "dot-info"
                          }`}
                        />
                        {STATUS_LABEL[item.status]}
                      </span>
                    </td>
                    <td className="num">
                      {money(item.amount)}
                      {item.price_change?.direction === "up" && (
                        <div className="delta-down tiny">↑ from {money(item.price_change.from)}</div>
                      )}
                    </td>
                    <td className="num">{money(item.annual_cost, true)}</td>
                    <td className="num secondary">{money(item.total_paid, true)}</td>
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
