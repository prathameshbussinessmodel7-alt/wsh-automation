
"""
WSH R7.3 — Semantic and Unit Validation Engine.

Purpose:
- Validate documented field definitions and units.
- Validate numeric and text values against documented contracts.
- Validate documented relationships between fields.
- Preserve UNKNOWN when authoritative semantic evidence is absent.
- Never infer units or definitions from numeric magnitude or header names.

This module is an independent validation layer.
It does not modify or execute the WSH collector.
"""

from decimal import Decimal, InvalidOperation
from typing import Any


VERSION = "R7.3"

SUPPORTED_DATASETS = {
    "NSE_FII_DII",
    "CCIL_MONEY_MARKET",
    "CCIL_GSEC",
    "CCIL_FOREX",
    "CCIL_DERIVATIVES",
}

POLICY = {
    "units_inferred_from_magnitude": False,
    "definitions_inferred_from_header_names": False,
    "source_evidence_required_for_semantic_pass": True,
}

VALID_VALUE_TYPES = {
    "numeric",
    "text",
}

VALID_RELATIONSHIP_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
}


def _result(status, reason, **extra):
    """Return a consistent validation result."""
    output = {
        "status": status,
        "reason": reason,
    }
    output.update(extra)
    return output


def to_decimal(value):
    """
    Convert a value to Decimal without losing precision.

    Returns None for missing, malformed or non-finite values.
    """
    if value is None or isinstance(value, bool):
        return None

    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None

    if not result.is_finite():
        return None

    return result


def validate_field_spec(field_name, spec):
    """
    Validate the documentation contract for one field.

    Required:
    - definition
    - unit
    - evidence_reference
    - value_type
    """
    if not isinstance(spec, dict):
        return _result(
            "UNKNOWN",
            "Field specification is missing or malformed.",
            field=field_name,
        )

    required = (
        "definition",
        "unit",
        "evidence_reference",
        "value_type",
    )

    missing = [
        key for key in required
        if spec.get(key) is None
        or str(spec.get(key)).strip() == ""
    ]

    if missing:
        return _result(
            "UNKNOWN",
            "Required semantic documentation is missing.",
            field=field_name,
            missing_fields=missing,
        )

    value_type = str(spec["value_type"]).strip().lower()

    if value_type not in VALID_VALUE_TYPES:
        return _result(
            "UNKNOWN",
            "Unsupported documented value type.",
            field=field_name,
            value_type=value_type,
        )

    if value_type == "numeric":
        minimum = spec.get("minimum")
        maximum = spec.get("maximum")

        if minimum is not None and maximum is not None:
            min_value = to_decimal(minimum)
            max_value = to_decimal(maximum)

            if min_value is None or max_value is None:
                return _result(
                    "FAIL",
                    "Numeric bounds are malformed.",
                    field=field_name,
                )

            if min_value > max_value:
                return _result(
                    "FAIL",
                    "Minimum exceeds maximum.",
                    field=field_name,
                )

        elif minimum is not None and to_decimal(minimum) is None:
            return _result(
                "FAIL",
                "Numeric minimum is malformed.",
                field=field_name,
            )

        elif maximum is not None and to_decimal(maximum) is None:
            return _result(
                "FAIL",
                "Numeric maximum is malformed.",
                field=field_name,
            )

    if value_type == "text":
        allowed = spec.get("allowed_values")

        if allowed is not None:
            if not isinstance(allowed, (list, tuple, set)):
                return _result(
                    "FAIL",
                    "Allowed text values must be a collection.",
                    field=field_name,
                )

    return _result(
        "PASS",
        "Documented field contract is complete.",
        field=field_name,
        value_type=value_type,
        evidence_reference=spec["evidence_reference"],
    )


def validate_field_value(value, spec):
    """Validate one observed value against its documented field contract."""

    spec_result = validate_field_spec("field", spec)

    if spec_result["status"] != "PASS":
        return _result(
            spec_result["status"],
            "Field contract is not sufficiently documented.",
            contract=spec_result,
        )

    if value is None or (
        isinstance(value, str) and not value.strip()
    ):
        return _result(
            "PARTIAL",
            "Field value is missing.",
        )

    value_type = str(spec["value_type"]).strip().lower()

    if value_type == "numeric":
        number = to_decimal(value)

        if number is None:
            return _result(
                "FAIL",
                "Value is not a valid finite number.",
            )

        minimum = spec.get("minimum")
        maximum = spec.get("maximum")

        if minimum is not None:
            if number < to_decimal(minimum):
                return _result(
                    "FAIL",
                    "Value is below documented minimum.",
                    observed_value=str(number),
                    minimum=str(to_decimal(minimum)),
                )

        if maximum is not None:
            if number > to_decimal(maximum):
                return _result(
                    "FAIL",
                    "Value exceeds documented maximum.",
                    observed_value=str(number),
                    maximum=str(to_decimal(maximum)),
                )

        return _result(
            "PASS",
            "Numeric value satisfies documented contract.",
            normalized_value=str(number),
        )

    if value_type == "text":
        text_value = str(value).strip()
        allowed = spec.get("allowed_values")

        if allowed is not None and text_value not in allowed:
            return _result(
                "FAIL",
                "Text value is outside documented allowed values.",
                observed_value=text_value,
                allowed_values=list(allowed),
            )

        return _result(
            "PASS",
            "Text value satisfies documented contract.",
            normalized_value=text_value,
        )

    return _result(
        "UNKNOWN",
        "Value type cannot be validated.",
    )


