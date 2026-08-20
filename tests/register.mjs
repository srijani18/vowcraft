import { registerHooks } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve as resolvePath } from 'node:path'

/**
 * Module resolution for `node --test`: the `@/*` path alias, and the
 * extensionless imports that a bundler would resolve for us.
 *
 * The alternative was a bundler or ts-node in devDependencies. A synchronous
 * resolve hook is a few dozen lines, needs no dependency, and Node compiles the
 * TypeScript itself — which keeps the unit suite honest: it imports the *same*
 * files the application imports, with no build step in between that could diverge.
 */

const root = resolvePath(dirname(fileURLToPath(import.meta.url)), '..')
const HAS_EXTENSION = /\.[cm]?[jt]sx?$/

function attempts(target) {
  // Mirrors what a bundler tries, in the same order.
  return HAS_EXTENSION.test(target) ? [target] : [`${target}.ts`, `${target}.tsx`, `${target}/index.ts`, target]
}

registerHooks({
  resolve(specifier, context, nextResolve) {
    const isAlias = specifier.startsWith('@/')
    const isRelative = specifier.startsWith('./') || specifier.startsWith('../')
    if (!isAlias && !isRelative) return nextResolve(specifier, context)

    const base = isAlias
      ? pathToFileURL(resolvePath(root, 'src', specifier.slice(2))).href
      : new URL(specifier, context.parentURL).href

    let lastError
    for (const candidate of attempts(base)) {
      try {
        return nextResolve(candidate, context)
      } catch (err) {
        lastError = err
      }
    }
    throw lastError
  },
})
