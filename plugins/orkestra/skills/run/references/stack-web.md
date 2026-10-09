# Stack checklist: web (on demand — TypeScript · React · Next.js · Vite · Remix/React Router; distilled from vendor/ecc: typescript-reviewer, react-reviewer, rules/react)

Load when the diff contains .ts/.tsx/.js/.jsx in a web or Node project. Lint and typecheck gates already ran — do not
repeat what they catch; look for what they miss. Only the diff; cite file:line.

## Types — major
- New `any`, `as` cast to an unrelated type, or `!` without a preceding guard; tsconfig strictness weakened.
- External data typed by assertion instead of parsed (zod/valibot) at the boundary.

## Async — major
- Floating promise: async call without `await`/`.catch` in handlers, effects or constructors; `forEach(async …)`.
- `await` inside a loop over independent work (N+1 API/DB calls) → batch; unbounded `Promise.all` over a user-sized list.
- `fetch` without `res.ok` check, timeout/abort or failure path; `JSON.parse`/`new URL` on external input unguarded.
- Swallowed errors: empty `catch`, `.catch(() => [])`, a default value that hides the failure, log-and-forget.

## React correctness — major
- Hook called conditionally or after an early return; `exhaustive-deps` disabled without a reason comment.
- Effect without cleanup (listener, interval, subscription, fetch without AbortController); stale closure in an async
  handler or interval.
- State mutated in place; derived state stored through an effect (compute in render); the same data in two states;
  effect chains that set state.
- `key={index}` on a list that can reorder/insert/delete; state initialised from a prop and never reset (missing `key`).
- New async UI without loading, empty and error states.

## Server/client boundary — blocker when data or secrets leak
- Next.js: server-only module (DB client, secret) imported under `"use client"`; a full record passed as props to a
  client component; server action without its own auth check + input validation.
- Remix / React Router (Shopify apps): secrets or Admin API calls outside `loader`/`action`; a new route without
  `authenticate.admin(request)` (or the matching `authenticate.*`); a loader returning more fields than the UI needs.
- Client code may read only `NEXT_PUBLIC_*` / `VITE_*` values — and those are public.

## UI — major/minor
- `<div onClick>` instead of a button/link; input without a label; `img` without `alt`; `target="_blank"` without
  `rel="noopener noreferrer"`; an error signalled by colour only.

## Performance — minor unless on a hot path
- Whole-library import (`import _ from 'lodash'`); sync fs or heavy CPU in a request handler; 50+ rows rendered
  without virtualization; inline object/function props into memoized children; memoization with no measured need.

## Leftovers — minor
- `console.log`/`debugger`, commented-out code, repeated magic values.
