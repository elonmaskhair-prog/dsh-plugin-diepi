import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const [profile, outputArgument, probeArgument] = process.argv.slice(2)
if (!['web', 'headless'].includes(profile) || outputArgument === undefined
  || probeArgument === undefined) {
  throw new Error(
    'usage: node write_dsh_probe_overlay.mjs <web|headless> <output.yml> <probe.mjs>',
  )
}

const output = resolve(outputArgument)
const probeUrl = pathToFileURL(resolve(probeArgument)).href
const lines = []
if (profile === 'headless') {
  // Preserve headless profile parsing while preventing a real model turn. The
  // CI invocation still supplies a dummy task to headless-startup.
  lines.push('- id: headless-runner', '  disabled: true', '')
}
lines.push(
  '- insert:',
  '    - id: diepi-dsh-runtime-probe',
  `      name: ${JSON.stringify(probeUrl)}`,
  '',
)

mkdirSync(dirname(output), { recursive: true })
writeFileSync(output, lines.join('\n'), { encoding: 'utf8', flag: 'wx' })
process.stdout.write(`${output}\n`)
