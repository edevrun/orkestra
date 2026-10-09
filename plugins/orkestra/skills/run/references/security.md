# Security checklist (on demand — distilled from vendor/ecc: security-reviewer, security-review, rules/*/security)

Load when the diff touches: auth/session · request handlers, server actions, loaders/actions · webhooks · payments ·
DB queries · file paths/uploads · outbound fetch to caller-supplied URLs · env/config · HTML built from external data ·
deep links · dependencies. Judge only what the diff introduces or exposes; cite file:line. Never open `.env*` or key
files to check (guards block it) — reason from variable names and call sites.

## Blocker
- Secret in source, or reaching a client bundle: private key/token behind `NEXT_PUBLIC_*`, `VITE_*`, `EXPO_PUBLIC_*`,
  `REACT_APP_*` or app.config `extra`.
- Injection: SQL built by concatenation / `$queryRawUnsafe` / `$executeRawUnsafe`; `exec` or `spawn(…, {shell:true})`
  with input; `eval`/`new Function`; file path from input without `path.resolve` + root-prefix check.
- Server-side authn/authz missing: a route, server action, loader/action or API handler reachable without a session
  check, or reading/mutating a record without proving it belongs to the caller (IDOR). Hiding UI is not access control.
- Webhook handled without signature verification (Shopify HMAC, Stripe signature…) or not idempotent on redelivery.
- XSS: `dangerouslySetInnerHTML`/`innerHTML` with non-constant input and no allowlist sanitizer at the same call site;
  `href`/`src` from external data without a scheme check (`javascript:`, `data:`).
- Session/refresh token in `localStorage`/`AsyncStorage` (web → httpOnly cookie; mobile → SecureStore).
- Check-then-write on money, stock, quota or uniqueness without a transaction, lock or unique constraint.

## Major
- External input used without schema validation at the boundary: body, query, FormData, headers, deep-link params,
  push payloads, third-party API responses.
- SSRF: server-side fetch of a caller-supplied URL without a host allowlist.
- Secrets, tokens or PII in logs, analytics or error responses; stack traces returned to clients.
- Cookie-authenticated state change without CSRF defence (SameSite or token); CORS `*` together with credentials.
- No rate limit on login/OTP/password-reset/expensive endpoints; unbounded page size or upload size.
- Deep merge/spread of untrusted objects into config, query or `where` objects (prototype pollution, mass assignment).
- Dependency added or bumped: unknown publisher, look-alike name, install scripts, or a high/critical advisory
  (`npm audit --omit=dev` — only when the lockfile changed).

## Not findings
`.env.example` placeholders · clearly marked test fixtures · keys that are public by design (Supabase anon, Firebase web
config, Shopify API key) provided server-side rules enforce access · MD5/SHA used as checksums.

## Output
Rows in your review table: severity · file:line · exploit scenario (exact request/input) · concrete fix.
Nothing found → one line: `security: checked <areas> — none`.
