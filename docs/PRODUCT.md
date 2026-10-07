# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Python developers, in two groups, with evaluators first:

- **Evaluators** are deciding whether Python on Cloudflare Workers can handle their workload. Many have already hit the "hello world" ceiling: they'd have to drop into JavaScript bindings, write glue code by hand, or run into the runtime's limits. The home page exists mainly to convince them.
- **Adopters** have already decided. They come to the SDK pages to install a package, wire it into a Worker, and ship.

## Product Purpose

Crosswind is a family of typed, async-first Python SDKs that give Cloudflare Python Workers first-class, Pythonic access to more of the Cloudflare developer platform. They also let Workers take on heavier, longer-running and more specialised workloads than the isolate supports alone. This site is the documentation and front door for those SDKs.

Success: an evaluator leaves believing Python Workers can do real work with Crosswind, and an adopter goes from install to a working Worker without having to read JavaScript.

## Positioning

- Idiomatic, typed, `async` Python. The APIs are designed for Python, not JavaScript translated line by line.
- Pushes Python Workers past what the runtime handles on its own. For example, `containers-py` hands heavier work to Cloudflare Containers without leaving Python.
- Each Cloudflare product gets its own small package, and every SDK shares one consistent developer experience.

## Operating Context

- Readers arrive from Python and Cloudflare Workers ecosystems, PyPI, and search. They read in a desktop browser with an editor and terminal nearby, copying code into `src/entry.py` and Wrangler config.
- The home page links straight to each SDK's getting-started guide and to PyPI. Live version numbers come from the PyPI JSON API (`components/pypi-version.tsx`), and the static "PyPI" link text is the fallback.
- A live demo, Mosslight Valley, is embedded on the home page. It's a game built with `cf-agents` and running entirely on Python Workers.

## Capabilities and Constraints

- **Site stack:** [Holocron](https://holocron.so) (a Vite plugin) with MDX pages in `pages/`, site config in `docs.jsonc`, theme tokens in `style.css` and React components in `components/`. It's deployed as a Cloudflare Worker with Wrangler (`npm run deploy`).
- **SDKs today:** `containers-py` (Containers) and `cf-agents` (Agents SDK). More are in development and get listed only as they ship.
- **Maturity:** early and pre-1.0. APIs may change, and the docs should say so plainly rather than imply production stability.
- **Terminology:** "Crosswind" is the umbrella name. Each product gets its own package (`containers-py`, `cf-agents`). The runtime is called "Python Workers".
- **Open:** the Containers getting-started guide is still a stub, the Agents docs page doesn't exist yet, so the SDK table's Agents row isn't linked, and the minimum supported Python version isn't confirmed (the hero says 3.12+).

## Brand Commitments

- **Independence:** Crosswind is an independent, community project. It is not affiliated with or endorsed by Cloudflare, and nothing on the site may look or read as official Cloudflare property. Use Cloudflare product names only to describe what the SDKs target. Never use Cloudflare logos, trade dress, or "official" framing.
- **Personal connections stay off the site:** No personal or organisational connection to Cloudflare may appear anywhere in site UI or copy.
- **Long-term goal:** upstream adoption by Cloudflare. The site and SDKs should hold up to Cloudflare's own developer-docs standards, so adoption could happen without a rewrite.
- **Voice:** plain, technical, and direct, as in the existing copy, e.g. "You write idiomatic, typed, `async` Python; Crosswind handles the plumbing."

## Evidence on Hand

- Real code sample: a `containers-py` transcoder Worker in the home hero (`pages/index.mdx`).
- Live demo: Mosslight Valley (`https://agents-py-mosslight-valley.reachvishm8605.workers.dev/`), built with `cf-agents`.
- PyPI package pages for `containers-py` and `cf-agents`.
- **Absent, do not fabricate:** user counts, testimonials, company logos, benchmarks or performance numbers, production case studies, and any claim of Cloudflare endorsement.

## Product Principles

1. **Pythonic first.** Every example and API description should read like idiomatic Python. If something needs JavaScript knowledge to understand, it's a docs bug.
2. **Show real capability, not claims.** Use working code and live demos as proof. Don't use invented metrics or social proof.
3. **Honest about maturity.** Pre-1.0 status and unshipped SDKs are stated plainly, never hidden.
4. **Independent, but adoption-ready.** Hold Cloudflare-grade quality without borrowing Cloudflare's identity.
5. **Small and focused.** One package per product, and docs that get a reader to a working Worker quickly.
