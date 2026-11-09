import { useState } from "react";

import { api, useApi } from "../lib/api";
import { categoryColor, dateLabel, money } from "../lib/format";
import type { Account, Transaction } from "../types";
import { Card, Empty, ErrorBanner, Loading } from "./ui";

interface Page {
  total: number;
  limit: number;
  offset: number;
  transactions: Transaction[];
}

const PAGE_SIZE = 60;

export default function Transactions() {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [accountId, setAccountId] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [includeTransfers, setIncludeTransfers] = useState(false);
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [ruleFor, setRuleFor] = useState<Transaction | null>(null);
  // After one transaction is re-filed: offer the same for the whole merchant.
  const [offer, setOffer] = useState<{ merchant: string; category: string } | null>(null);
  const [offerResult, setOfferResult] = useState<string | null>(null);

  const params = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(offset),
    include_transfers: String(includeTransfers),
  });
  if (search) params.set("search", search);
  if (category) params.set("category", category);
  if (accountId) params.set("account_id", accountId);
  if (start) params.set("start", start);
  if (end) params.set("end", end);

  const page = useApi<Page>(`/api/transactions?${params.toString()}`, [
    search,
    category,
    accountId,
    start,
    end,
    includeTransfers,
    offset,
    version,
  ]);
  const categories = useApi<{ categories: string[] }>("/api/categories");
  const accounts = useApi<{ accounts: Account[] }>("/api/accounts");

  const refresh = () => setVersion((v) => v + 1);

  const setCategoryFor = async (txn: Transaction, next: string) => {
    await api.patch(`/api/transactions/${txn.id}`, { category: next });
    setOffer({ merchant: txn.merchant, category: next });
    setOfferResult(null);
    refresh();
  };

  const applyToMerchant = async () => {
    if (!offer) return;
    try {
      const result = await api.post<{ recategorised: number }>("/api/categorise/merchant", offer);
      setOfferResult(
        `Done: ${result.recategorised} more transaction${result.recategorised === 1 ? "" : "s"} moved, and future ones will follow.`,
      );
      setOffer(null);
      refresh();
    } catch (err) {
      setOffer(null); // the result line, not the question, is what shows the error
      setOfferResult(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="stack">
      <Card title="Filters">
        <div className="row">
          <input
            placeholder="Search merchant or description"
            value={search}
            style={{ minWidth: 240 }}
            onChange={(event) => {
              setOffset(0);
              setSearch(event.target.value);
            }}
          />
          <select
            value={category}
            onChange={(event) => {
              setOffset(0);
              setCategory(event.target.value);
            }}
          >
            <option value="">All categories</option>
            {(categories.data?.categories ?? []).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <select
            value={accountId}
            onChange={(event) => {
              setOffset(0);
              setAccountId(event.target.value);
            }}
          >
            <option value="">All accounts</option>
            {(accounts.data?.accounts ?? []).map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
          <input type="date" value={start} onChange={(event) => setStart(event.target.value)} title="From" />
          <input type="date" value={end} onChange={(event) => setEnd(event.target.value)} title="To" />
          <label className="row tiny secondary" style={{ gap: 6 }}>
            <input
              type="checkbox"
              checked={includeTransfers}
              onChange={(event) => setIncludeTransfers(event.target.checked)}
            />
            Show internal transfers
          </label>
          <button
            className="btn"
            onClick={() => {
              setSearch("");
              setCategory("");
              setAccountId("");
              setStart("");
              setEnd("");
              setOffset(0);
            }}
          >
            Clear
          </button>
        </div>
      </Card>

      {(offer || offerResult) && (
        <div className="banner row-between" role="status">
          {offer ? (
            <>
              <span>
                Put every <strong>{offer.merchant}</strong> payment in <strong>{offer.category}</strong>, past and
                future?
              </span>
              <span className="row">
                <button className="btn btn-primary" onClick={applyToMerchant}>
                  Yes, all of them
                </button>
                <button className="btn" onClick={() => setOffer(null)}>
                  Just this one
                </button>
              </span>
            </>
          ) : (
            <>
              <span>{offerResult}</span>
              <button className="btn btn-ghost" onClick={() => setOfferResult(null)} aria-label="Dismiss">
                ✕
              </button>
            </>
          )}
        </div>
      )}

      {ruleFor && (
        <RuleForm
          transaction={ruleFor}
          categories={categories.data?.categories ?? []}
          onClose={() => setRuleFor(null)}
          onSaved={() => {
            setRuleFor(null);
            refresh();
          }}
        />
      )}

      <Card
        title="Transactions"
        hint={page.data ? `${page.data.total.toLocaleString("en-GB")} matching` : undefined}
        actions={
          <div className="row">
            <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(offset - PAGE_SIZE, 0))}>
              ← Newer
            </button>
            <button
              className="btn"
              disabled={!page.data || offset + PAGE_SIZE >= page.data.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Older →
            </button>
          </div>
        }
      >
        {page.error ? (
          <ErrorBanner message={page.error} onRetry={page.reload} />
        ) : !page.data ? (
          <Loading />
        ) : page.data.transactions.length === 0 ? (
          <Empty>Nothing matches those filters.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Merchant</th>
                  <th>Account</th>
                  <th>Category</th>
                  <th className="num">Amount</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {page.data.transactions.map((txn) => (
                  <tr key={txn.id}>
                    <td className="nowrap secondary">{dateLabel(txn.date, true)}</td>
                    <td>
                      <span className="swatch" style={{ background: categoryColor(txn.category) }} />
                      {txn.merchant}
                      {txn.is_transfer && <span className="badge" style={{ marginLeft: 8 }}>Transfer</span>}
                      {txn.status === "pending" && (
                        <span className="badge" style={{ marginLeft: 8 }}>Pending</span>
                      )}
                      <div className="muted tiny" style={{ marginLeft: 18 }}>
                        {txn.description}
                      </div>
                    </td>
                    <td className="secondary nowrap">{txn.account}</td>
                    <td>
                      <select
                        value={txn.category}
                        onChange={(event) => setCategoryFor(txn, event.target.value)}
                        style={txn.category_source === "manual" ? { borderColor: "var(--series-1)" } : undefined}
                        title={`Set by: ${txn.category_source}`}
                      >
                        {(categories.data?.categories ?? [txn.category]).map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className={`num ${txn.amount >= 0 ? "delta-up" : ""}`}>{money(txn.amount)}</td>
                    <td className="num">
                      <button className="btn btn-ghost" title="Always categorise this merchant this way" onClick={() => setRuleFor(txn)}>
                        Rule
                      </button>
                    </td>
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

function RuleForm({
  transaction,
  categories,
  onClose,
  onSaved,
}: {
  transaction: Transaction;
  categories: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [pattern, setPattern] = useState(transaction.merchant.toLowerCase());
  const [category, setCategory] = useState(transaction.category);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      const response = await api.post<{ recategorised: number }>("/api/rules", {
        pattern,
        category,
        match_type: "words",
        field: "merchant",
        apply_now: true,
      });
      setResult(`Rule saved — ${response.recategorised} transactions recategorised.`);
      setTimeout(onSaved, 900);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title="New categorisation rule" hint="Applies to every past and future transaction that matches">
      <form className="row" onSubmit={save}>
        <input
          value={pattern}
          onChange={(event) => setPattern(event.target.value)}
          style={{ minWidth: 240 }}
          placeholder="Merchant name has the words…"
        />
        <span className="muted">→</span>
        <select value={category} onChange={(event) => setCategory(event.target.value)}>
          {categories.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <button className="btn btn-primary" disabled={saving || !pattern}>
          {saving ? "Saving…" : "Save rule"}
        </button>
        <button type="button" className="btn" onClick={onClose}>
          Cancel
        </button>
        {result && <span className="secondary">{result}</span>}
      </form>
    </Card>
  );
}
