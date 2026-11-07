import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api, useApi } from "../lib/api";
import { dateLabel, money, moneyShort, timeAgo } from "../lib/format";
import type { Account, ManualAsset, NetWorth as NetWorthData, NetWorthPoint } from "../types";
import { BarList, Card, ChartTooltip, Empty, ErrorBanner, Legend, Loading, Stat } from "./ui";

const AXIS = { tick: { fill: "var(--text-muted)", fontSize: 11 }, tickLine: false, axisLine: { stroke: "var(--axis)" } };

const CATEGORIES = [
  { value: "car", label: "Car" },
  { value: "pension", label: "Pension" },
  { value: "property", label: "Property" },
  { value: "other", label: "Other" },
];

export default function NetWorth() {
  const [version, setVersion] = useState(0);
  const nw = useApi<NetWorthData>("/api/networth", [version]);
  const history = useApi<{ series: NetWorthPoint[]; change: { amount: number; pct: number | null; since: string } | null }>(
    "/api/networth/history?days=365",
    [version],
  );
  const accounts = useApi<{ accounts: Account[] }>("/api/accounts", [version]);
  const assets = useApi<{ assets: ManualAsset[] }>("/api/assets", [version]);

  if (nw.error) return <ErrorBanner message={nw.error} onRetry={nw.reload} />;
  if (!nw.data) return <Loading />;

  const data = nw.data;
  const series = history.data?.series ?? [];
  const change = history.data?.change ?? null;
  const t212Accounts = (accounts.data?.accounts ?? []).filter(
    (a) => a.provider === "trading212" && a.holdings.length > 0,
  );

  const refresh = () => setVersion((v) => v + 1);

  return (
    <div className="stack">
      <div className="grid cols-4">
        <Stat
          label="Net worth"
          value={money(data.net, true)}
          note={
            change ? (
              <span className={change.amount >= 0 ? "delta-up" : "delta-down"}>
                {change.amount >= 0 ? "▲" : "▼"} {money(Math.abs(change.amount), true)} since{" "}
                {dateLabel(change.since, true)}
              </span>
            ) : (
              `as of ${dateLabel(data.as_of, true)}`
            )
          }
        />
        <Stat label="Assets" value={money(data.assets, true)} note={`${data.asset_items.length} holdings`} />
        <Stat
          label="Owed"
          value={money(data.liabilities, true)}
          note={`${data.liability_items.length} debts · ${money(data.card_debt, true)} on cards`}
        />
        <Stat
          label="Invested"
          value={money(data.investments + data.pension, true)}
          note={`${money(data.investments, true)} markets · ${money(data.pension, true)} pension`}
        />
      </div>

      <Card title="Net worth over time" hint="One snapshot per day, taken whenever you sync">
        {series.length < 2 ? (
          <Empty>Not enough snapshots yet — this fills in as you sync.</Empty>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={series} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="date" {...AXIS} minTickGap={50} tickFormatter={(v) => dateLabel(v)} />
                <YAxis {...AXIS} width={62} tickFormatter={moneyShort} />
                <Tooltip
                  content={<ChartTooltip labelFormatter={(v) => dateLabel(v, true)} />}
                  cursor={{ stroke: "var(--axis)" }}
                />
                <Line
                  type="monotone"
                  dataKey="net"
                  name="Net worth"
                  stroke="var(--series-1)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-1)" }}
                />
                <Line
                  type="monotone"
                  dataKey="assets"
                  name="Assets"
                  stroke="var(--series-3)"
                  strokeWidth={2}
                  strokeDasharray="4 3"
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
            <Legend
              items={[
                { label: "Net worth", color: "var(--series-1)" },
                { label: "Total assets", color: "var(--series-3)" },
              ]}
            />
          </>
        )}
      </Card>

      <div className="grid cols-2">
        <Card title="Assets" hint="Balances, investments and anything you've entered by hand">
          <BarList
            rows={data.asset_items.map((item) => ({
              label: item.name,
              value: item.value,
              color: item.source === "manual" ? "var(--series-7)" : "var(--series-1)",
              sub: item.institution,
            }))}
          />
        </Card>
        <Card title="What you owe" hint="Cards, overdrafts and manual liabilities">
          {data.liability_items.length === 0 ? (
            <Empty>No debts recorded. Nice.</Empty>
          ) : (
            <BarList
              rows={data.liability_items.map((item) => ({
                label: item.name,
                value: item.value,
                color: "var(--series-8)",
                sub: item.institution,
              }))}
            />
          )}
        </Card>
      </div>

      {t212Accounts.map((t212) => (
        <Card
          key={t212.id}
          title={t212Accounts.length > 1 ? `${t212.name} holdings` : "Trading 212 holdings"}
          hint={`${money(t212.balance, true)} total · updated ${timeAgo(t212.updated_at)}`}
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Holding</th>
                  <th className="num">Units</th>
                  <th className="num">Price</th>
                  <th className="num">Value</th>
                  <th className="num">P/L</th>
                </tr>
              </thead>
              <tbody>
                {t212.holdings.map((holding) => (
                  <tr key={holding.ticker}>
                    <td>
                      {holding.name || holding.ticker}
                      {holding.name && <div className="muted tiny">{holding.ticker}</div>}
                    </td>
                    <td className="num">{holding.quantity}</td>
                    <td className="num">{sharePrice(holding.current_price, holding.price_currency)}</td>
                    <td className="num">{money(holding.value)}</td>
                    <td className={`num ${holding.ppl >= 0 ? "delta-up" : "delta-down"}`}>
                      {money(holding.ppl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ))}

      <ManualAssets assets={assets.data?.assets ?? []} onChange={refresh} />

      <Card title="Accounts" hint="Untick anything you don't want counted in net worth">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Account</th>
                <th>Type</th>
                <th className="num">Balance</th>
                <th>Updated</th>
                <th className="num">In net worth</th>
              </tr>
            </thead>
            <tbody>
              {(accounts.data?.accounts ?? []).map((account) => (
                <tr key={account.id}>
                  <td>
                    {account.name}
                    <div className="muted tiny">{account.institution}</div>
                  </td>
                  <td>
                    <select
                      value={account.kind}
                      onChange={async (event) => {
                        await api.patch(`/api/accounts/${account.id}`, { kind: event.target.value });
                        refresh();
                      }}
                    >
                      {["current", "savings", "credit_card", "investment", "loan", "mortgage"].map((kind) => (
                        <option key={kind} value={kind}>
                          {kind.replace("_", " ")}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className={`num ${account.balance < 0 ? "delta-down" : ""}`}>{money(account.balance)}</td>
                  <td className="secondary nowrap">{timeAgo(account.updated_at)}</td>
                  <td className="num">
                    <input
                      type="checkbox"
                      checked={account.include_in_networth}
                      onChange={async (event) => {
                        await api.patch(`/api/accounts/${account.id}`, {
                          include_in_networth: event.target.checked,
                        });
                        refresh();
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function ManualAssets({ assets, onChange }: { assets: ManualAsset[]; onChange: () => void }) {
  const [form, setForm] = useState({ name: "", category: "car", kind: "asset", value: "" });
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");

  const add = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!form.name || !form.value) return;
    await api.post("/api/assets", { ...form, value: Number(form.value) });
    setForm({ name: "", category: "car", kind: "asset", value: "" });
    onChange();
  };

  const save = async (asset: ManualAsset) => {
    await api.put(`/api/assets/${asset.id}`, {
      name: asset.name,
      kind: asset.kind,
      category: asset.category,
      value: Number(draft),
      notes: asset.notes,
    });
    setEditing(null);
    onChange();
  };

  return (
    <Card
      title="Manual assets"
      hint="Things with no API — the car, your pension, anything else. Update the value whenever you like."
    >
      {assets.length === 0 ? (
        <Empty>Nothing added yet.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th className="num">Value</th>
                <th>Updated</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {assets.map((asset) => (
                <tr key={asset.id}>
                  <td>{asset.name}</td>
                  <td className="secondary">
                    {asset.category} · {asset.kind}
                  </td>
                  <td className="num">
                    {editing === asset.id ? (
                      <input
                        type="number"
                        step="0.01"
                        value={draft}
                        style={{ width: 110, textAlign: "right" }}
                        onChange={(event) => setDraft(event.target.value)}
                        autoFocus
                      />
                    ) : (
                      money(asset.value)
                    )}
                  </td>
                  <td className="secondary nowrap">{timeAgo(asset.updated_at)}</td>
                  <td className="num nowrap">
                    {editing === asset.id ? (
                      <>
                        <button className="btn btn-primary" onClick={() => save(asset)}>
                          Save
                        </button>{" "}
                        <button className="btn btn-ghost" onClick={() => setEditing(null)}>
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          className="btn"
                          onClick={() => {
                            setEditing(asset.id);
                            setDraft(String(asset.value));
                          }}
                        >
                          Update
                        </button>{" "}
                        <button
                          className="btn btn-ghost"
                          onClick={async () => {
                            await api.del(`/api/assets/${asset.id}`);
                            onChange();
                          }}
                        >
                          ✕
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <form className="row" style={{ marginTop: 14 }} onSubmit={add}>
        <input
          placeholder="Name (e.g. VW Golf)"
          value={form.name}
          onChange={(event) => setForm({ ...form, name: event.target.value })}
        />
        <select value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })}>
          {CATEGORIES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <select value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>
          <option value="asset">Asset</option>
          <option value="liability">Liability (owed)</option>
        </select>
        <input
          type="number"
          step="0.01"
          placeholder="Value (£)"
          value={form.value}
          onChange={(event) => setForm({ ...form, value: event.target.value })}
        />
        <button className="btn btn-primary">Add</button>
      </form>
    </Card>
  );
}

/** A share price in its own currency: US shares are priced in dollars, not pounds. */
function sharePrice(value: number, currency?: string): string {
  if (!currency || currency === "GBP") return money(value);
  if (currency === "GBX") return `${value.toLocaleString("en-GB", { maximumFractionDigits: 2 })}p`;
  try {
    return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(value);
  } catch {
    return `${value} ${currency}`;
  }
}
