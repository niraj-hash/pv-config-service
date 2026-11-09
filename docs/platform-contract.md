# Runvance platform contract: what products never build themselves

Owner's decisions, 9 November 2026. A product keeps its business logic and nothing else: every
concern below is provided once, by the platform, and products use it the same way. Agents and
engineers working on a product follow this file; change it here first.

| Concern | Provided by | A product... |
|---|---|---|
| Sign-in, sessions, teams, roles | pg-api-gateway + pv-auth-service | reads `X-PV-*` headers; never verifies passwords or user tokens |
| Guest access (candidates, prospects, meeting guests) | the product (by decision) | issues one-time links scoped to one interview / demo / meeting; nothing else |
| Configuration | pv-config-service (Consul KV `config/<service>/data`, profiles `config/<service>,<profile>/data`) | keeps safe defaults in `application.yml` for local runs and tests; every environment's non-secret values live in pv-config-service; secrets only in Key Vault → Kubernetes secrets → env |
| Service discovery | Consul (`lb://<service>`) | never hard-codes another service's address |
| Inbound rate limits | pg-api-gateway, per route (`rate-limit` / `rate-limits` in `config/pg-api-gateway/conf.d/<product>.yml`), keyed by client address, user, organization, API key or guest link; every product's former limiter and its rule: [rate-limits.md](rate-limits.md) | has no rate-limit filters; business quotas (a plan's monthly sends, AI budgets) stay with the service that owns them |
| Circuit breakers, retries, timeouts | inbound: pg-api-gateway per route (defaults in `config/pg-api-gateway/data.yml`); outbound: Resilience4j in each caller with the shared defaults in `config/application/data.yml` | wraps every outbound call (platform services and third parties) in a named instance (`<target>`, e.g. `ai-service`, `notification-service`, `meta-graph`) that inherits the defaults; never hand-rolls breakers |
| AI models | runvance-ai-service (`lb://ai-service`, scope `ai.invoke`) | holds no provider SDK, key or model name |
| Email, SMS, WhatsApp, push | Runvance Notify (`lb://notification-service`, internal API, scope `comm.send`) | holds no mail/SMS/WhatsApp/push provider SDK, key or SMTP; sends rendered messages through Notify |
| Runtime | AKS with Consul; one Helm library chart (`runvance-service`) | ships a Helm chart built on the library chart; no Azure Container Apps |

## Service-to-service calls

A product backend calls a platform service with its own pv-auth-service **service client**
(client credentials; one client per product, e.g. `payroll-svc`, holding only the scopes it
needs: `ai.invoke`, `comm.send`), caches the token until a minute before expiry, refreshes once
on a 401, and sends `X-PV-Org-Id` (the organization the work is for). Platform services read the
product from the token, never from the request.

## Messages through Runvance Notify

- Products send **system messages** (invitations, reminders, alerts, payslips, interview
  schedules, OTP-free notices) as the platform: Notify sends them from the platform sender with the
  product's name ("Runvance Payroll <noreply@runvance.com>"), and meters them per product and
  organization. Transactional messages ignore marketing unsubscribes.
- Content is rendered by the product (subject, HTML, text; SMS/WhatsApp text; push title/body/
  data); attachments and calendar invites (`.ics`) are allowed.
- Runvance CRM's two-way lead conversations (WhatsApp and email from each customer's own accounts)
  stay in the CRM by decision; only CRM's own system messages use Notify.
- Inbound messages (a WhatsApp reply to Payroll's assistant, say) still arrive at the product that
  owns the conversation; its replies go out through Notify.

## Gateway rate limits

Every limit a product enforced itself becomes a gateway rule on that product's route, with the
same numbers unless the product's owner changes them. Keys: `CLIENT_IP`, `USER`, `ORG`,
`API_KEY` (a hash of the key header) and `GUEST` (a hash of the guest link token in the path or
header). Responses are 429 problem details with `Retry-After`. A route may have several limits
(per address and per organization, say): a request needs room in every one that applies, and
limits naming the same `bucket` share it. No bucket key holds a secret. The survey, the rules and
what products keep: [rate-limits.md](rate-limits.md).

## Circuit breakers, retries, timeouts

**Inbound (pg-api-gateway).** Every route has a circuit breaker (Spring Cloud CircuitBreaker,
Resilience4j), set by `pv.gateway.circuit-breaker` in `config/pg-api-gateway/data.yml` and a
route's own `circuit-breaker` block: open at 50 % failures (connection errors, timeouts, 502, 503,
504) or 80 % calls slower than 30 s (a route's longer `response-timeout-ms` counts instead) of the
last 50, once 20 were made; open for 30 s, during which the gateway answers 503
`UPSTREAM_UNAVAILABLE` with `Retry-After` and sends nothing; then 5 trial calls. The service's own
answers still reach the client. The gateway retries a GET, HEAD or OPTIONS once when no answer
came back (the connection failed or closed first), and never retries anything else.

**Outbound (each caller).** Resilience4j, from `config/application/data.yml` (the same
`resilience4j.*` names under Spring Boot 3 and 4): one circuit breaker, time limiter and, for
idempotent calls, retry per target, named after it.

| | `default` | `ai-service` | `notification-service` | `pv-auth-service` |
|---|---|---|---|---|
| Circuit breaker | 50 % failures or 80 % slower than 10 s of the last 50 calls (from 20); open 30 s; 5 trial calls; 4xx answers don't count | slow above 90 s | default | slow above 2 s |
| Time limiter | 10 s for the whole call (a blocking client sets its read timeout to match) | 130 s | 30 s (a batch is synchronous) | 5 s |
| Retry | 3 attempts, 200 ms doubling to 2 s, ±50 % jitter, on I/O errors, timeouts, 502, 503, 504, 429 | 2 attempts, reads only | default, sends with an `Idempotency-Key` | default, token requests |

- **Only idempotent calls are retried**: GET, HEAD, a PUT or DELETE of a known resource, a token
  request, a POST with an `Idempotency-Key` the callee honours (Notify's sends). Completions,
  speech, transcription and embeddings are never retried by the caller: they cost tokens, and
  ai-service already retries its providers.
- **Retry-After is honoured**: after a 429 or 503 carrying it, the next attempt waits that long
  instead of the backoff, when that is at most `pv.resilience.retry-after-max` (10 s); longer,
  the call is not retried and the caller says "try later". Resilience4j has no property for it, so
  each caller's retry instance reads the header (its interval function).
- A target that isn't a platform service (`meta-graph`, `sendgrid`, an ATS) gets an instance of
  its own name, inheriting `default`; a product changes numbers in its own context, never here.
- `scripts/validate.py` checks the names, units and classes (JDK and spring-web only, so the shared
  file loads in every service).

## Runtime

Each service: a chart depending on `runvance-service` (Deployment, Service, ServiceAccount with
workload identity, Consul token and other secrets from Key Vault via ExternalSecrets,
`CONFIG_IMPORT=consul:`, probes, resources, HPA, PDB, NetworkPolicy admitting only the gateway
and named callers). Images in ACR, built by hand (`scripts/release.sh`). Deployed only by a manual
Sync in Argo CD and rolled back from its History, from the service's release file; no CI
([deploy-with-argocd.md](deploy-with-argocd.md)). Container Apps scripts and workflows are retired.
Conventions and how to add a service: [runtime.md](runtime.md).
