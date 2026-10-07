import { cloudflare } from '@cloudflare/vite-plugin'
import { holocron } from '@holocron.so/vite'
import { createLogger, defineConfig } from 'vite'

// @orama/orama (Holocron's search) publishes sourcemaps that point at src/*.ts
// files it doesn't ship, so Vite warns once per module in the Worker
// environment. Drop only those warnings for packages in node_modules.
const logger = createLogger()
const warnOnce = logger.warnOnce
logger.warnOnce = (msg, options) => {
  if (/Sourcemap for ".*\/node_modules\/.*" points to missing source files/.test(msg)) return
  warnOnce(msg, options)
}

export default defineConfig({
  customLogger: logger,
  plugins: [
    holocron({ pagesDir: './pages' }),
    cloudflare({
      viteEnvironment: {
        name: 'rsc',
        childEnvironments: ['ssr'],
      },
    }),
  ],
})
