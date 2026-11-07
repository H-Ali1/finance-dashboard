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
    trading212_api_key: str = ""
    trading212_base_url: str = "https://live.trading212.com/api/v0"

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
    essential_categories: str = (
        "Groceries,Bills & Utilities,Rent & Mortgage,Insurance,Transport,Fuel,Health & Fitness"
    )

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
        return bool(self.trading212_api_key)


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
    return {
        key: value
        for key, value in data.items()
        if key in Settings.model_fields and value not in (None, "")
    }


@lru_cache
def get_settings() -> Settings:
    return Settings(**_app_options())


settings = get_settings()
