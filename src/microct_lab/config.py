"""Configuration. Every path here points OUTSIDE the repo and is set via .env."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent          # src/microct_lab -> src -> <repo>
DEFAULT_SCRIPTS_DIR = REPO_ROOT / "scripts"
WEB_DIR = PACKAGE_DIR / "web"
# Absolute path so the .env is found no matter what the process CWD is (uvicorn,
# the worker, tests, or a service manager may launch from anywhere).
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MICROCT_", env_file=str(ENV_FILE), extra="ignore",
        protected_namespaces=(),
    )

    # External, machine-specific locations (kept out of version control):
    data_root: Path = Path.home() / "microct_lab_data" / "datasets"
    results_root: Path = Path.home() / "microct_lab_data" / "results"
    models_root: Path = Path.home() / "microct_lab_data" / "models"
    state_dir: Path = Path.home() / "microct_lab_data" / "state"

    # Shared NAS ("Ultron") root as mapped ON THIS MACHINE. Dataset/analysis paths
    # are stored NAS-relative so they survive a different drive letter per machine;
    # they resolve against this root. Empty -> NAS features fall back to data_root.
    nas_root: Optional[Path] = None
    # Root that holds analysis folders (R code + figures). Defaults to nas_root/Analyses.
    analyses_root: Optional[Path] = None

    db_url: Optional[str] = None
    segment_script: Path = DEFAULT_SCRIPTS_DIR / "segment_microct.py"
    python_exe: Optional[str] = None

    host: str = "127.0.0.1"
    port: int = 8000
    default_device: str = "auto"
    poll_seconds: float = 2.0

    # --- Remote frontend access -------------------------------------------------
    # A frontend hosted elsewhere (e.g. a Vercel-deployed Next.js build) runs in
    # the user's browser and calls THIS server on localhost. Its origin must be
    # allowed explicitly; comma-separated, no trailing slash. Example:
    #   MICROCT_ALLOWED_ORIGINS=https://microctweb.vercel.app
    allowed_origins: str = ""
    # Serve a different frontend build from "/" instead of the bundled SPA.
    # Point this at the Next.js static export (`microctweb/out`) to run the new
    # UI same-origin — no CORS, no Private Network Access preflight, works
    # offline, and works when a hosted deployment is unavailable. Empty = use the
    # SPA that ships inside the package.
    web_dir: Optional[Path] = None

    # Demand the bearer token from LOCAL callers too. Off by default.
    #
    # The token exists to authenticate callers that are NOT on this machine — a
    # page hosted on Vercel, say. A request from this machine (the bundled UI at
    # "/", another local tool, curl) is already as trusted as the machine itself,
    # and the browser's own origin rules stop a public website from pretending to
    # be local: it always attaches its real Origin to a cross-origin request.
    # So local callers are exempt, which keeps the local dashboard zero-config
    # while remote access still requires the secret.
    #
    # Set this to true to require the token from everyone, including localhost.
    require_token_local: bool = False

    # Shared secret required in `Authorization: Bearer <token>` on /api/*.
    # REQUIRED whenever allowed_origins names a non-local origin: it is what stops
    # an arbitrary website from driving this API, and because it is a custom header
    # it also forces a CORS preflight on every request (so no "simple request" can
    # mutate state without passing the origin check first).
    api_token: Optional[str] = None

    @property
    def extra_origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def remote_origins(self) -> list[str]:
        """Allowed origins that are NOT localhost — these are the ones that make
        an auth token mandatory."""
        import re
        local = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")
        return [o for o in self.extra_origins if not local.match(o)]

    @property
    def thumbs_dir(self) -> Path:
        return self.state_dir / "thumbnails"

    @property
    def frontend_dir(self) -> Path:
        """Directory served at "/". Falls back to the bundled SPA if the
        configured one does not exist, so a stale path cannot take the UI down."""
        if self.web_dir and Path(self.web_dir).is_dir():
            return Path(self.web_dir)
        return WEB_DIR

    @property
    def analyses_dir(self) -> Path:
        if self.analyses_root:
            return Path(self.analyses_root)
        if self.nas_root:
            return Path(self.nas_root) / "Analyses"
        return Path(self.state_dir) / "analyses"

    @property
    def nas_base(self) -> Path:
        """Root that NAS-relative paths resolve against on this machine."""
        return Path(self.nas_root) if self.nas_root else Path(self.data_root)

    @property
    def database_url(self) -> str:
        if self.db_url:
            return self.db_url
        return f"sqlite:///{(self.state_dir / 'registry.db').as_posix()}"

    @property
    def python(self) -> str:
        return self.python_exe or sys.executable

    def ensure_dirs(self) -> None:
        for d in (self.data_root, self.results_root, self.models_root,
                  self.state_dir, self.thumbs_dir):
            Path(d).mkdir(parents=True, exist_ok=True)


settings = Settings()
