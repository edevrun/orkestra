# Project: {{project}}

> Orkestra project memory. Written by onboarding (`/orkestra:onboard`), verified against the code,
> kept short (< 120 lines). Every run reads the first 25 lines, so keep the summary block dense and current.

## Summary
- Purpose: <one line: what the product does and for whom>
- Stack: <languages, frameworks, runtime versions>
- Entry points: <app/server/cli entry files>
- Data: <DB / storage / external APIs>
- Deploy target: <domain / store / host, from the AGENTS.md isolation rules>
- Gates: <see .orkestra/config.json>
- Invariants: <never-break rules from AGENTS.md / CLAUDE.md>

## Structure
<top-level directories and what lives where, 1 line each>

## Conventions
<naming, error handling, state management, test style, file layout patterns WITH example paths>

## Testing
<frameworks, where tests live, how to run one test, fixtures/mocks>

## Integrations
<third-party services, env var NAMES (never values)>

## Concerns
<known fragile areas, tech debt, flaky tests, pre-existing failing gates>
