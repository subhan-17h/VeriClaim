"""Centralised, typed configuration.

Every production tunable lives here. Keeping chunk sizes, retrieval depths, OCR
thresholds, and database endpoints together prevents configuration drift across the
API, the CLI, and the evaluation harness.

Two layers, deliberately separated:

* :class:`Settings` — operational tunables, overridable from the environment or a
  ``.env`` file with the ``VC_`` prefix (e.g. ``VC_CHUNK_SIZE=512``). See ``.env.example``.
* :class:`ModelRouting` — which model serves which task, loaded from ``config.yaml``.
  Model choice is reviewed as a table rather than edited as scattered constants, and
  cost rates live beside the model they price so the gateway's accounting is data-driven.

Secrets are read from the environment only; they never appear in ``config.yaml``,
which is committed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py -> vericlaim/ -> src/ -> project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_ROUTING_PATH = PROJECT_ROOT / "config.yaml"

# Populate the real process environment from .env, once, at import.
#
# pydantic-settings reads .env into Settings, but API keys are consumed straight from
# os.environ by the provider adapters and the tracing wrapper -- keeping secrets out of
# a settings object that gets logged and repr'd. Without this, keys placed in .env are
# invisible to exactly the code that needs them.
#
# override=False so a real environment variable always beats the file, which is what
# lets CI and one-off shell overrides work.
load_dotenv(PROJECT_ROOT / ".env", override=False)


class Settings(BaseSettings):
    """All VeriClaim operational tunables, resolved once at import time."""

    model_config = SettingsConfigDict(
        env_prefix="VC_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Embeddings (local, via Ollama) -----------------------------------
    # Also honours the standard OLLAMA_HOST variable, since the ollama client reads
    # that itself and having the two disagree is a confusing failure.
    ollama_host: str = Field(
        default="http://127.0.0.1:11434",
        validation_alias=AliasChoices("VC_OLLAMA_HOST", "OLLAMA_HOST"),
    )
    # Do not change away from nomic-embed-text without also revisiting the
    # search_document:/search_query: task prefixes in policy/embeddings.py, which are
    # specific to this model. Changing one without the other silently degrades recall.
    embed_model: str = "nomic-embed-text"
    embed_dim: int = 768
    embed_batch_size: int = 32

    # --- Paths ------------------------------------------------------------
    data_dir: Path = PROJECT_ROOT / "data"
    policy_dir: Path = PROJECT_ROOT / "data" / "policies"
    scanned_dir: Path = PROJECT_ROOT / "data" / "scanned"
    spreadsheet_dir: Path = PROJECT_ROOT / "data" / "spreadsheets"
    chroma_dir: Path = PROJECT_ROOT / "chroma_db"
    bm25_dir: Path = PROJECT_ROOT / "bm25_index"
    manifest_path: Path = PROJECT_ROOT / "chroma_db" / "manifest.json"
    collection_name: str = "vericlaim"

    # --- Document parsing -------------------------------------------------
    pdf_parser: Literal["docling", "pypdf"] = "docling"
    docling_artifacts_path: Path = Path.home() / ".cache" / "docling" / "models"

    # --- OCR (scanned source) ---------------------------------------------
    # RapidOCR is ONNX/CPU and offline. The engine is named explicitly rather than
    # left to Docling's OcrAutoOptions, which probes the runtime and therefore
    # resolves differently on different machines.
    ocr_engine: Literal["rapidocr", "easyocr", "tesseract"] = "rapidocr"
    # RapidOcrOptions.lang defaults to ["chinese"]. Leaving it unset is a live bug.
    ocr_lang: tuple[str, ...] = ("english",)
    # Pages scoring below this escalate to the vision tier. Evidence that remains
    # below it after escalation is flagged so synthesis qualifies rather than asserts.
    ocr_confidence_floor: float = 0.60
    ocr_vision_escalation: bool = True
    # Ceiling on the confidence an escalated page may claim. A page that needed a
    # second reading is not a page to then call pristine, and capping below 1.0 keeps
    # its evidence qualified rather than letting a model's self-report promote a
    # degraded scan to the same standing as a clean one.
    ocr_escalated_confidence_cap: float = 0.85
    # Characters per square point of page area. A page below this is treated as
    # scanned and routed to the expensive OCR path. Gating matters in both
    # directions: running full OCR over digital policy PDFs is prohibitively slow,
    # and skipping it on a real scan makes that document invisible.
    #
    # Set from measurement, not intuition. Digital pages in this project's corpus
    # measure 0.00142-0.00230 and image-only scans measure exactly 0.00000, so this
    # sits ~7x below the sparsest real text page with room on both sides. The
    # original 0.005 was above every real page and would have classified the entire
    # policy corpus as scanned.
    scanned_char_density_threshold: float = 0.0002
    # How a scanned document's claim reference is recognised in its corpus-relative
    # path -- "claims/CLM-1001/estimate.pdf" or "CLM-1088_INSPECTION.pdf". The path,
    # not the recognised text: a misread character in a document's own heading would
    # attach its evidence to the wrong matter. Configurable because it encodes a
    # corpus naming convention rather than anything about OCR.
    claim_id_pattern: str = r"CLM-\d{3,}"

    # --- Chunking ---------------------------------------------------------
    chunk_size: int = 400  # approximate tokens
    chunk_overlap: int = 60

    # --- Retrieval --------------------------------------------------------
    retrieval_mode: Literal["dense", "hybrid"] = "hybrid"
    top_k_dense: int = 20
    top_k_bm25: int = 20
    rrf_k: int = 60
    rerank_enabled: bool = True
    flashrank_model: str = "ms-marco-MiniLM-L-12-v2"
    flashrank_cache_dir: Path = Path.home() / ".cache" / "flashrank"
    rerank_candidates: int = 40
    rerank_top_n: int = 5

    # --- PostgreSQL -------------------------------------------------------
    # 5435 avoids both the default 5432 and the Supabase stack on 54321-54324.
    pg_host: str = "127.0.0.1"
    pg_port: int = 5435
    pg_database: str = "vericlaim"
    pg_admin_user: str = "vericlaim"
    pg_readonly_user: str = "vericlaim_readonly"
    # Secrets come from the environment only.
    pg_admin_password_env: str = "POSTGRES_PASSWORD"
    pg_readonly_password_env: str = "READONLY_PASSWORD"
    ops_schema: str = "ops"
    sheets_schema: str = "sheets"

    # --- SQL safety -------------------------------------------------------
    sql_row_limit: int = 500
    sql_statement_timeout_ms: int = 10_000
    sql_max_refine_attempts: int = 5
    sql_step_budget_s: float = 60.0
    sql_candidate_count: int = 4
    sql_multi_candidate_enabled: bool = True

    # --- Cost control -----------------------------------------------------
    # Gemini's free tier serves every tier; OpenAI rungs are marked paid in
    # config.yaml and the ladder refuses them unless this is turned on. Flip it
    # deliberately for a demo or a paid evaluation run.
    allow_paid_fallback: bool = False
    # Ceilings checked BEFORE each call.
    #
    # Sized for a $1.00 prepaid OpenAI credit, deliberately well under it. The lifetime
    # figure is the one that matters: it is measured against a total persisted to disk,
    # so it bounds the project rather than resetting to zero on every process start.
    # A budget you can clear by restarting is a suggestion, not a ceiling.
    max_cost_usd_lifetime: float = 0.50
    # Bounds one process. Kept below the lifetime cap so a single runaway run cannot
    # consume the whole allowance.
    max_cost_usd_total: float = 0.25
    # Bounds one question, which is what catches a pathological retry or replan loop.
    max_cost_usd_per_request: float = 0.02
    spend_state_path: Path = PROJECT_ROOT / ".vericlaim_cache" / "spend.json"
    # Client-side throttling so we never generate the 429s that would otherwise walk
    # the ladder toward a paid provider.
    enforce_rate_limits: bool = True
    quota_state_path: Path = PROJECT_ROOT / ".vericlaim_cache" / "quota.json"
    # Longest we will wait for an RPM slot before giving up on a model.
    max_rate_limit_wait_s: float = 20.0

    # --- Orchestration ----------------------------------------------------
    max_replans: int = 2
    evidence_row_limit: int = 30

    # --- Tracing ----------------------------------------------------------
    langsmith_project: str = "vericlaim"
    # Tracing is opt-in. Absent a key, @traced is a transparent no-op so tests and
    # offline runs behave identically to traced runs.
    langsmith_tracing: bool = Field(
        default=False,
        validation_alias=AliasChoices("VC_LANGSMITH_TRACING", "LANGSMITH_TRACING"),
    )

    # --- Refusal ----------------------------------------------------------
    refusal_message: str = (
        "The available evidence is insufficient to answer that question reliably."
    )

    @model_validator(mode="after")
    def _check_chunking(self) -> Settings:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be smaller than "
                f"chunk_size ({self.chunk_size})"
            )
        return self

    @model_validator(mode="after")
    def _check_thresholds(self) -> Settings:
        if not 0.0 <= self.ocr_confidence_floor <= 1.0:
            raise ValueError(
                f"ocr_confidence_floor ({self.ocr_confidence_floor}) must be in [0, 1]"
            )
        return self

    def dsn(self, *, readonly: bool) -> str:
        """Return a libpq connection string for the requested role.

        The password is read from the environment variable named by settings, never
        from a committed file. A missing password yields an empty value so that the
        connection fails loudly at connect time rather than silently falling back to
        an ambient superuser.
        """
        user = self.pg_readonly_user if readonly else self.pg_admin_user
        env_name = (
            self.pg_readonly_password_env if readonly else self.pg_admin_password_env
        )
        password = os.environ.get(env_name, "")
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.pg_database} "
            f"user={user} password={password}"
        )


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One concrete model endpoint together with what it costs and what it allows.

    ``paid`` is the flag the fallback ladder consults. Gemini's free tier signals
    exhaustion with HTTP 429 -- a legitimately transient error -- so without an
    explicit paid marker, running out of free quota would retry, fall through to a
    billed provider, and quietly start spending.

    ``rpm``/``rpd`` are the provider's published free-tier limits, enforced
    client-side so we self-throttle instead of generating those 429s.
    """

    provider: str
    model: str
    usd_per_1m_input: float = 0.0
    usd_per_1m_output: float = 0.0
    timeout_s: float = 60.0
    max_output_tokens: int = 4096
    # Fails closed. An entry that omits the flag -- in YAML or in code -- is treated
    # as billable and therefore refused by default. Guessing wrong in this direction
    # costs a declined call; guessing wrong the other way costs money.
    paid: bool = True
    rpm: int | None = None
    rpd: int | None = None

    @property
    def label(self) -> str:
        """A stable ``provider/model`` identifier for traces, errors, and quota keys."""
        return f"{self.provider}/{self.model}"

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        """Return the USD cost of one call at this model's rates."""
        return (
            input_tokens * self.usd_per_1m_input
            + output_tokens * self.usd_per_1m_output
        ) / 1_000_000


