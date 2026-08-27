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

    # How many segmentation runs the worker may execute CONCURRENTLY, each
    # pinned to its own GPU via CUDA_VISIBLE_DEVICES. 1 = today's serial
    # behavior. This is only the .env default — the UI writes a runtime
    # override into the app_settings table (key "parallel_gpu_runs") that both
    # server and worker read, so no restart is needed.
    parallel_gpu_runs: int = 1

    # --- Interim threshold BV/TV (see bvtv.py) -----------------------------------
    # Bone threshold in Hounsfield units. The scans are 8-bit with HU
    # calibration OFF, so this is converted per scan through the _rec.log's
    # "CS to Image Conversion" window using mu_water below.
    bvtv_threshold_hu: float = 800.0
    # Linear attenuation of water (1/mm) for the HU->attenuation conversion.
    # Default calibrated so 800 HU lands at grey ~77/255 on the R2 window
    # (Max CS 0.132622), matching the empirical grey-80 threshold the perios
    # pipeline uses. Re-pin against a CTAn export when one is available.
    mu_water: float = 0.0222

    # How long a job may sit in "canceling" before the API reports it as stuck.
    # A cancel is a two-step handshake: the API flips the row to "canceling" and
    # the WORKER kills the subprocess and writes the terminal status. If no worker
    # is running (or it died in between) nobody ever completes step two and the
    # row sits there forever, blocking archive. Past this many seconds the API
    # sets `stuck` on the row so the UI can offer a forced cancel. Advisory only —
    # nothing is changed automatically.
    stuck_after_seconds: float = 120.0

    # --- Morphometry anatomy gate -----------------------------------------------
    # The vendored digitpipe_v5 pipeline is built for mouse terminal phalanx at
    # ~4um. On other anatomy it does not fail — it returns confident, plausible,
    # wrong numbers (see morphqc.py). So refuse to measure a dataset that is not
    # marked as the right anatomy, rather than only warning afterwards.
    morph_require_anatomy: bool = True
    # Comma-separated tags, any ONE of which marks a dataset as measurable.
    morph_anatomy_tags: str = "phalanx"

    # --- Pre-measurement mask QC (see maskqc.py) ---------------------------------
    # Checks the MASK before the pipeline runs, not the numbers afterwards. Socket
    # detection alone is ~25 min of CPU per case, so a mask that cannot yield a
    # meaningful measurement is worth catching in the seconds it takes to count
    # voxels. Blocking findings refuse the job; warnings are recorded and shown.
    morph_mask_qc: bool = True
    # Measure anyway when the voxel spacing is not the ~4um digitpipe_v5's geometry
    # assumes. The mm values stay correctly scaled, but the pipeline's downsample
    # factor and socket erosion radii are voxel counts tuned for a 4um grid, so
    # they span the wrong physical distance — a structurally wrong segmentation
    # with right-looking units. Off by default; a measurement made with this on is
    # stamped as such in its record.
    morph_allow_spacing_mismatch: bool = False

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
    def morph_anatomy_tag_list(self) -> list[str]:
        """The anatomy tags, lowercased and stripped, for case-insensitive matching
        against Dataset.tags."""
        return [t.strip().lower() for t in self.morph_anatomy_tags.split(",") if t.strip()]

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
