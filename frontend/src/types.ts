/** Mirrors the FastAPI responses in backend/app/routers. */

export type Severity = "critical" | "warning" | "info" | "good";

export interface Insight {
  key: string;
  severity: Severity;
  title: string;
  detail: string;
  impact: number | null;
  impact_period: string;
  action: string | null;
  area: string;
}

export interface Headline {
  status: "good" | "watch" | "bad";
  message: string;
  month: string;
  net_worth: number;
  projected_net: number;
  counts: Record<Severity, number>;
}

export interface NetWorthItem {
  id: string;
  name: string;
  institution: string;
  kind: string;
  value: number;
  source: string;
  updated_at: string | null;
}

export interface NetWorth {
  as_of: string;
  assets: number;
  liabilities: number;
  net: number;
  liquid: number;
  investments: number;
  pension: number;
  card_debt: number;
  asset_items: NetWorthItem[];
  liability_items: NetWorthItem[];
}

export interface NetWorthPoint {
  date: string;
  net: number;
  assets: number;
  liabilities: number;
}

export interface Upcoming {
  merchant: string;
  amount: number;
  cadence: string;
  next_expected: string;
  days_until_due: number;
  account: string;
  category: string;
}

export interface Cashflow {
  month: string;
  days_elapsed: number;
  days_in_month: number;
  income_so_far: number;
  spend_so_far: number;
  net_so_far: number;
  daily_spend_rate: number;
  fixed_so_far: number;
  variable_so_far: number;
  projected_variable: number;
  projected_spend: number;
  projected_income: number;
  projected_net: number;
  committed_upcoming: number;
  upcoming: Upcoming[];
  average_income: number;
  average_spend: number;
  typical_income: number;
  typical_spend: number;
  typical_essential: number;
  typical_discretionary: number;
  typical_variable: number;
  typical_fixed: number;
  months_of_history: number;
  savings_rate: number | null;
  current_savings_rate: number | null;
  liquid: number;
  runway_months: number | null;
  history: {
    month: string;
    income: number;
    spend: number;
    essential: number;
    discretionary: number;
    fixed: number;
    variable: number;
    net: number;
  }[];
  burn?: { date: string; spend: number; cumulative: number }[];
}

export interface CategoryRow {
  category: string;
  total: number;
  transactions: number;
  share: number;
}

export interface SpendingBreakdown {
  start: string;
  end: string;
  total_spend: number;
  categories: CategoryRow[];
}

export interface MonthlyTrend {
  months: {
    month: string;
    income: number;
    spend: number;
    net: number;
    savings_rate: number | null;
  }[];
}

export interface CategoryTrend {
  months: string[];
  current_month: string;
  comparison: string;
  through_day: number | null;
  categories: {
    category: string;
    current: number;
    average: number;
    change_pct: number | null;
    change: number;
    series: { month: string; total: number }[];
  }[];
}

export interface BudgetStatus {
  month: string;
  days_elapsed: number;
  days_in_month: number;
  total_spent: number;
  budgets: {
    category: string;
    limit: number;
    spent: number;
    remaining: number;
    used_pct: number | null;
    typical: number;
    projected: number;
    on_track: boolean;
  }[];
  unbudgeted: { category: string; spent: number }[];
}

export interface Subscription {
  merchant: string;
  merchant_key: string;
  family: string;
  category: string;
  type: "bill" | "subscription";
  cadence: string;
  amount: number;
  typical_amount: number;
  annual_cost: number;
  monthly_cost: number;
  occurrences: number;
  first_seen: string;
  last_seen: string;
  next_expected: string;
  days_until_due: number;
  status: "active" | "overdue" | "stopped";
  price_change: {
    from: number;
    to: number;
    pct: number;
    direction: "up" | "down";
    annual_impact: number;
  } | null;
  account: string;
  account_id: number;
  institution: string;
  total_paid: number;
  duplicate_of?: string[];
}

export interface SubscriptionSummary {
  count: number;
  active_count: number;
  monthly_total: number;
  annual_total: number;
  subscription_count: number;
  subscription_monthly: number;
  subscription_annual: number;
  bills_monthly: number;
  bills_annual: number;
  price_rises: Subscription[];
  stopped: Subscription[];
  duplicates: Subscription[];
}

export interface Account {
  id: number;
  name: string;
  institution: string;
  provider: string;
  kind: string;
  currency: string;
  balance: number;
  available: number | null;
  is_liability: boolean;
  include_in_networth: boolean;
  is_active: boolean;
  flip_sign: boolean;
  updated_at: string | null;
  holdings: {
    ticker: string;
    name?: string;
    quantity: number;
    current_price: number;
    /** Currency of current_price (the share's own, e.g. USD); value and ppl are in GBP. */
    price_currency?: string;
    value: number;
    ppl: number;
  }[];
}

export interface ManualAsset {
  id: number;
  name: string;
  kind: "asset" | "liability";
  category: string;
  value: number;
  currency: string;
  notes: string | null;
  updated_at: string | null;
}

export interface Transaction {
  id: number;
  date: string;
  amount: number;
  currency: string;
  merchant: string;
  description: string;
  category: string;
  category_source: string;
  status: string;
  is_transfer: boolean;
  excluded: boolean;
  notes: string | null;
  account: string;
  account_id: number;
  institution: string;
}

export interface UncategorisedMerchant {
  merchant: string;
  merchant_key: string;
  example: string;
  last_date: string;
  account: string;
  transactions: number;
  total: number;
}

export interface UncategorisedList {
  days: number;
  total: number;
  transactions: number;
  merchant_count: number;
  merchants: UncategorisedMerchant[];
}

export interface Connection {
  id: number;
  provider: string;
  institution_name: string;
  status: string;
  last_synced_at: string | null;
  last_error: string | null;
  account_count: number;
  /** TrueLayer banks: when the 90 days of access end. */
  expires_at: string | null;
  /** TrueLayer banks: TrueLayer's id for the bank, e.g. ob-hsbc. */
  bank_id: string | null;
}

export interface TrueLayerBank {
  provider_id: string;
  name: string;
  logo_url: string | null;
  scopes: string[];
}

export interface ConnectionsResponse {
  connections: Connection[];
  providers: Record<string, { configured: boolean; how: string }>;
}

export interface Anomaly {
  id: number;
  date: string;
  merchant: string;
  category: string;
  amount: number;
  account: string;
  z_score: number;
  severity: Severity;
  reasons: string[];
}

export interface DoubleCharge {
  merchant: string;
  amount: number;
  dates: string[];
  account: string;
  transaction_ids: number[];
}

export interface Dashboard {
  headline: Headline;
  networth: NetWorth;
  networth_history: NetWorthPoint[];
  cashflow: Cashflow;
  spending: SpendingBreakdown;
  trend: MonthlyTrend;
  budgets: BudgetStatus;
  subscriptions: SubscriptionSummary;
  upcoming: Upcoming[];
  insights: Insight[];
  top_merchants: {
    merchant: string;
    category: string;
    total: number;
    transactions: number;
    average: number;
  }[];
}

export interface Health {
  status: string;
  accounts: number;
  transactions: number;
  latest_transaction: string | null;
  providers_configured: Record<string, boolean>;
  base_currency: string;
}
