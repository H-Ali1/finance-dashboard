# Changelog

Home Assistant shows this when an update is available. Releases are added
automatically by the CI workflow.

## 1.1.3

- Pull requests only: lint, secret scan, auto-merge and CodeRabbit (#7)

## 1.1.2

- TrueLayer connector for HSBC, Amex and other UK banks
- Starling: read transaction history in windows Starling accepts

## 1.1.1

- Trading 212 key + secret, second account; safer statement imports

## 1.1.0

- First release from GitHub, with images built and tested by CI.
- Restarts itself when unhealthy, retries failed syncs, backs up daily, and
  recovers from a damaged database.
- Versioned database migrations, current dependencies, automated tests.
