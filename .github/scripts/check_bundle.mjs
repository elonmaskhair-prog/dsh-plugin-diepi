import assert from 'node:assert/strict'
import path from 'node:path'

import { apply, name } from '../../dsh/index.js'

const originalCommand = process.env.DIEPI_MCP_COMMAND
const originalConfig = process.env.DIEPI_MCP_CONFIG

function restore(name, value) {
  if (value === undefined) delete process.env[name]
  else process.env[name] = value
}

function invoke() {
  let provided
  apply({
    provide(key, value) {
      provided = { key, value }
    },
  })
  return provided
}

try {
  assert.equal(name, 'diepi-bundle-config')

  delete process.env.DIEPI_MCP_COMMAND
  delete process.env.DIEPI_MCP_CONFIG
  assert.throws(invoke, /DIEPI_MCP_COMMAND is required/)

  process.env.DIEPI_MCP_COMMAND = 'diepi-mcp'
  process.env.DIEPI_MCP_CONFIG = path.resolve('config.json')
  assert.throws(invoke, /DIEPI_MCP_COMMAND must be an absolute path/)

  process.env.DIEPI_MCP_COMMAND = path.resolve('venv', 'diepi-mcp')
  process.env.DIEPI_MCP_CONFIG = 'config.json'
  assert.throws(invoke, /DIEPI_MCP_CONFIG must be an absolute path/)

  process.env.DIEPI_MCP_CONFIG = path.resolve('config.json')
  const supplied = invoke()
  assert.equal(supplied.key, 'diepiBundleConfig')
  assert.equal(supplied.value.command, process.env.DIEPI_MCP_COMMAND)
  assert.equal(supplied.value.config, process.env.DIEPI_MCP_CONFIG)
  assert.equal(supplied.value.cwd, path.dirname(process.env.DIEPI_MCP_CONFIG))
  assert.equal(path.isAbsolute(supplied.value.skillDir), true)
  assert.equal(Object.isFrozen(supplied.value), true)
} finally {
  restore('DIEPI_MCP_COMMAND', originalCommand)
  restore('DIEPI_MCP_CONFIG', originalConfig)
}

console.log('bundle fail-closed path checks passed')
