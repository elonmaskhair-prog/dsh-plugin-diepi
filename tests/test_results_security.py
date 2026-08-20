from diepi_mcp.results import _public_result_contract, _scalars


def test_execution_stats_projection_rejects_unsafe_keys_and_sanitizes_text():
    projected = _scalars(
        {
            "trade_count": 3,
            "unsafe\nkey": "visible",
            "detail": "/srv/private/report final.csv",
            "not_finite": float("nan"),
        }
    )

    assert projected == {"detail": "<path>", "trade_count": 3}


def test_result_contract_projection_is_bounded_and_path_private():
    contract = {
        "schema_version": 1,
        "semantics_version": "v1",
        "status": "FAILED",
        "rankable": False,
        "reason": {"code": "FAILED", "message": r"C:\private\reason secret"},
        "warnings": [
            {"code": "WARNING", "message": "/srv/private/warning secret"}
            for _ in range(75)
        ],
        "assumptions": [
            {"key": "data.source", "value": "/srv/private/assumption secret"}
            for _ in range(75)
        ],
        "actual_interval": None,
        "data_coverage": None,
    }

    projected = _public_result_contract(contract)
    serialized = str(projected)

    assert len(projected["warnings"]) == 50
    assert len(projected["assumptions"]) == 50
    assert "private" not in serialized
    assert "secret" not in serialized
    assert "<path>" in serialized
