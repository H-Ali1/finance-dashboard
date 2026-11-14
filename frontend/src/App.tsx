import { useEffect, useState } from "react";

import Cashflow from "./components/Cashflow";
import Connections from "./components/Connections";
import Dashboard from "./components/Dashboard";
import Income from "./components/Income";
import Insights from "./components/Insights";
import NetWorth from "./components/NetWorth";
import Rules from "./components/Rules";
import Spending from "./components/Spending";
import Subscriptions from "./components/Subscriptions";
import Transactions from "./components/Transactions";
import { api, useApi } from "./lib/api";
import { money, timeAgo } from "./lib/format";
import type { Health } from "./types";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "insights", label: "Needs fixing" },
  { key: "spending", label: "Spending" },
  { key: "subscriptions", label: "Subscriptions" },
  { key: "income", label: "Income" },
  { key: "cashflow", label: "Cash flow" },
  { key: "networth", label: "Net worth" },
  { key: "transactions", label: "Transactions" },
  { key: "rules", label: "Rules" },
  { key: "connections", label: "Connections" },
];

type Theme = "system" | "light" | "dark";

export default function App() {
  const [tab, setTab] = useState("overview");
  const [version, setVersion] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [syncNote, setSyncNote] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem("theme") as Theme | null) ?? "system",
  );

  const health = useApi<Health>("/api/health", [version]);
  const summary = useApi<{ net: number }>("/api/networth", [version]);

  useEffect(() => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

  const syncAll = async () => {
    setSyncing(true);
    setSyncNote(null);
    try {
      const result = await api.post<{ errors: { error: string }[]; transfer_pairs: number }>("/api/sync");
      setSyncNote(
        result.errors.length
          ? `Synced with problems: ${result.errors.map((e) => e.error).join("; ")}`
          : "Everything is up to date.",
      );
      setVersion((v) => v + 1);
    } catch (err) {
      setSyncNote(err instanceof Error ? err.message : String(err));
    } finally {
      setSyncing(false);
    }
  };

  const configured = Object.values(health.data?.providers_configured ?? {}).some(Boolean);

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <h1>Finance Dashboard</h1>
          <span className="sub">
            {health.data
              ? `${health.data.accounts} accounts · ${health.data.transactions.toLocaleString("en-GB")} transactions · latest ${
                  health.data.latest_transaction ? timeAgo(health.data.latest_transaction) : "none"
                }`
              : "connecting…"}
          </span>
        </div>

        {summary.data && (
          <span className="badge" title="Total net worth">
            Net worth {money(summary.data.net, true)}
          </span>
        )}

        <select value={theme} onChange={(event) => setTheme(event.target.value as Theme)} title="Theme">
          <option value="system">System theme</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>

        <button className="btn btn-primary" onClick={syncAll} disabled={syncing}>
          {syncing ? (
            <>
              <span className="spinner" style={{ marginRight: 8, verticalAlign: -2 }} /> Syncing
            </>
          ) : (
            "Sync now"
          )}
        </button>
      </header>

      <nav className="nav">
        {TABS.map((item) => (
          <button
            key={item.key}
            onClick={() => setTab(item.key)}
            aria-current={tab === item.key ? "page" : undefined}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <main>
        <div className="stack">
          {syncNote && <div className="banner">{syncNote}</div>}

          {health.data && !configured && health.data.transactions === 0 && (
            <div className="banner">
              <strong>Nothing connected yet.</strong>{" "}
              <span className="secondary">
                Add credentials to <code>backend/.env</code>, or run{" "}
                <code>python seed_demo.py</code> to explore with sample data. The Connections tab has
                the setup instructions for each provider.
              </span>
            </div>
          )}

          <div key={`${tab}-${version}`}>
            {tab === "overview" && <Dashboard onNavigate={setTab} />}
            {tab === "insights" && <Insights />}
            {tab === "spending" && <Spending />}
            {tab === "subscriptions" && <Subscriptions />}
            {tab === "income" && <Income />}
            {tab === "cashflow" && <Cashflow />}
            {tab === "networth" && <NetWorth />}
            {tab === "transactions" && <Transactions />}
            {tab === "rules" && <Rules />}
            {tab === "connections" && <Connections onSynced={() => setVersion((v) => v + 1)} />}
          </div>
        </div>
      </main>
    </div>
  );
}
