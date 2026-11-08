# Finance Dashboard

A local budgeting and net-worth dashboard for UK personal finances. It pulls
transactions from Starling, from HSBC, Amex and other UK banks through
TrueLayer, and from statement files for anything else (Chase). It values your
Trading 212 account, tracks manual assets like the car and your pension, and
tells you what's worth fixing.

Everything runs on your machine. The database is a SQLite file in `backend/`,
credentials live in `backend/.env`, and the only outbound network calls are to
the bank APIs you configure yourself.

## What it does

- **Connects to your money.** Starling directly, HSBC, Amex and other UK
  banks through TrueLayer, Trading 212 for investments, and a CSV statement
  importer for anything else.
- **Categorises automatically** using merchant keywords, card MCC codes and the
  provider's own labels — with your own rules taking precedence.
- **Detects internal transfers**, so paying the Amex off from your current
  account doesn't look like a large purchase *and* a large income.
- **Finds recurring payments** by cadence and amount stability rather than a
  fixed brand list, then separates cancellable subscriptions from fixed bills
  and flags price rises, duplicates across cards, and payments that stopped.
- **Projects the month honestly.** Fixed commitments are projected from what's
  actually scheduled; everyday spending is blended between your pace this month
  and a typical month, so day 2 doesn't predict fifteen rents.
- **Tracks net worth** across accounts, investments, pension and the car, with a
  daily history so you can see the trend.
- **Tells you what needs fixing** — a rules engine producing plain-English
  findings, each with a pound figure attached and a stable key so dismissing one
  makes it stay dismissed.

## Quick start

Try it with realistic sample data before connecting anything real:

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
```

```bash
cd backend && .venv/Scripts/python seed_demo.py --reset
```

```bash
cd backend && .venv/Scripts/python -m uvicorn app.main:app --reload
```

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. The API docs are at http://localhost:8000/docs.

On macOS/Linux the venv paths are `.venv/bin/` rather than `.venv/Scripts/`.

## Connecting real accounts

Copy `backend/.env.example` to `backend/.env` and fill in whichever providers
you want. Restart the backend after editing it, then use the **Connections** tab.

### Starling — direct, free, five minutes

1. Sign in at [developer.starlingbank.com](https://developer.starlingbank.com).
2. Create a **personal access token** with only these read scopes:
   `account:read`, `account-list:read`, `balance:read`, `transaction:read`,
   `savings-goal:read` and `space:read`. Leave every `:create`/`:edit` scope
   unticked. The token doesn't expire; revoke it in the portal to stop access.
3. Set `STARLING_ACCESS_TOKEN` in `.env`.

Savings spaces come through as separate accounts so they count toward net worth.

### HSBC, Amex and other UK banks — TrueLayer

None of these offers a personal API, so they're read through TrueLayer, an
open banking provider. TrueLayer only signs contracts with businesses, but an
app switched to Live in its Console still connects real banks, with a "test
mode" notice on the approval screen. TrueLayer doesn't support that use and
could end it at any time. Your transactions pass through TrueLayer on the way.

1. Sign up at [console.truelayer.com](https://console.truelayer.com) and switch
   the app to **Live** (the client ID doesn't start with `sandbox-`).
2. Add `https://console.truelayer.com/redirect-page` to its **Allowed redirect
   URIs**. TrueLayer's page shows the code you paste back, so the dashboard
   never has to be reachable from the internet.
