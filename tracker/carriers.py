"""
Canonical carrier names so reporting doesn't split the same carrier across
spelling variants (e.g. "United Healthcare" vs "UnitedHealthcare", or Molina's
several legal names). Matching elsewhere uses substring checks (.contains
"ambetter"/"united"/…), which still work after canonicalization.

Only unambiguous duplicates are merged; genuinely distinct plans (e.g. the
state-specific Blue Cross entities) are left alone.
"""

import re


def normalize_carrier(name):
    if name is None:
        return name
    s = str(name).strip()
    if not s:
        return s
    key = re.sub(r"[^a-z0-9]", "", s.lower())
    if key.startswith("unitedhealthcar"):      # "United Healthcare", "UnitedHealthcare", truncated
        return "UnitedHealthcare"
    if key.startswith("molina"):               # "Molina Marketplace", "MOLINA HEALTHCARE OF …"
        return "Molina"
    if key == "uofu" or key.startswith("universityofuta"):
        return "University of Utah"
    return s


def normalize_carrier_series(series):
    return series.map(normalize_carrier)
