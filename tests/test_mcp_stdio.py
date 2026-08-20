import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from diepi_mcp.server import create_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_stdio_server_discovers_constrained_tool_surface(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    config = tmp_path / "config.json"
    config.write_text(
        """{
          "schema_version": 1,
          "state_root": "state",
          "datasets": {
            "fixture": {
              "data_root": "data",
              "results_root": "results",
              "data_grade": "raw_only",
              "default_price_mode": "raw"
            }
          }
        }""",
        encoding="utf-8",
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "diepi_mcp", "--config", str(config)],
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.list_tools()
            assert [tool.name for tool in response.tools] == [
                "capabilities",
                "doctor",
                "validate_data",
                "preview_strategy",
                "start_backtest",
                "job_status",
                "cancel_job",
                "get_result",
            ]
            assert all("path" not in tool.name for tool in response.tools)
            banned_schema_keys = {
                "$defs",
                "$ref",
                "allOf",
                "anyOf",
                "not",
                "minimum",
                "maximum",
                "exclusiveMinimum",
                "exclusiveMaximum",
                "pattern",
                "format",
            }

            def schema_keys(value):
                if isinstance(value, dict):
                    return set(value) | set().union(*(schema_keys(item) for item in value.values()))
                if isinstance(value, list):
                    return set().union(*(schema_keys(item) for item in value))
                return set()

            for tool in response.tools:
                assert not (schema_keys(tool.inputSchema) & banned_schema_keys)
            start_tool = next(tool for tool in response.tools if tool.name == "start_backtest")
            assert "submission_id" in start_tool.inputSchema["required"]
            assert start_tool.inputSchema["properties"]["submission_id"]["type"] == "string"
            result = await session.call_tool("capabilities", {})
            assert result.isError is False
            assert result.structuredContent["mode"] == "research_backtest_only"
            assert result.structuredContent["data_contract"]["layout"] == ("diepi.market_data_v1")
            assert result.structuredContent["execution_model"]["granularity"] == (
                "bar_based_cash_account_not_order_book"
            )
            preview = await session.call_tool(
                "preview_strategy",
                {
                    "symbol": "510300.SH",
                    "fast_window": 5,
                    "slow_window": 20,
                    "amount_lookback": 10,
                    "amount_minimum_ratio": 1.5,
                },
            )
            assert preview.isError is False
            canonical = preview.structuredContent["canonical_spec"]
            assert canonical["schema_version"] == 1
            assert canonical["amount_filter"] == {
                "lookback": 10,
                "minimum_ratio": 1.5,
            }
            invalid_job = await session.call_tool(
                "job_status",
                {"job_id": "../C:\\private\\secret\n" + "x" * 1000},
            )
            assert invalid_job.isError is True
            error_text = " ".join(
                item.text for item in invalid_job.content if getattr(item, "text", None)
            )
            assert "DIEPI_MCP_INVALID_ARGUMENT" in error_text
            assert "private" not in error_text
            assert "secret" not in error_text
            assert "\n" not in error_text
            assert len(error_text) < 400


@pytest.mark.anyio
async def test_fastmcp_lifespan_releases_service_state_root_lock(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    config = tmp_path / "config.json"
    config.write_text(
        """{
          "schema_version": 1,
          "state_root": "state",
          "datasets": {
            "fixture": {
              "data_root": "data",
              "results_root": "results",
              "data_grade": "raw_only",
              "default_price_mode": "raw"
            }
          }
        }""",
        encoding="utf-8",
    )

    first = create_server(str(config))
    async with first._mcp_server.lifespan(first._mcp_server):
        pass

    second = create_server(str(config))
    async with second._mcp_server.lifespan(second._mcp_server):
        pass
