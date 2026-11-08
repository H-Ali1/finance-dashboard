import { useEffect, useRef, useState } from "react";

import { api, useApi } from "../lib/api";
import { dateLabel, money, timeAgo } from "../lib/format";
import type { Account, Connection, ConnectionsResponse, TrueLayerBank } from "../types";
import { Card, Empty, ErrorBanner, Loading } from "./ui";

const PROVIDER_LABEL: Record<string, string> = {
  starling: "Starling",
  trading212: "Trading 212",
  truelayer: "TrueLayer",
  csv: "Statement import",
};

export default function Connections({ onSynced }: { onSynced: () => void }) {
  const [version, setVersion] = useState(0);
  const { data, error, loading, reload } = useApi<ConnectionsResponse>("/api/connections", [version]);
  const accounts = useApi<{ accounts: Account[] }>("/api/accounts", [version]);
  const [busy, setBusy] = useState<number | "all" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // A TrueLayer bank to connect again, chosen from the table.
  const [reconnect, setReconnect] = useState<{ bankId: string; at: number } | null>(null);

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
      const result = await api.post<{ transactions_added?: number; skipped?: string }>(
        `/api/connections/${connection.id}/sync`,
      );
      setMessage(
        result.skipped
          ? `${connection.institution_name}: ${result.skipped}`
          : `${connection.institution_name}: ${result.transactions_added ?? 0} new transactions.`,
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

      {data.providers.truelayer?.configured && (
        <ConnectBank
          key={reconnect?.at ?? "new"}
          connections={data.connections}
          initialBankId={reconnect?.bankId ?? ""}
          onConnected={(text) => {
            setMessage(text);
            refresh();
          }}
        />
      )}

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
          <Empty>
            Nothing connected yet. Add a Starling, Trading 212 or TrueLayer key in the App's Configuration tab, or
            import a statement file below.
          </Empty>
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
                      {connection.expires_at && connection.status !== "expired" && (
                        <div className="muted tiny">Access ends {dateLabel(connection.expires_at, true)}</div>
                      )}
                    </td>
                    <td className="num">{connection.account_count}</td>
                    <td className="secondary nowrap">{timeAgo(connection.last_synced_at)}</td>
                    <td className="num nowrap">
                      {connection.provider === "truelayer" && connection.bank_id && (
                        <>
                          <button
                            className={connection.status === "expired" ? "btn btn-primary" : "btn"}
                            onClick={() => setReconnect({ bankId: connection.bank_id!, at: Date.now() })}
                            title="Approve access again for another 90 days"
                          >
                            Connect again
                          </button>{" "}
                        </>
                      )}
                      {connection.status !== "expired" && (
                        <>
                          <button className="btn" disabled={busy !== null} onClick={() => syncOne(connection)}>
                            {busy === connection.id ? "Syncing…" : "Sync"}
                          </button>{" "}
                        </>
                      )}
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

/**
 * Connecting a bank through TrueLayer: pick it, approve at the bank in a new
 * tab, then paste back the code TrueLayer shows. The dashboard never has to
 * be reachable from the internet for this.
 */
function ConnectBank({
  connections,
  initialBankId,
  onConnected,
}: {
  connections: Connection[];
  initialBankId: string;
  onConnected: (message: string) => void;
}) {
  const banks = useApi<{ banks: TrueLayerBank[] }>("/api/truelayer/banks");
  const [bankId, setBankId] = useState(initialBankId);
  // The approval address for one bank and attempt; `key` says which.
  const [link, setLink] = useState<{ key: string; url: string | null; error: string | null } | null>(null);
  const [opened, setOpened] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [linkVersion, setLinkVersion] = useState(0);
  const cardRef = useRef<HTMLDivElement>(null);

  // Arriving from "Connect again" in the table: bring this card into view.
  useEffect(() => {
    if (initialBankId) cardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [initialBankId]);

  // Each address works once, so a fresh one is made per attempt.
  const linkKey = `${bankId}:${linkVersion}`;
  useEffect(() => {
    if (!bankId) return;
    const bank = banks.data?.banks.find((b) => b.provider_id === bankId);
    let cancelled = false;
    api
      .post<{ url: string }>("/api/truelayer/link", { bank_id: bankId, scopes: bank?.scopes })
      .then((response) => {
        if (!cancelled) setLink({ key: linkKey, url: response.url, error: null });
      })
      .catch((err) => {
        if (!cancelled) setLink({ key: linkKey, url: null, error: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [bankId, linkKey, banks.data]);
  const current = bankId && link?.key === linkKey ? link : null;

  const connectedIds = new Set(connections.filter((c) => c.provider === "truelayer").map((c) => c.bank_id));
  const bankName = banks.data?.banks.find((b) => b.provider_id === bankId)?.name ?? "your bank";

  const finish = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!code.trim()) return;
    setBusy(true);
    setResult(null);
    try {
      const response = await api.post<{ institution: string; accounts?: number; transactions_added?: number }>(
        "/api/truelayer/connect",
        { code: code.trim() },
      );
      onConnected(
        `${response.institution} connected: ${response.accounts ?? 0} account(s), ` +
          `${response.transactions_added ?? 0} transactions read.`,
      );
      setBankId("");
      setOpened(false);
      setCode("");
    } catch (err) {
      setResult(err instanceof Error ? err.message : String(err));
      setLinkVersion((v) => v + 1);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div ref={cardRef}>
      <Card
        title="Connect a bank through TrueLayer"
        hint="For HSBC, Amex and other UK banks. You approve read-only access on your bank's own screen. TrueLayer shows a test-mode notice there: it doesn't support personal use, so this can stop working without warning."
      >
        {banks.error ? (
          <ErrorBanner message={banks.error} onRetry={banks.reload} />
        ) : (
          <div className="stack" style={{ gap: 12 }}>
            <div className="row">
              <select
                value={bankId}
                onChange={(event) => {
                  setBankId(event.target.value);
                  setOpened(false);
                  setResult(null);
                }}
              >
                <option value="">{banks.loading ? "Loading banks…" : "Choose a bank…"}</option>
                {banks.data?.banks.map((bank) => (
                  <option key={bank.provider_id} value={bank.provider_id}>
                    {bank.name}
                    {connectedIds.has(bank.provider_id) ? " (connected)" : ""}
                  </option>
                ))}
              </select>
              {bankId &&
                (current?.url ? (
                  <a
                    className="btn btn-primary"
                    href={current.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={() => {
                      setOpened(true);
                      // That address is now used; have a fresh one ready for a retry.
                      window.setTimeout(() => setLinkVersion((v) => v + 1), 500);
                    }}
                  >
                    1. Approve at {bankName}
                  </a>
                ) : (
                  <span className="muted tiny">{current?.error ?? "Preparing…"}</span>
                ))}
            </div>

            {bankId && (
              <form className="row" onSubmit={finish}>
                <input
                  placeholder="2. Paste the code TrueLayer shows"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  style={{ minWidth: 280 }}
                  autoComplete="off"
                  spellCheck={false}
                />
                <button className="btn btn-primary" disabled={busy || !code.trim()}>
                  {busy ? "Connecting…" : "3. Finish"}
                </button>
              </form>
            )}
            {opened && (
              <div className="muted tiny">
                After you approve, TrueLayer opens a page showing a code. Copy it, or the page's whole address, into
                the box above within 5 minutes. The first read fetches as much history as the bank allows.
              </div>
            )}
            {result && <div className="banner error">{result}</div>}
          </div>
        )}
      </Card>
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
  const [newKind, setNewKind] = useState("current");

  const createAccount = async () => {
    if (!newName) return;
    const form = new FormData();
    form.set("name", newName);
    form.set("institution", newName);
    form.set("kind", newKind);
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
        first_date: string;
        last_date: string;
        money_in: number;
        money_out: number;
        signs_flipped: boolean;
      }>("/api/import/csv", form);
      setResult(
        `Read ${response.rows_parsed} rows (${response.detected_format}) from ${response.first_date} to ` +
          `${response.last_date}: ${money(response.money_out)} out, ${money(response.money_in)} in. ` +
          `${response.added} added, ${response.duplicates_skipped} already there.` +
          (response.signs_flipped ? " Signs were flipped to match a card statement." : "") +
          " If money in and out look swapped, tick Flip signs and import the same file again — it corrects them.",
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
      hint="CSV exports from Starling and Amex are recognised; other banks (HSBC, Chase, ...) use a generic date/description/amount mapping. Re-importing an overlapping file is safe — duplicates are skipped."
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
        <select value={newKind} onChange={(event) => setNewKind(event.target.value)} title="Account type">
          <option value="current">Current account</option>
          <option value="savings">Savings</option>
          <option value="credit_card">Credit card</option>
        </select>
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
