import { useState } from "react";

import { api, useApi } from "../lib/api";
import { timeAgo } from "../lib/format";
import type { Account, Connection, ConnectionsResponse } from "../types";
import { Card, Empty, ErrorBanner, Loading } from "./ui";

const PROVIDER_LABEL: Record<string, string> = {
  starling: "Starling",
  trading212: "Trading 212",
  csv: "Statement import",
};

export default function Connections({ onSynced }: { onSynced: () => void }) {
  const [version, setVersion] = useState(0);
  const { data, error, loading, reload } = useApi<ConnectionsResponse>("/api/connections", [version]);
  const accounts = useApi<{ accounts: Account[] }>("/api/accounts", [version]);
  const [busy, setBusy] = useState<number | "all" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  if (loading && !data) return <Loading />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!data) return null;

  const refresh = () => {
    setVersion((v) => v + 1);
    onSynced();
  };

  const syncOne = async (connection: Connection) => {
    setBusy(connection.id);
    setMessage(null);
    try {
      const result = await api.post<{ transactions_added?: number }>(
        `/api/connections/${connection.id}/sync`,
      );
      setMessage(
        `${connection.institution_name}: ${result.transactions_added ?? 0} new transactions.`,
      );
      refresh();
    } catch (err) {
      setMessage(`${connection.institution_name}: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(null);
    }
  };

  const remove = async (connection: Connection) => {
    if (!window.confirm(`Disconnect ${connection.institution_name}? Transactions already imported are kept.`)) {
      return;
    }
    await api.del(`/api/connections/${connection.id}`);
    refresh();
  };

  return (
    <div className="stack">
      {message && <div className="banner">{message}</div>}

      <div className="grid cols-3">
        {Object.entries(data.providers).map(([key, provider]) => (
          <Card key={key} title={PROVIDER_LABEL[key] ?? key}>
            <div className="row" style={{ marginBottom: 8 }}>
              <span className="badge">
                <span className={`dot ${provider.configured ? "dot-good" : "dot-warning"}`} />
                {provider.configured ? "Credentials set" : "Not configured"}
              </span>
            </div>
            <div className="secondary tiny">{provider.how}</div>
          </Card>
        ))}
      </div>

      <Card
        title="Connected"
        actions={
          <button
            className="btn btn-primary"
            disabled={busy !== null}
            onClick={async () => {
              setBusy("all");
              setMessage(null);
              try {
                const result = await api.post<{ results: unknown[]; errors: { error: string }[] }>("/api/sync");
                setMessage(
                  result.errors.length
                    ? `Synced with ${result.errors.length} problem(s): ${result.errors.map((e) => e.error).join("; ")}`
                    : "All connections synced.",
                );
                refresh();
              } finally {
                setBusy(null);
              }
            }}
          >
            {busy === "all" ? "Syncing…" : "Sync everything"}
          </button>
        }
      >
        {data.connections.length === 0 ? (
          <Empty>Nothing connected yet. Add a Starling or Trading 212 key to backend/.env, or import a statement file below.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Institution</th>
                  <th>Status</th>
                  <th className="num">Accounts</th>
                  <th>Last sync</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {data.connections.map((connection) => (
                  <tr key={connection.id}>
                    <td>
                      {connection.institution_name}
                      <div className="muted tiny">{PROVIDER_LABEL[connection.provider] ?? connection.provider}</div>
                    </td>
                    <td>
                      <span className="badge">
                        <span
                          className={`dot ${
                            connection.status === "linked"
                              ? "dot-good"
                              : connection.status === "pending"
                                ? "dot-info"
                                : "dot-critical"
                          }`}
                        />
                        {connection.status}
                      </span>
                      {connection.last_error && (
                        <div className="muted tiny" style={{ maxWidth: 320 }}>
                          {connection.last_error}
                        </div>
                      )}
                    </td>
                    <td className="num">{connection.account_count}</td>
                    <td className="secondary nowrap">{timeAgo(connection.last_synced_at)}</td>
                    <td className="num nowrap">
                      <button className="btn" disabled={busy !== null} onClick={() => syncOne(connection)}>
                        {busy === connection.id ? "Syncing…" : "Sync"}
                      </button>{" "}
                      <button className="btn btn-ghost" onClick={() => remove(connection)}>
                        Disconnect
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <StatementImport accounts={accounts.data?.accounts ?? []} onImported={refresh} />
    </div>
  );
}

function StatementImport({ accounts, onImported }: { accounts: Account[]; onImported: () => void }) {
  const [accountId, setAccountId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [balance, setBalance] = useState("");
  const [flip, setFlip] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [newName, setNewName] = useState("");

  const createAccount = async () => {
    if (!newName) return;
    const form = new FormData();
    form.set("name", newName);
    form.set("institution", newName);
    form.set("kind", "current");
    const created = await api.upload<{ id: number }>("/api/accounts/manual", form);
    setAccountId(String(created.id));
    setNewName("");
    onImported();
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file || !accountId) return;
    setBusy(true);
    setResult(null);
    try {
      const form = new FormData();
      form.set("account_id", accountId);
      form.set("file", file);
      form.set("flip_sign", String(flip));
      if (balance) form.set("closing_balance", balance);
      const response = await api.upload<{
        added: number;
        rows_parsed: number;
        duplicates_skipped: number;
        detected_format: string;
      }>("/api/import/csv", form);
      setResult(
        `Read ${response.rows_parsed} rows as ${response.detected_format}: ${response.added} added, ` +
          `${response.duplicates_skipped} already there.`,
      );
      onImported();
    } catch (err) {
      setResult(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Import a statement"
      hint="CSV exports from Starling and Amex are detected automatically; other banks use a generic date/description/amount mapping. Re-importing an overlapping file is safe — duplicates are skipped."
    >
      <form className="row" onSubmit={submit}>
        <select value={accountId} onChange={(event) => setAccountId(event.target.value)}>
          <option value="">Import into…</option>
          {accounts.map((account) => (
            <option key={account.id} value={account.id}>
              {account.name}
            </option>
          ))}
        </select>
        <input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
        <input
          type="number"
          step="0.01"
          placeholder="Closing balance (optional)"
          value={balance}
          onChange={(event) => setBalance(event.target.value)}
          title="Sets the account balance after import, so net worth stays right"
        />
        <label className="row tiny secondary" style={{ gap: 6 }}>
          <input type="checkbox" checked={flip} onChange={(event) => setFlip(event.target.checked)} />
          Flip signs
        </label>
        <button className="btn btn-primary" disabled={busy || !file || !accountId}>
          {busy ? "Importing…" : "Import"}
        </button>
      </form>

      <div className="row" style={{ marginTop: 12 }}>
        <input
          placeholder="…or create an account to import into"
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
        />
        <button className="btn" onClick={createAccount} disabled={!newName}>
          Create account
        </button>
      </div>

      {result && (
        <div className="banner" style={{ marginTop: 12 }}>
          {result}
        </div>
      )}
    </Card>
  );
}
