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
| Starling personal access token | From developer.starlingbank.com, Personal access, with the account, account-identifier, balance, transaction and savings-goal read scopes. |
| Trading 212 API key | Trading 212 app, Settings, API. Read scopes only. |
| Trading 212 API address | Leave blank for the live account. |
| Sync every (hours) | Automatic sync interval. Default 6. `0` turns it off; the Sync button still works. |
| Essential categories | Comma-separated. Leave blank for the default list. |

Restart the App after changing options.

HSBC, Chase and Amex have no live feed yet: export a CSV from each and use
**Connections → Import a statement**.

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
- **Home Assistant backups include the database and your API tokens**, so keep
  backup encryption on. They skip the App's own daily copies. The App stops
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

- The Trading 212 connector still uses Trading 212's older single-key
  authentication. Their current API expects a key and a secret, so syncing it
  may fail until the connector is updated.
