import { useState } from "react";

import { api, useApi } from "../lib/api";
import { describeRule } from "../lib/rules";
import type { Account, Rule, UncategorisedList } from "../types";
import RuleEditor from "./RuleEditor";
import { Card, Empty, ErrorBanner, Loading } from "./ui";

/** Your rules, in the order they're checked, and what's been learned. */
export default function Rules() {
  const [version, setVersion] = useState(0);
  const rules = useApi<{ rules: Rule[] }>("/api/rules", [version]);
  const categories = useApi<{ categories: string[] }>("/api/categories");
  const accounts = useApi<{ accounts: Account[] }>("/api/accounts");
  const learning = useApi<UncategorisedList>("/api/categorise/uncategorised?days=180&limit=0", [version]);
  // null: no editor; {} a new rule; a rule: changing it.
  const [editing, setEditing] = useState<Partial<Rule> | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const accountList = accounts.data?.accounts ?? [];

  const remove = async (rule: Rule) => {
    try {
      const result = await api.del<{ recategorised: number }>(`/api/rules/${rule.id}`);
      setMessage(`Rule deleted: ${result.recategorised} transaction${result.recategorised === 1 ? "" : "s"} changed.`);
      setVersion((v) => v + 1);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setConfirming(null);
    }
  };

  if (rules.error) return <ErrorBanner message={rules.error} onRetry={rules.reload} />;
  if (!rules.data) return <Loading />;

  return (
    <div className="stack">
      {message && (
        <div className="banner row-between" role="status">
          <span>{message}</span>
          <button className="btn btn-ghost" onClick={() => setMessage(null)} aria-label="Dismiss">
            ✕
          </button>
        </div>
      )}

      {categories.error && <ErrorBanner message={`Categories didn't load: ${categories.error}`} onRetry={categories.reload} />}
      {accounts.error && <ErrorBanner message={`Accounts didn't load: ${accounts.error}`} onRetry={accounts.reload} />}

      {editing && (
        <RuleEditor
          key={editing.id ?? "new"}
          rule={editing}
          categories={categories.data?.categories ?? []}
          accounts={accountList}
          onClose={() => setEditing(null)}
          onSaved={(text) => {
            // Only this editor closes: another may have opened while it saved.
            const owner = editing;
            setEditing((current) => (current === owner ? null : current));
            setMessage(text);
            setVersion((v) => v + 1);
          }}
        />
      )}

      <Card
        title="Rules"
        hint="Checked in this order, the most specific first. Each action comes from the first rule that has it, so one rule can rename a merchant while another files it."
        actions={
          <button className="btn btn-primary" onClick={() => setEditing({})} disabled={editing !== null}>
            New rule
          </button>
        }
      >
        {rules.data.rules.length === 0 ? (
          <Empty>
            No rules yet. Make one here, from a transaction's Rule button, or by choosing a category under Needs a
            category on the Spending tab.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Then</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rules.data.rules.map((rule) => {
                  const { when, then } = describeRule(rule, accountList);
                  return (
                    <tr key={rule.id}>
                      <td>{when}</td>
                      <td>{then}</td>
                      <td className="num nowrap">
                        <button className="btn btn-ghost" onClick={() => setEditing(rule)}>
                          Edit
                        </button>
                        {confirming === rule.id ? (
                          <button className="btn" onClick={() => remove(rule)}>
                            Delete it
                          </button>
                        ) : (
                          <button className="btn btn-ghost" onClick={() => setConfirming(rule.id)}>
                            Delete
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {learning.data && (
        <Card
          title="Learned from your choices"
          hint="Money out that no rule or keyword can place is filed like similar merchants you've categorised, when the match is clear. It runs on your box: nothing is sent anywhere."
        >
          <div className="secondary">
            {learning.data.learned_from === 0
              ? "Nothing to learn from yet: categorise a few merchants first."
              : `Learning from ${learning.data.learned_from} merchant${learning.data.learned_from === 1 ? "" : "s"} you've categorised. ${
                  learning.data.learned_count === 0
                    ? "Nothing has been filed this way in the last 6 months."
                    : `${learning.data.learned_count} merchant${learning.data.learned_count === 1 ? " was" : "s were"} filed this way in the last 6 months: check them under Needs a category on the Spending tab.`
                }`}
          </div>
        </Card>
      )}
    </div>
  );
}
