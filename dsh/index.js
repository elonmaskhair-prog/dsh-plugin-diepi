import { fileURLToPath } from 'node:url'
import { dirname, isAbsolute } from 'node:path'

export const name = 'diepi-bundle-config'

function requiredAbsoluteEnvironmentPath(variable) {
  const value = process.env[variable]?.trim()
  if (!value) {
    throw new Error(
      `${variable} is required; set it to an absolute host-owned path before activating dsh-plugin-diepi`,
    )
  }
  if (!isAbsolute(value)) {
    throw new Error(`${variable} must be an absolute path; received ${JSON.stringify(value)}`)
  }
  return value
}

export function apply(ctx) {
  const command = requiredAbsoluteEnvironmentPath('DIEPI_MCP_COMMAND')
  const config = requiredAbsoluteEnvironmentPath('DIEPI_MCP_CONFIG')
  ctx.provide('diepiBundleConfig', Object.freeze({
    skillDir: fileURLToPath(new URL('./skills/', import.meta.url)),
    command,
    config,
    cwd: dirname(config),
  }))
}
