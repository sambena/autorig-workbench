// SPDX-License-Identifier: GPL-3.0-or-later
// Autorig Workbench: runs Mesh2Motion's own TypeScript skin solvers under the fork's vitest, for
// scripts/m2m_skin_parity.py. Plain object (no import from 'vitest/config'): vitest and three live in the submodule's
// node_modules, not ours.
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const repo = resolve(here, '..', '..')
const m2m = resolve(repo, 'third_party', 'mesh2motion-app')

export default {
  root: repo,
  // vite's caches go in the submodule's node_modules (git-ignored there), not a node_modules of ours
  cacheDir: resolve(m2m, 'node_modules', '.vite-parity'),
  // one copy of three for this harness and the solvers, so instanceof Bone holds across them
  resolve: { alias: { three: resolve(m2m, 'node_modules', 'three', 'build', 'three.module.js') } },
  server: { fs: { allow: [repo] } },
  test: {
    include: ['scripts/m2m_skin_parity/*.test.ts'],
    environment: 'node',
    globals: true,
    testTimeout: 1800000,
    silent: true
  }
}
