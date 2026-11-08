# Making changes

`main` only changes through pull requests. Every merge to `main` that touches
the app is released to Home Assistant, so the checks below are the gate.

1. **Branch** from an up-to-date `main`:
   `git switch main && git pull && git switch -c fix/what-it-does`.
2. **Commit and push** the branch, then open a pull request into `main`. Its
   title becomes the commit on `main` and the changelog line Home Assistant
   shows, so describe the change for the user.
3. **Checks run** on the pull request (`.github/workflows/ci.yml`):

   | Check | What it does |
   |---|---|
   | Backend tests | `pytest` |
   | Frontend typecheck and build | `tsc` and the Vite build |
   | Lint | `ruff check`, `ruff format --check`, ESLint |
   | Secret scan | Fails on committed `.env`, database or key files, and runs gitleaks over every commit |
   | CodeRabbit | An automated review; its comments must be resolved |

4. **It merges itself.** Auto-merge is switched on for every pull request
   (`.github/workflows/auto-merge.yml`). Once the checks pass and every review
   conversation is resolved, GitHub squashes it into `main` and deletes the
   branch. Then CI releases it.

Drafts are left alone until they're marked ready. Dependabot's minor and patch
updates go through the same checks and merge themselves; major versions wait
for a person.

## Running the checks locally

From the repository root:

```bash
backend/.venv/Scripts/python -m pip install -r backend/requirements-dev.txt
```

```bash
backend/.venv/Scripts/python -m ruff check . && backend/.venv/Scripts/python -m ruff format --check .
```

```bash
backend/.venv/Scripts/python -m pytest backend/tests -q
```

```bash
cd frontend && npm run lint && npx tsc --noEmit && npm run build
```

`ruff format .` and `ruff check --fix .` fix most findings themselves. On macOS
and Linux the venv paths are `.venv/bin/` rather than `.venv/Scripts/`.

## Secrets

Credentials never go in the repository: they live in `backend/.env` (ignored by
git) or the Home Assistant App's Configuration tab. If the secret scan flags
something that isn't a secret, check it, then add its fingerprint (printed in
the scan log) to `.gitleaksignore` with a comment saying why.
