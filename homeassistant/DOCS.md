# Finance Dashboard

Spending, budgets, subscriptions, cash flow and net worth across your own
accounts, in the Home Assistant sidebar. It runs as its own container next to
Home Assistant: if it stops or crashes, Home Assistant carries on.

## Installing

1. In Home Assistant: **Settings → Apps → App store → ⋮ → Repositories**, add

   ```
   https://github.com/H-Ali1/finance-dashboard#ha-release
   ```

2. **Finance Dashboard** appears in the App store. Open it and press
   **Install**. The image is already built, so this only downloads it.

3. Fill in the **Configuration** tab (below). On the **Info** tab, turn on
   **Start on boot**, **Watchdog**, **Auto update** and **Show in sidebar**,
   then **Start**.
   - **Watchdog** restarts the App if it crashes or stops answering.
   - **Auto update** installs new releases on its own.

## Configuration

| Option | What it does |
|---|---|
| Starling personal access token | From developer.starlingbank.com, Personal access. Tick only `account:read`, `account-list:read`, `balance:read`, `transaction:read`, `savings-goal:read` and `space:read`. |
| Trading 212 API key and secret | Trading 212 app, Settings, API, Generate API key, with the "account" and "portfolio" read permissions. The secret is shown once. |
| Trading 212 API key and secret (second account) | Optional: a key from your other account, if you have both an Invest account and a Stocks ISA. |
| Trading 212 API address | Leave blank for the live account. |
| TrueLayer client ID and secret | For HSBC, Amex and other UK banks; see below. |
| Sync every (hours) | Automatic sync interval. Default 6. `0` turns it off; the Sync button still works. |
| Essential categories | Comma-separated. Leave blank for the default list. |

Restart the App after changing options.

## HSBC, Amex and other UK banks (TrueLayer)

TrueLayer reads UK banks through open banking. It only signs contracts with
businesses, but an app switched to Live in its Console still connects real
banks, showing a "test mode" notice on the approval screen. TrueLayer doesn't
support this use and could stop it at any time. Your transactions pass through
TrueLayer on their way to the dashboard.

1. Sign up at [console.truelayer.com](https://console.truelayer.com) and switch
   your app to **Live**. The Live client ID doesn't start with `sandbox-`.
2. Under the app's **Allowed redirect URIs**, add
   `https://console.truelayer.com/redirect-page`.
3. Paste the client ID and secret into the **Configuration** tab and restart
   the App.
4. On the dashboard's **Connections** tab, choose a bank and press
   **Approve at** *your bank*. Approve read-only access on the bank's screen. TrueLayer then
   shows a code: paste it back within 5 minutes and press **Finish**. The
   first read fetches as much history as the bank allows, up to two years.

Things to know:

- **90 days.** Access ends 90 days after you approve it. The dashboard warns
  you under **Needs fixing** in the last 10 days. Press **Connect again** next
  to the bank: the same accounts and transactions are kept, not duplicated.
- **Four refreshes a day.** UK banks allow four automatic refreshes a day. The
  schedule reads each TrueLayer bank at most every 8 hours, leaving one for
  the **Sync** button.
- **Not every bank.** TrueLayer's UK list has HSBC, first direct and American
  Express, but not Chase UK. For Chase, export a CSV and use
  **Connections → Import a statement**.
- **Disconnecting** a bank on the Connections tab also tells TrueLayer to drop
  its access. You can revoke it from your bank's app too.

## Access and security

- The dashboard is only reachable through the Home Assistant sidebar. No port
  is published, and the app refuses any connection that doesn't come through
  Home Assistant's ingress proxy, so it sits behind Home Assistant's own login.
  Remote access works however you already reach Home Assistant.
- The sidebar panel is visible to administrator accounts only.

## Data and backups

- Everything is stored in the App's private `/data` folder: the SQLite
  database and the options above.
- **The App backs itself up.** A copy of the database is saved every day to
  `/data/backups`, and the last 7 are kept. Another copy is taken before any
  update that changes the database's structure.
- **A damaged database recovers on its own.** If the database is damaged (a
  bad shutdown, failing storage), it's moved aside at the next start and the
  newest good copy is restored. Starling and Trading 212 catch up at the next
  sync. A warning under **Needs fixing** tells you when and what happened;
  re-import any statement files from after that copy's date.
- **Home Assistant backups include the database and your API tokens**
  (including each TrueLayer bank's access token), so keep backup encryption
  on. They skip the App's own daily copies. The App stops
  for the few seconds a backup takes, so the database is copied in a
  consistent state.
- **Failed syncs retry on their own:** after 15 minutes, then 30, 60, ...
  until the normal interval.

## Updating

Every change merged into the repository's `main` branch is tested and
released by CI. Home Assistant picks up new versions within a few hours.
With **Auto update** on they install themselves; otherwise press **Update**.
The changelog shows what changed.

## Known limitations

- TrueLayer banks rely on a mode TrueLayer doesn't support for personal use.
- Chase UK isn't on TrueLayer's list, so it's a statement import. The
  importer reads CSV, so a bank that only offers PDF statements can't be
  imported yet.
