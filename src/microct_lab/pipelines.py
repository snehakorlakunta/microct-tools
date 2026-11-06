"""Which measurement method is which: the official one, and the ones tagged SK.

Three measurement kinds share the Measurement table and the worker:

  levidigit_v1    OFFICIAL. The vendored LeviDigit postprocessing
                  (scripts/measure_levidigit.py): BV/TV with a fixed grey bone
                  threshold, and digit length from the largest concavity.
  bvtv_thresh_v1  SK. The interim in-app BV/TV (HU threshold converted per scan).
  digitpipe_v5    SK. The vendored perios morphometry (socket, bone length).

The SK methods are kept and still run — nothing is deleted or migrated — but
they are labelled "SK" everywhere and the default views (stats, normalization,
the site) use only the official method. The tag is derived from
`pipeline_version`, never stored, so every existing row is tagged correctly
without a migration.
"""
from __future__ import annotations

LEVIDIGIT_PIPELINE = "levidigit_v1"
OFFICIAL_PIPELINE = LEVIDIGIT_PIPELINE

OFFICIAL_TAG = "LeviDigit"
SK_TAG = "SK"

# Source filters accepted by the list / stats endpoints.
SOURCES = ("official", "sk", "all")


def is_levidigit(version: str | None) -> bool:
    return (version or "").startswith("levidigit")


def is_official(version: str | None) -> bool:
    return is_levidigit(version)


def is_sk_bvtv(version: str | None) -> bool:
    return (version or "").startswith("bvtv")


def source_tag(version: str | None) -> str:
    return OFFICIAL_TAG if is_official(version) else SK_TAG


def matches_source(version: str | None, source: str | None) -> bool:
    if source in (None, "", "all"):
        return True
    if source == "official":
        return is_official(version)
    if source == "sk":
        return not is_official(version)
    raise ValueError(f"unknown source {source!r} (expected one of {', '.join(SOURCES)})")