class UnknownTaskError(KeyError):
    """Raised when a gateway call names a task with no routing entry.

    Deliberately an error rather than a silent default: a typo must not quietly
    bill the strong tier or, worse, downgrade a task that needs it.
    """


@dataclass(frozen=True, slots=True)
class ModelRouting:
    """The task -> tier -> model mapping, plus each tier's fallback ladder."""

    tiers: dict[str, ModelSpec]
    tasks: dict[str, str]
    fallbacks: dict[str, tuple[ModelSpec, ...]]
    transient_retries: int
    transient_backoff_s: float

    def tier_for(self, task: str) -> str:
        """Return the tier name serving ``task``."""
        try:
            return self.tasks[task]
        except KeyError as exc:
            known = ", ".join(sorted(self.tasks))
            raise UnknownTaskError(
                f"No model routing for task {task!r}. Known tasks: {known}"
            ) from exc

    def resolve(self, task: str) -> ModelSpec:
        """Return the primary model serving ``task``."""
        return self.tiers[self.tier_for(task)]

    def fallback_chain(self, task: str) -> tuple[ModelSpec, ...]:
        """Return the ordered fallback models for ``task``, primary excluded."""
        return self.fallbacks.get(self.tier_for(task), ())


def _model_spec(raw: dict[str, Any], *, defaults: ModelSpec | None = None) -> ModelSpec:
    """Build a ModelSpec from a YAML mapping, inheriting unset timing from ``defaults``."""
    if "provider" not in raw or "model" not in raw:
        raise ValueError(f"Model entry requires 'provider' and 'model': {raw!r}")
    return ModelSpec(
        provider=str(raw["provider"]),
        model=str(raw["model"]),
        usd_per_1m_input=float(raw.get("usd_per_1m_input", 0.0)),
        usd_per_1m_output=float(raw.get("usd_per_1m_output", 0.0)),
        timeout_s=float(
            raw.get("timeout_s", defaults.timeout_s if defaults else 60.0)
        ),
        max_output_tokens=int(
            raw.get(
                "max_output_tokens",
                defaults.max_output_tokens if defaults else 4096,
            )
        ),
        paid=bool(raw.get("paid", True)),
        rpm=int(raw["rpm"]) if raw.get("rpm") is not None else None,
        rpd=int(raw["rpd"]) if raw.get("rpd") is not None else None,
    )