def validate_relationship(record, relationship, field_specs):
    """
    Validate a documented relationship between two record fields.

    Supported operators:
    eq, ne, gt, gte, lt, lte
    """
    if not isinstance(record, dict):
        return _result(
            "FAIL",
            "Relationship record is malformed.",
        )

    if not isinstance(relationship, dict):
        return _result(
            "UNKNOWN",
            "Relationship specification is missing or malformed.",
        )

    left = relationship.get("left")
    right = relationship.get("right")
    operator = relationship.get("operator")
    evidence = relationship.get("evidence_reference")

    if not evidence or not str(evidence).strip():
        return _result(
            "UNKNOWN",
            "Relationship evidence reference is missing.",
        )

    if operator not in VALID_RELATIONSHIP_OPERATORS:
        return _result(
            "UNKNOWN",
            "Relationship operator is unsupported.",
            operator=operator,
        )

    if left not in field_specs or right not in field_specs:
        return _result(
            "UNKNOWN",
            "Relationship fields lack documented contracts.",
        )

    left_spec = validate_field_spec(left, field_specs[left])
    right_spec = validate_field_spec(right, field_specs[right])

    if (
        left_spec["status"] != "PASS"
        or right_spec["status"] != "PASS"
    ):
        return _result(
            "UNKNOWN",
            "Relationship field documentation is incomplete.",
        )

    if (
        field_specs[left]["value_type"] != "numeric"
        or field_specs[right]["value_type"] != "numeric"
    ):
        return _result(
            "UNKNOWN",
            "Relationship requires documented numeric fields.",
        )

    left_value = record.get(left)
    right_value = record.get(right)

    if left_value is None or right_value is None:
        return _result(
            "PARTIAL",
            "One or more relationship values are missing.",
        )

    a = to_decimal(left_value)
    b = to_decimal(right_value)

    if a is None or b is None:
        return _result(
            "FAIL",
            "Relationship contains an invalid numeric value.",
        )

    comparisons = {
        "eq": a == b,
        "ne": a != b,
        "gt": a > b,
        "gte": a >= b,
        "lt": a < b,
        "lte": a <= b,
    }

    if comparisons[operator]:
        return _result(
            "PASS",
            "Documented relationship is satisfied.",
            left_value=str(a),
            right_value=str(b),
            operator=operator,
        )

    return _result(
        "FAIL",
        "Documented relationship is violated.",
        left_value=str(a),
        right_value=str(b),
        operator=operator,
    )


def _aggregate_status(statuses):
    """Apply conservative dataset-level status aggregation."""
    if not statuses:
        return "UNKNOWN"

    if "FAIL" in statuses:
        return "FAIL"

    if "PARTIAL" in statuses:
        return "PARTIAL"

    if "UNKNOWN" in statuses:
        return "UNKNOWN"

    if all(status == "PASS" for status in statuses):
        return "PASS"

    return "UNKNOWN"


def validate_dataset(
    dataset,
    records,
    field_specs,
    relationships=None,
):
    """
    Validate a dataset using explicit semantic field contracts.

    A PASS requires:
    - A supported dataset identifier.
    - Non-empty records.
    - Documented field contracts.
    - All supplied field values satisfying those contracts.
    - All supplied relationships satisfying their contracts.

    Missing semantic evidence is UNKNOWN, not PASS.
    """
    base = {
        "validator_version": VERSION,
        "dataset": dataset,
        "policy": dict(POLICY),
    }

    if dataset not in SUPPORTED_DATASETS:
        return {
            **base,
            "status": "UNKNOWN",
            "reason": "Dataset is outside the supported R7.3 scope.",
            "records_checked": 0,
        }

    if not isinstance(records, list) or not records:
        return {
            **base,
            "status": "UNKNOWN",
            "reason": "No records supplied for semantic validation.",
            "records_checked": 0,
        }

    if not isinstance(field_specs, dict):
        return {
            **base,
            "status": "UNKNOWN",
            "reason": "Field specifications are missing or malformed.",
            "records_checked": len(records),
        }

    if not field_specs:
        return {
            **base,
            "status": "UNKNOWN",
            "reason": "No documented semantic field contracts supplied.",
            "records_checked": len(records),
            "field_contracts": {},
            "records": [],
        }

    if any(not isinstance(record, dict) for record in records):
        return {
            **base,
            "status": "FAIL",
            "reason": "At least one dataset record is malformed.",
            "records_checked": len(records),
        }

    contract_results = {}
    for field, spec in field_specs.items():
        contract_results[field] = validate_field_spec(field, spec)

    record_results = []
    all_statuses = []

    for index, record in enumerate(records):
        field_results = {}
        record_statuses = []

        for field, spec in field_specs.items():
            result = validate_field_value(
                record.get(field),
                spec,
            )
            field_results[field] = result
            record_statuses.append(result["status"])

        relationship_results = []

        for relationship in relationships or []:
            result = validate_relationship(
                record,
                relationship,
                field_specs,
            )
            relationship_results.append(result)
            record_statuses.append(result["status"])

        record_status = _aggregate_status(record_statuses)

        record_results.append({
            "record_index": index,
            "status": record_status,
            "fields": field_results,
            "relationships": relationship_results,
        })

        all_statuses.append(record_status)

    # An undocumented field contract cannot support a semantic PASS.
    for result in contract_results.values():
        if result["status"] != "PASS":
            all_statuses.append(result["status"])

    final_status = _aggregate_status(all_statuses)

    return {
        **base,
        "status": final_status,
        "reason": "Dataset semantic validation completed.",
        "records_checked": len(records),
        "field_contracts": contract_results,
        "records": record_results,
    }


if __name__ == "__main__":
    print("WSH R7.3 Semantic Validation Engine")
    print("Version:", VERSION)
    print("Supported datasets:", len(SUPPORTED_DATASETS))
    print("Unit inference from magnitude:", POLICY[
        "units_inferred_from_magnitude"
    ])
    print("Header-name semantic inference:", POLICY[
        "definitions_inferred_from_header_names"
    ])
