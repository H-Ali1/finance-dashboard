"""Runtime configuration.

Settings come from, highest priority first:
  1. the Home Assistant App's options file (/data/options.json), when present
  2. environment variables (what Docker / Kubernetes deployments use)
  3. backend/.env (local development)

Everything here stays on your machine: credentials live in one of those three
places, the SQLite file holds the data, and nothing is sent anywhere except
directly to the provider APIs you have configured.
"""

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
# Written by the Home Assistant Supervisor from the App's Configuration tab.
DEFAULT_OPTIONS_FILE = "/data/options.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- storage -----------------------------------------------------------
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'finance.db').as_posix()}"

    # --- Starling (personal access token from developer.starlingbank.com) ---
    starling_access_token: str = ""
    starling_base_url: str = "https://api.starlingbank.com"

    # --- Trading 212 (Settings -> API in the app) ---------------------------
    # One key + secret per account; the second pair is for a second account
    # (e.g. Invest and Stocks ISA).
    trading212_api_key: str = ""
    trading212_api_secret: str = ""
    trading212_api_key_2: str = ""
    trading212_api_secret_2: str = ""
    trading212_base_url: str = "https://live.trading212.com/api/v0"

    # --- TrueLayer (console.truelayer.com, app switched to Live) -------------
    # Reads HSBC, Amex and other UK banks through open banking. Each bank you
    # connect is approved on the bank's own screen; the dashboard stores only
    # TrueLayer's refresh token for it, in the database.
    truelayer_client_id: str = ""
    truelayer_client_secret: str = ""
    # Must be listed under Allowed redirect URIs in TrueLayer Console. The
    # default is TrueLayer's own page, which shows the code to paste back here,
    # so the dashboard never has to be reachable from the internet.
    truelayer_redirect_uri: str = "https://console.truelayer.com/redirect-page"
    truelayer_auth_url: str = "https://auth.truelayer.com"
    truelayer_api_url: str = "https://api.truelayer.com"

    # --- app ---------------------------------------------------------------
    frontend_origin: str = "http://localhost:5173"
    # Built UI to serve from the API process. Missing in development, where
    # the Vite dev server serves it instead.
    frontend_dist: str = str(BACKEND_DIR.parent / "frontend" / "dist")
    # Hours between automatic syncs; 0 turns the schedule off.
    sync_interval_hours: float = 6
    # Daily database copies kept in backups/ next to the database.
    backup_keep_days: int = 7
    # Comma-separated client IPs allowed to reach the app. Empty allows all.
    # The Home Assistant App sets this to the Supervisor's ingress proxy, so
    # the dashboard is only reachable through HA's own login.
    allowed_client_ips: str = ""
    base_currency: str = "GBP"
    # Categories treated as unavoidable when computing runway / essential spend.
    essential_categories: str = "Groceries,Bills & Utilities,Rent & Mortgage,Insurance,Transport,Fuel,Health & Fitness"

    @property
    def essential_category_list(self) -> list[str]:
        return [c.strip() for c in self.essential_categories.split(",") if c.strip()]

    @property
    def allowed_client_ip_set(self) -> set[str]:
        return {ip.strip() for ip in self.allowed_client_ips.split(",") if ip.strip()}

    @property
    def starling_configured(self) -> bool:
        return bool(self.starling_access_token)

    @property
    def trading212_configured(self) -> bool:
        return bool(self.trading212_api_key.strip() or self.trading212_api_key_2.strip())

    @property
    def truelayer_configured(self) -> bool:
        return bool(self.truelayer_client_id.strip() and self.truelayer_client_secret.strip())


def _app_options() -> dict:
    """Options saved in the Home Assistant App's Configuration tab, if any."""
    path = Path(os.environ.get("OPTIONS_FILE", DEFAULT_OPTIONS_FILE))
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    # Blank optional fields fall through to the environment / defaults.
    return {key: value for key, value in data.items() if key in Settings.model_fields and value not in (None, "")}


@lru_cache
def get_settings() -> Settings:
    return Settings(**_app_options())


settings = get_settings()
