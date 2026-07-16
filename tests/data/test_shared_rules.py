from __future__ import annotations

import pandas as pd

from data.scripts.corporate_action_status import (
    CORPORATE_ACTION_CAPABILITIES,
    CorporateActionFormulaStatus,
    can_credit_holder_entitlements,
    capabilities_for,
    formula_status_value,
    requires_conservative_invalidation,
    serialized_status_values_for,
)
from data.scripts.price_rules import cny_cent_key, round_cny_price_to_cent


def test_unknown_corporate_action_status_fails_closed() -> None:
    assert formula_status_value("new_unreviewed_formula") == (
        CorporateActionFormulaStatus.UNSUPPORTED_MISSING_FORMULA_STATUS
    )
    assert requires_conservative_invalidation("new_unreviewed_formula")


def test_rights_issue_capabilities_separate_price_from_execution() -> None:
    status = CorporateActionFormulaStatus.RIGHTS_ISSUE_PRICE_ONLY
    capabilities = capabilities_for(status)

    assert formula_status_value(status) is status
    assert capabilities.price_formula_supported
    assert capabilities.holder_entitlements_supported
    assert not capabilities.execution_supported
    assert not capabilities.label_supported
    assert can_credit_holder_entitlements(status)
    assert requires_conservative_invalidation(status)


def test_reference_mismatch_preserves_known_holder_entitlements_only() -> None:
    capabilities = capabilities_for(
        CorporateActionFormulaStatus.UNSUPPORTED_REFERENCE_MISMATCH
    )

    assert not capabilities.price_formula_supported
    assert capabilities.holder_entitlements_supported
    assert not capabilities.execution_supported
    assert not capabilities.label_supported


def test_reference_gap_distinguishes_declared_and_unknown_entitlements() -> None:
    unknown = capabilities_for(
        CorporateActionFormulaStatus.UNSUPPORTED_MISSING_REFERENCE_PRICE
    )
    declared = capabilities_for(
        CorporateActionFormulaStatus
        .UNSUPPORTED_DECLARED_ACTION_MISSING_REFERENCE_PRICE
    )

    assert not unknown.holder_entitlements_supported
    assert declared.holder_entitlements_supported
    assert not declared.price_formula_supported
    assert not declared.execution_supported
    assert not declared.label_supported


def test_every_serialized_status_has_explicit_capabilities() -> None:
    assert set(CORPORATE_ACTION_CAPABILITIES) == set(CorporateActionFormulaStatus)


def test_serialized_status_values_include_canonical_and_legacy_aliases() -> None:
    assert serialized_status_values_for(
        CorporateActionFormulaStatus.RIGHTS_ISSUE_PRICE_ONLY
    ) == ("rights_issue_price_formula_only", "unsupported_rights_issue")


def test_vector_and_scalar_cent_rules_are_identical() -> None:
    values = pd.Series([10.725, 9.646, float("nan"), -1.0])
    rounded = round_cny_price_to_cent(values)

    assert rounded.iloc[:2].tolist() == [10.73, 9.65]
    assert [cny_cent_key(value) for value in values.iloc[:2]] == [1073, 965]
    assert cny_cent_key(values.iloc[2]) is None
    assert cny_cent_key(values.iloc[3]) is None