def load_model_routing(path: Path | None = None) -> ModelRouting:
    """Load and validate the routing table from ``config.yaml``.

    Validation is strict on purpose. A task pointing at a tier that does not exist,
    or a fallback for an unknown tier, is a configuration bug that would otherwise
    surface only when that specific task first ran in production.
    """
    routing_path = path or DEFAULT_ROUTING_PATH
    raw = yaml.safe_load(routing_path.read_text(encoding="utf-8")) or {}

    tiers_raw = raw.get("tiers") or {}
    if not tiers_raw:
        raise ValueError(f"{routing_path} defines no tiers")
    tiers = {name: _model_spec(spec) for name, spec in tiers_raw.items()}

    tasks = {str(task): str(tier) for task, tier in (raw.get("tasks") or {}).items()}
    if not tasks:
        raise ValueError(f"{routing_path} defines no tasks")
    for task, tier in tasks.items():
        if tier not in tiers:
            raise ValueError(
                f"Task {task!r} routes to unknown tier {tier!r}. "
                f"Known tiers: {', '.join(sorted(tiers))}"
            )

    fallbacks: dict[str, tuple[ModelSpec, ...]] = {}
    for tier, entries in (raw.get("fallback") or {}).items():
        if tier not in tiers:
            raise ValueError(f"Fallback defined for unknown tier {tier!r}")
        fallbacks[str(tier)] = tuple(
            _model_spec(entry, defaults=tiers[tier]) for entry in entries or ()
        )

    limits = raw.get("limits") or {}
    return ModelRouting(
        tiers=tiers,
        tasks=tasks,
        fallbacks=fallbacks,
        transient_retries=int(limits.get("transient_retries", 2)),
        transient_backoff_s=float(limits.get("transient_backoff_s", 1.0)),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, resolved once."""
    return Settings()


@lru_cache(maxsize=1)
def get_model_routing() -> ModelRouting:
    """Return the process-wide model routing, loaded once.

    Cached rather than re-read per call: unibot re-read its config from disk on every
    single LLM call and every database connection, which is invisible at demo scale
    and a real problem under concurrency.
    """
    return load_model_routing()
