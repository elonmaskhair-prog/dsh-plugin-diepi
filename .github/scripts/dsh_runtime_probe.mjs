import assert from 'node:assert/strict'

export const name = 'diepi-dsh-runtime-probe'
export const inject = ['tools']

export const PASS_SENTINEL = 'DIEPI_DSH_RUNTIME_PROBE_PASS'

const EXPECTED_DIEPI_TOOLS = [
  'mcp__diepi__cancel_job',
  'mcp__diepi__capabilities',
  'mcp__diepi__doctor',
  'mcp__diepi__get_result',
  'mcp__diepi__job_status',
  'mcp__diepi__preview_strategy',
  'mcp__diepi__start_backtest',
  'mcp__diepi__validate_data',
]

function terminateCleanly() {
  // Windows does not implement process.kill(pid, 'SIGTERM') with POSIX signal
  // delivery. DSH's own built-bin tests use the event form on Windows and a
  // real signal elsewhere; both reach the launcher's bounded shutdown path.
  if (process.platform === 'win32') process.emit('SIGTERM')
  else process.kill(process.pid, 'SIGTERM')
}

function writePassSentinel() {
  return new Promise((resolve, reject) => {
    process.stdout.write(`${PASS_SENTINEL}\n`, (error) => {
      if (error) reject(error)
      else resolve()
    })
  })
}

async function probe(ctx) {
  const actual = ctx.tools.schemas()
    .map(schema => schema.name)
    .filter(toolName => toolName.startsWith('mcp__diepi__'))
    .sort()
  assert.deepEqual(actual, EXPECTED_DIEPI_TOOLS)

  const result = await ctx.tools.execute({
    callId: 'call_diepi_ci_probe_capabilities',
    name: 'mcp__diepi__capabilities',
    arguments: {},
    signal: AbortSignal.timeout(30_000),
  })
  assert.equal(result.isError, false, 'DSH capabilities call returned an MCP error')
  assert.ok(Array.isArray(result.content) && result.content.length > 0,
    'DSH capabilities call returned no content')

  await writePassSentinel()
  terminateCleanly()
}

export function apply(ctx) {
  // Loader siblings mount concurrently. Waiting for complete settlement proves
  // that failOnStartupError, MCP discovery, and tool registration all finished
  // before the catalog is inspected. An assertion rejection is intentionally
  // left to DSH's fail-loud process handler.
  void ctx.loader.await().then(async () => { await probe(ctx) })
}
