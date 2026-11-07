import { useState } from "react";

import { api, useApi } from "../lib/api";
import { money } from "../lib/format";
import type { Headline, Insight } from "../types";
import { Card, Empty, ErrorBanner, InsightRow, Loading, Stat } from "./ui";

interface InsightsResponse {
  headline: Headline;
  insights: Insight[];
  by_area: Record<string, Insight[]>;
  total_annual_impact: number;
}

const AREA_TITLES: Record<string, string> = {
  cashflow: "Cash flow",
  budget: "Budgets",
  trend: "Spending trends",
  subscriptions: "Subscriptions",
  debt: "Debt",
  fees: "Fees & interest",
  anomalies: "Unusual activity",
  connections: "Connections",
  hygiene: "Data quality",
  general: "Other",
};

export default function Insights() {
  const { data, error, loading, reload } = useApi<InsightsResponse>("/api/insights");
  const [busy, setBusy] = useState(false);

  if (loading && !data) return <Loading label="Reviewing your finances" />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!data) return null;

  const dismiss = async (key: string) => {
    setBusy(true);
    try {
      await api.post("/api/insights/dismiss", { key });
      await reload();
    } finally {
      setBusy(false);
    }
  };

  const counts = data.headline.counts;

  return (
    <div className="stack">
      <div className="grid cols-4">
        <Stat label="Critical" value={counts.critical} small note="Deal with these first" />
        <Stat label="Warnings" value={counts.warning} small note="Worth a look this month" />
        <Stat label="For info" value={counts.info} small note="No action needed yet" />
        <Stat
          label="Annual impact"
          value={money(data.total_annual_impact, true)}
          small
          note="If every yearly-impact finding were fixed"
        />
      </div>

      {data.insights.length === 0 ? (
        <Card title="Nothing to fix">
          <Empty>
            No findings. Either your finances are in good shape or there isn't enough history yet —
            sync a few more months and check back.
          </Empty>
        </Card>
      ) : (
        Object.entries(data.by_area).map(([area, items]) => (
          <Card
            key={area}
            title={AREA_TITLES[area] ?? area}
            hint={`${items.length} finding${items.length === 1 ? "" : "s"}`}
          >
            {items.map((insight) => (
              <InsightRow key={insight.key} insight={insight} onDismiss={busy ? undefined : dismiss} />
            ))}
          </Card>
        ))
      )}
    </div>
  );
}
