"""Typed corporate-action capabilities shared by data and execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


CORPORATE_ACTION_ASOF_SOURCE = (
    "eastmoney_sharebonus_server_asof+cninfo_allotment_server_asof+"
    "baostock_preclose_server_asof"
)
CORPORATE_ACTION_ASOF_UNIVERSE_RULE = (
    "security_master.exchange in SH/SZ and list_date<=cutoff"
)


class CorporateActionFormulaStatus(str, Enum):
    NONE = "none"
    NO_PRICE_ADJUSTMENT = "no_price_adjustment"
    SUPPORTED_STANDARD = "supported_standard"
    RIGHTS_ISSUE_PRICE_ONLY = "rights_issue_price_formula_only"
    UNSUPPORTED_MALFORMED_TERMS = "unsupported_malformed_terms"
    UNSUPPORTED_MISSING_FORMULA_STATUS = "unsupported_missing_formula_status"
    UNSUPPORTED_SHARE_CONSOLIDATION = "unsupported_share_consolidation"
    UNSUPPORTED_UNCLASSIFIED_SHARE_CHANGE = "unsupported_unclassified_share_change"
    UNSUPPORTED_REFERENCE_MISMATCH = "unsupported_reference_mismatch"
    UNSUPPORTED_MISSING_REFERENCE_PRICE = "unsupported_missing_reference_price"
    UNSUPPORTED_DECLARED_ACTION_MISSING_REFERENCE_PRICE = (
        "unsupported_declared_action_missing_reference_price"
    )


@dataclass(frozen=True)
class CorporateActionCapabilities:
    """Independent safety capabilities for one serialized event status."""

    price_formula_supported: bool
    holder_entitlements_supported: bool
    execution_supported: bool
    label_supported: bool


_SAFE = CorporateActionCapabilities(True, True, True, True)
_STANDARD = CorporateActionCapabilities(True, True, True, True)
_PRICE_ONLY = CorporateActionCapabilities(True, True, False, False)
_ENTITLEMENTS_ONLY = CorporateActionCapabilities(False, True, False, False)
_UNSUPPORTED = CorporateActionCapabilities(False, False, False, False)

CORPORATE_ACTION_CAPABILITIES = {
    CorporateActionFormulaStatus.NONE: _SAFE,
    CorporateActionFormulaStatus.NO_PRICE_ADJUSTMENT: _SAFE,
    CorporateActionFormulaStatus.SUPPORTED_STANDARD: _STANDARD,
    CorporateActionFormulaStatus.RIGHTS_ISSUE_PRICE_ONLY: _PRICE_ONLY,
    CorporateActionFormulaStatus.UNSUPPORTED_MALFORMED_TERMS: _UNSUPPORTED,
    CorporateActionFormulaStatus.UNSUPPORTED_MISSING_FORMULA_STATUS: (_UNSUPPORTED),
    CorporateActionFormulaStatus.UNSUPPORTED_SHARE_CONSOLIDATION: _UNSUPPORTED,
    CorporateActionFormulaStatus.UNSUPPORTED_UNCLASSIFIED_SHARE_CHANGE: (_UNSUPPORTED),
    CorporateActionFormulaStatus.UNSUPPORTED_REFERENCE_MISMATCH: (_ENTITLEMENTS_ONLY),
    CorporateActionFormulaStatus.UNSUPPORTED_MISSING_REFERENCE_PRICE: (_UNSUPPORTED),
    CorporateActionFormulaStatus.UNSUPPORTED_DECLARED_ACTION_MISSING_REFERENCE_PRICE: (
        _ENTITLEMENTS_ONLY
    ),
}
LEGACY_STATUS_ALIASES = {
    "unsupported_rights_issue": (CorporateActionFormulaStatus.RIGHTS_ISSUE_PRICE_ONLY)
}


def formula_status_value(value: object) -> CorporateActionFormulaStatus:
    """Parse a status and fail closed when a producer emits an unknown value."""
    if isinstance(value, CorporateActionFormulaStatus):
        return value
    serialized = str(value)
    if serialized in LEGACY_STATUS_ALIASES:
        return LEGACY_STATUS_ALIASES[serialized]
    try:
        return CorporateActionFormulaStatus(serialized)
    except ValueError:
        return CorporateActionFormulaStatus.UNSUPPORTED_MISSING_FORMULA_STATUS


def serialized_status_values_for(
    status: CorporateActionFormulaStatus,
) -> tuple[str, ...]:
    """Return canonical and legacy serialized values for one typed status."""
    return (
        status.value,
        *(alias for alias, target in LEGACY_STATUS_ALIASES.items() if target is status),
    )


def capabilities_for(value: object) -> CorporateActionCapabilities:
    """Return explicit price/execution/label capabilities for a status."""
    return CORPORATE_ACTION_CAPABILITIES[formula_status_value(value)]


def has_supported_price_formula(value: object) -> bool:
    """Whether event terms can form an ex-right reference price."""
    return capabilities_for(value).price_formula_supported


def requires_conservative_invalidation(value: object) -> bool:
    """Whether execution and forward labels must both be invalidated."""
    capabilities = capabilities_for(value)
    return not (capabilities.execution_supported and capabilities.label_supported)
