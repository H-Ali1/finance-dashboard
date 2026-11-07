import type { ReactNode } from "react";

import { money } from "../lib/format";
import type { Insight, Severity } from "../types";

export function Card({
  title,
  hint,
  actions,
  children,
  className,
}: {
  title?: string;
  hint?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <header>
          <div>
            {title && <h3>{title}</h3>}
            {hint && <div className="hint">{hint}</div>}
          </div>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  note,
  tone,
  small,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: "up" | "down";
  small?: boolean;
}) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className={`value ${small ? "sm" : ""} ${tone ? `delta-${tone}` : ""}`}>{value}</div>
      {note && <div className="note">{note}</div>}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="row muted" style={{ padding: 24 }}>
      <span className="spinner" /> {label}…
    </div>
  );
}

export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="banner error row-between">
      <div>
        <strong>Something went wrong.</strong> <span className="secondary">{message}</span>
      </div>
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

const SEVERITY_GLYPH: Record<Severity, string> = {
  critical: "!",
  warning: "!",
  info: "i",
  good: "✓",
};

const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Critical",
  warning: "Warning",
  info: "For info",
  good: "Going well",
};

/** Severity is carried by an icon + label, never by colour alone. */
export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className="badge">
      <span className={`dot dot-${severity}`} />
      {SEVERITY_LABEL[severity]}
    </span>
  );
}

export function InsightRow({
  insight,
  onDismiss,
}: {
  insight: Insight;
  onDismiss?: (key: string) => void;
}) {
  return (
    <div className="insight">
      <span className={`icon icon-${insight.severity}`} aria-label={SEVERITY_LABEL[insight.severity]}>
        {SEVERITY_GLYPH[insight.severity]}
      </span>
      <div className="body">
        <div className="title">{insight.title}</div>
        <div className="detail">{insight.detail}</div>
        {insight.action && <div className="action">{insight.action}</div>}
      </div>
      {insight.impact !== null && insight.impact > 0 && (
        <div className="impact">
          {money(insight.impact, true)}
          <small>per {insight.impact_period}</small>
        </div>
      )}
      {onDismiss && (
        <button className="btn-ghost btn" title="Dismiss" onClick={() => onDismiss(insight.key)}>
          ✕
        </button>
      )}
    </div>
  );
}

/**
 * Horizontal magnitude bars with the value printed on every row. The direct
 * labels are what let the lighter series colours be used on the light surface.
 */
export function BarList({
  rows,
  max,
  formatValue = (v: number) => money(v, true),
}: {
  rows: { label: string; value: number; color: string; sub?: string }[];
  max?: number;
  formatValue?: (value: number) => string;
}) {
  const ceiling = max ?? Math.max(...rows.map((r) => r.value), 1);
  return (
    <div>
      {rows.map((row, index) => (
        // Labels aren't guaranteed unique (two assets can share a name).
        <div className="bar-row" key={`${row.label}-${index}`}>
          <div className="bar-label" title={row.label}>
            <span className="swatch" style={{ background: row.color }} />
            {row.label}
            {row.sub && <span className="muted tiny"> · {row.sub}</span>}
          </div>
          <div className="bar-track">
            <div
              className="bar-fill"
              style={{ width: `${Math.max((row.value / ceiling) * 100, 1)}%`, background: row.color }}
            />
          </div>
          <div className="bar-value">{formatValue(row.value)}</div>
        </div>
      ))}
    </div>
  );
}

type TooltipEntry = { name?: string; value?: number | string; color?: string; dataKey?: string };

export function ChartTooltip({
  active,
  payload,
  label,
  labelFormatter,
  valueFormatter = (v: number) => money(v),
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string;
  labelFormatter?: (label: string) => string;
  valueFormatter?: (value: number) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tooltip">
      {label !== undefined && (
        <div className="t-label">{labelFormatter ? labelFormatter(String(label)) : label}</div>
      )}
      {payload.map((entry) => (
        <div className="t-row" key={String(entry.dataKey ?? entry.name)}>
          <span className="t-name">
            <span className="swatch" style={{ background: entry.color }} />
            {entry.name}
          </span>
          <span className="t-value">
            {typeof entry.value === "number" ? valueFormatter(entry.value) : entry.value}
          </span>
        </div>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div className="legend">
      {items.map((item) => (
        <span key={item.label}>
          <span className="swatch" style={{ background: item.color }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}