3. Set `TRUELAYER_CLIENT_ID` and `TRUELAYER_CLIENT_SECRET` (in Home Assistant:
   the App's Configuration tab).
4. **Connections** tab → choose a bank → **Approve at** *your bank* → approve
   on the bank's screen → paste the code → **Finish**, within 5 minutes.

- **One connection per bank.** It holds TrueLayer's refresh token for that
  bank, in the database. The access token is never stored, and nothing is sent
  to the browser.
- **History.** The first read asks for two years. Many banks release more than
  90 days only in the minutes after you approve; one that refuses is asked for
  a year, then 89 days.
- **90 days.** Access then ends. **Needs fixing** warns you 10 days before.
  **Connect again** reuses the same accounts: they're matched by a hash of the
  account number, and transactions by TrueLayer's stable ID, so nothing
  doubles.
- **Four refreshes a day.** UK banks allow four refreshes a day when you're
  not present. The schedule reads each TrueLayer bank at most every 8 hours,
  leaving one for the **Sync** button. A sync you start yourself passes your
  public IP when a proxy provides one, which lifts the limit. If the limit is
  hit anyway, the bank is skipped until the next sync, not marked broken.
- **Chase UK** isn't on TrueLayer's list. Use statement imports for it.

### Chase — statement imports

Export a CSV and use the statement importer below. Everything downstream
(categories, budgets, subscriptions, net worth) works the same as for a live
feed.

- **Chase UK:** in the app, profile → **Statements** → create a statement for
  up to 90 days → CSV.
- **HSBC** and **Amex** can be imported the same way if you'd rather not use
  TrueLayer. HSBC: online banking → the account → **Search and filter** →
  **Download** → CSV. Amex: **Statements & Activity** → **Download** → CSV,
  into an account created as a **Credit card**.

### Trading 212

In the Trading 212 app: **Settings → API → Generate API key**, with only the
**account** and **portfolio** read permissions. You get a key and a secret; the
secret is shown once. Set `TRADING212_API_KEY` and `TRADING212_API_SECRET` (in
Home Assistant: the App's Configuration tab).

Each key belongs to one account. If you have both an Invest account and a
Stocks ISA, generate a key from the other one too and set it as
`TRADING212_API_KEY_2` / `TRADING212_API_SECRET_2`.

Each sync fetches the account value, cash and holdings. Holding values and
profit/loss are in pounds, and share prices in the share's own currency.

### Statement imports

Connections → **Import a statement**. Pick an account, or create one and choose
its type (current, savings or credit card), then choose the file.

- **Formats.** Starling and Amex exports are recognised from their headers.
  Anything else is read with a generic mapping: a date, a description, and
  either one signed amount or separate paid-out / paid-in columns. A file
  with no header row works if it has exactly one column of amounts;
  otherwise you're asked to add a header line.
- **Signs.** Money out should come out negative. Amex is flipped
  automatically, and so is any statement imported into a credit-card account
  where most rows are positive. The result says what was read: the dates
  covered and the totals in and out. If they look swapped, tick **Flip signs**
  and import the same file again; that corrects the rows instead of adding
  them twice.
- **Overlaps.** Re-importing an overlapping file is safe: rows are matched on
  date, amount and description, so duplicates are skipped rather than doubled.
- **Balances.** Statement files carry a running balance that isn't read (the
  column differs between banks), so there's a **closing balance** field to
  keep net worth accurate for imported accounts.

## Running it permanently

The `Dockerfile` builds one image that serves the API and the built UI from a
single process on port 8099, keeps all state in `/data`, and runs its own
background jobs: a sync every `SYNC_INTERVAL_HOURS` (default 6; `0` turns it
off) and a daily database backup. `GET /api/health` is the liveness check for
every platform below.

### On Home Assistant (recommended)

The dashboard runs as a Home Assistant **App**: its own container next to Home
Assistant, opened from the sidebar behind HA's login. CI builds and tests the
images, and Home Assistant installs and updates them from this repository:

1. **Settings → Apps → App store → ⋮ → Repositories**, add
   `https://github.com/H-Ali1/finance-dashboard#ha-release`
2. Install **Finance Dashboard**, fill in its **Configuration** tab, and on its
   **Info** tab turn on **Start on boot**, **Watchdog**, **Auto update** and
   **Show in sidebar**.

Full steps, options and backup notes are in
[homeassistant/DOCS.md](homeassistant/DOCS.md), which also shows on the App's
Documentation tab. To try unpublished changes on the device itself,
`python scripts/package_ha_app.py` writes a copy to
`build/ha-app/finance_dashboard/` that HA builds from source; copy it into
`/addons`.

### Plain Docker (Compose)

```bash
docker compose up -d --build
```

Then open http://127.0.0.1:8099. [docker-compose.yml](docker-compose.yml) restarts
the container after a crash or reboot, checks its health every minute, reads
credentials from `backend/.env`, and keeps data in the `finance-data` volume.
The app has no login of its own, so the port is bound to localhost. Before
opening it further, put an authenticating proxy in front. If that proxy has a
fixed address, set `ALLOWED_CLIENT_IPS` to it so nothing else can connect.

### Kubernetes

The same image works as a pod:
- **Run exactly one replica**, with the `Recreate` update strategy. SQLite
  allows one writer, and two pods on one volume would corrupt it.
- **Mount a persistent volume at `/data`.**
- **Pass credentials as environment variables** from a Secret
  (`STARLING_ACCESS_TOKEN`, `TRADING212_API_KEY`).
- **Probe `/api/health` on port 8099** for liveness and readiness.
- **Put authentication in front of the Service.**

## Keeping it healthy

**What happens on its own:**
- **Crashes and hangs.** If the process exits, or `/api/health` stops answering
  (the database or the background jobs have stopped), the platform restarts it:
  HA's watchdog, Docker's restart policy, or a Kubernetes liveness probe.
- **Failed syncs.** A provider being down or a network blip is retried after 15
  minutes, then 30, 60, ... up to the normal interval.
- **Backups.** A copy of the database goes to `/data/backups` every day, and the
  last `BACKUP_KEEP_DAYS` (default 7) are kept. Another copy is taken before
  every schema upgrade.
- **A damaged database.** At startup the database is checked. If it's damaged,
  it's moved aside (never deleted) and the newest good backup takes its place.
  Bank feeds catch up at the next sync, and a warning appears under
  **Needs fixing**.
- **App updates.** Schema changes run automatically and in order
  ([backend/app/migrations.py](backend/app/migrations.py)). A database from a
  newer build is refused rather than damaged.

**How changes reach Home Assistant** ([.github/workflows/ci.yml](.github/workflows/ci.yml)):
1. Every push and pull request runs the backend tests and the frontend
   typecheck and build.
2. A push to `main` that changes the app also releases it:
   - builds the image for amd64 and for aarch64 (the HA Green);
   - smoke-tests each image the way HA runs it, so a broken one is never
     published;
   - pushes them to `ghcr.io/h-ali1/{arch}-finance-dashboard`;
   - publishes the App definition, with the new version and a changelog entry,
     to the `ha-release` branch.
3. Home Assistant checks that branch every 3 hours. With **Auto update** on, it
   installs the update within about a day.

The patch number goes up by one on every release. For a bigger jump, raise
`version` in `homeassistant/config.yaml` (e.g. to `1.2.0`). Docs-only and
test-only changes don't trigger a release. To release anyway, run the workflow
from the Actions tab with **force release** ticked.

**Dependencies.** [Dependabot](.github/dependabot.yml) opens one grouped pull
request per ecosystem each month, plus security fixes as soon as they're
published. CI tests each one; merging it releases it. Python versions live in
`backend/requirements.in`. `backend/requirements.txt` is generated from it with
every package pinned, so a rebuild never picks up something untested. To
regenerate it by hand:

```bash
cd backend && docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c "pip install -q pip-tools && pip-compile --strip-extras --no-emit-index-url requirements.in"
```

To run the tests locally:

```bash
cd backend && .venv/Scripts/python -m pip install -r requirements-dev.txt && .venv/Scripts/python -m pytest tests
```

## Notes on the analysis

**Sign convention.** `amount_minor` is signed pence from your point of view:
negative is money out. This holds for credit cards too, so a purchase on Amex is
negative and spend analysis never special-cases an account type. Some issuers
report card debt as a positive balance; the per-account **Flip signs** toggle
handles those.

**Typical vs average.** A holiday drags a mean around for months, so the runway,
projections and the "typical month" figures use a **median** across complete
months. The mean is still reported alongside it.

**Month-to-date comparisons.** Comparing three days of spending against whole
previous months would say every category had collapsed, so category trends
compare month-to-date against the same stretch of each previous month, and trend
findings are suppressed in the first week.

**Recurring detection** groups by account *and* merchant — the same service on
two cards is two subscriptions, which is exactly the case worth flagging. A
payment qualifies when at least three charges land at a regular cadence with a
stable amount (median absolute deviation within 25–30%).

**Anomalies** use median + MAD rather than mean + standard deviation, so one big
transaction doesn't inflate the threshold that would have caught it. Scheduled
payments are excluded — rent is much larger than a typical bill every month, and
that's expected.

## Layout

```
backend/
  app/
    providers/    starling, trading212, truelayer, csv_import — normalise to RawAccount/RawTransaction
    services/     categorise, transfers, sync, scheduler (background jobs), backup (copies + recovery)
    migrations.py versioned schema changes
    analytics/    spending, recurring, cashflow, anomalies, networth, insights
    routers/      connections, accounts, transactions, analytics
  seed_demo.py    14 months of realistic fake data
frontend/
  src/components/ one file per tab, plus shared ui.tsx
  src/lib/        api client, formatting, the categorical colour assignment
backend/tests/     API, serving/guard, migrations, backups, recovery, retries
.github/          CI workflow, release helper, Dependabot
homeassistant/    the Home Assistant App definition, option labels and its docs
scripts/          package_ha_app.py — assembles the App folder for /addons
Dockerfile        one image for Home Assistant, Docker and Kubernetes
docker-compose.yml  restart policy + health check for running outside HA
```

## Privacy

The dashboard sends nothing anywhere except requests to the providers you
configure. Starling and Trading 212 are read directly. Banks read through
TrueLayer pass through TrueLayer on the way, as with any open banking app.
There is no telemetry, no cloud sync and no third-party analytics. The app has no login of its own. Locally the backend binds
to localhost. On Home Assistant it only accepts connections from HA's ingress
proxy, so HA's login protects it. Anywhere else, put it behind something that
requires authentication.

`.env` and `*.db` are gitignored. Revoke tokens from the provider's own app or
dashboard: deleting a connection here does not revoke a Starling or Trading 212
key. Disconnecting a TrueLayer bank here does ask TrueLayer to drop its access.
