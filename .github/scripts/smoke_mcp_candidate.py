"""Exercise the installed candidate wheel through its real MCP stdio entry."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


EXPECTED_TOOLS = [
    "capabilities",
    "doctor",
    "validate_data",
    "preview_strategy",
    "start_backtest",
    "job_status",
    "cancel_job",
    "get_result",
]


def _child_environment(config: Path) -> dict[str, str]:
    allowed = (
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
    )
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment["DIEPI_MCP_CONFIG"] = str(config)
    return environment


async def _run() -> None:
    command = Path(os.environ["DIEPI_MCP_COMMAND"]).resolve(strict=True)
    config = Path(os.environ["DIEPI_MCP_CONFIG"]).resolve(strict=True)
    parameters = StdioServerParameters(
        command=str(command),
        args=[],
        env=_child_environment(config),
        cwd=str(config.parent),
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            actual = [tool.name for tool in tools.tools]
            if actual != EXPECTED_TOOLS:
                raise AssertionError({"expected": EXPECTED_TOOLS, "actual": actual})

            capabilities = await session.call_tool("capabilities", {})
            if capabilities.isError:
                raise AssertionError("candidate capabilities call failed")
            structured = capabilities.structuredContent or {}
            if structured.get("mode") != "research_backtest_only":
                raise AssertionError(structured)

            preview = await session.call_tool(
                "preview_strategy",
                {
                    "symbol": "000001.SZ",
                    "fast_window": 2,
                    "slow_window": 5,
                },
            )
            if preview.isError:
                raise AssertionError("candidate preview_strategy call failed")


def main() -> int:
    asyncio.run(_run())
    print("candidate wheel MCP stdio smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
