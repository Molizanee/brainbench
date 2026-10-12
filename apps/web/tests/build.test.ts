import { afterAll, expect, test } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import pkg from '../package.json'

const outdir = mkdtempSync(join(tmpdir(), 'brainbench-web-build-'))

afterAll(() => rmSync(outdir, { recursive: true, force: true }))

// The SPA is served for every path, so a relative asset URL in the built HTML resolves under
// the current route (/topics/<id>/index-<hash>.js), gets the HTML back, and the page is blank
// on reload or on a direct link to any nested route.
test('the production build references its assets by absolute path', () => {
  const command = pkg.scripts.build.replace('--outdir dist', `--outdir ${outdir}`)
  const build = Bun.spawnSync(['sh', '-c', command], { cwd: join(import.meta.dir, '..') })
  expect(build.exitCode).toBe(0)
  const html = readFileSync(join(outdir, 'index.html'), 'utf8')
  const urls = [...html.matchAll(/(?:src|href)="([^"]+)"/g)]
    .map((m) => m[1]!)
    .filter((url) => !url.startsWith('data:')) // the favicon is inline
  expect(urls.length).toBeGreaterThan(0)
  for (const url of urls) expect(url).toStartWith('/')
}, 60_000)
