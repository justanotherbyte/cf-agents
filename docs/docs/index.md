---
title: Home
template: home.html
hide:
  - navigation
  - toc
---

<section class="cw-section" markdown>

## What is Crosswind? { #what-is-crosswind }

[Python Workers](https://developers.cloudflare.com/workers/languages/python/) run
Python on Cloudflare's global network, but getting beyond "hello world" often
means dropping down to JavaScript bindings, hand-rolling glue code, or hitting
the edges of what the runtime supports.

Crosswind closes that gap. Each SDK gives Python Workers first-class, Pythonic
access to a part of the Cloudflare platform and pushes them into workloads they
couldn't handle on their own. You write idiomatic, typed, `async` Python;
Crosswind handles the plumbing.

</section>

<section class="cw-section" markdown>

## Why Crosswind? { #why-crosswind }

<div class="cw-features" markdown>
<div class="cw-feature" markdown>

### Pythonic by design

Typed, `async`-first APIs that read like Python, not JavaScript translated
line by line.

</div>
<div class="cw-feature" markdown>

### Past the runtime's limits

Move heavier, longer-running and more specialised work out of the isolate
without leaving Python.

</div>
<div class="cw-feature" markdown>

### More of the platform

Reach more Cloudflare products from Python, with one consistent developer
experience across every SDK.

</div>
<div class="cw-feature" markdown>

### Small and focused

Separate packages per product. Install only what you need and keep your
Worker lean.

</div>
</div>

</section>

<section class="cw-section" markdown>

## SDKs { #sdks }

<div class="cw-sdk-table" markdown>

| SDK | Package | Description | Version |
| --- | --- | --- | --- |
| [Containers](containers/getting-started.md) | `containers-py` | Start, stop and route requests to Cloudflare Containers from a Python Worker. | 0.1.0 |

</div>

More SDKs are in development and will be listed here as they ship.
{ .cw-footnote }

</section>
