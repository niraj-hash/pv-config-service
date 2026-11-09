# Rate limits: every product limiter and the gateway rule that replaces it

Inbound rate limits are pg-api-gateway's ([platform-contract.md](platform-contract.md)). This
is the survey of 9 November 2026 (each product's `platform/gateway-auth` working tree): each
limit a product enforced itself, and the gateway rule in
`config/pg-api-gateway/conf.d/<product>.yml` with the same paths, methods, numbers and key.

**Status (later on 9 November 2026): done.** The products' own request limiters are removed (CRM:
`AuthRateLimitFilter`, the MCP and `IntegrationGate` limiters; Meet: `RateLimiter`; Interview:
`StaffRateLimitFilter`, `CapabilityRateLimitFilter`; Connect's was already gone; Payroll had none
left). What a product still enforces itself is listed at the end, each with the reason the gateway
can't (the value is in the request body, it counts completed actions or failed credentials, it is a
business quota, or the caller reaches the service inside the cluster, past the gateway, as with
Interview's `PublicRateLimitFilter`).

## How a rule reads

```yaml
rate-limits:            # or rate-limit: one block. Every bucket that applies must have room.
  - bucket: interview-link       # optional name; limits with the same name share one bucket (on any route)
    key: GUEST                   # CLIENT_IP | USER | ORG | API_KEY | GUEST
    path-variable: token         # GUEST: the token is this variable of the paths (or headers: [...])
    paths: ["/api/v1/join/{token}/**"]
    exclude-paths: ["/api/v1/join/{token}/pad/**"]
    methods: [POST]
    replenish-rate: 1            # tokens a second
    burst-capacity: 60           # tokens the bucket holds
    requested-tokens: 2          # tokens a request takes
```

"C at once, then R a second" is `burst-capacity: C×T`, `replenish-rate: R×T`, `requested-tokens: T`,
with T the smallest whole number that makes R×T whole. So N a minute with bursts of N is
`1, 60, 60/N`; N an hour is `1, 3600, 3600/N`.

| Key | The bucket is per | When the request has none |
|---|---|---|
| `CLIENT_IP` | client address: the `X-Forwarded-For` entry the ingress added (the last), never one the client wrote | — |
| `USER` | verified person (`sub`) | client address |
| `ORG` | verified person's organization (`org_id`) | person, then client address |
| `API_KEY` | SHA-256 of the key in the first of `headers` present (default `Authorization`, its scheme dropped) | client address |
| `GUEST` | SHA-256 of the guest-link token: `path-variable` of the first matching path, else `headers` | client address |

A refused request is a 429 problem (`RATE_LIMITED`) with `Retry-After`. A request takes from
the caller's own buckets first (address, person, then key or link, then organization), so a
flood refused per address never drains its organization's or link's bucket. Unlike most of the
products' limiters, the buckets are shared by every gateway replica (Redis), are token buckets
(no double burst at a window's edge), and count every request that reaches the route, before
the service's own checks.

## Product limiter → gateway rule

Paths of product classes are relative to each repository; `→` names the route in that product's
`conf.d` file. Numbers are replenish-rate / burst-capacity / requested-tokens (default 1).

### Runvance Interview (`interview-ai-management`, `backend/src/main/java/com/interviewai/`) → `interview.yml`

| Product limiter | Requests | Limit, key | Gateway rule |
|---|---|---|---|
| `platform/web/PublicRateLimitFilter` | GET `/public/v1/**` | 300 at once, 5/s per address | `interview-public` and `interview-api-public`: bucket `interview-public-ip` CLIENT_IP, GET, 5 / 300 |
| `platform/web/CapabilityRateLimitFilter` (links) | any method on `/api/v1/{join,panel-access}/{token}/**`, `/api/v1/schedule/{token}[:book]`, not the pad | 30 then 0.5/s per link; 120 then 2/s per address | `interview-capability` (and `interview-sse` for `join/{token}/captions/stream`): `interview-link` GUEST `{token}` 1 / 60 / 2, `interview-link-ip` CLIENT_IP 2 / 120, pad paths excluded |
| `CapabilityRateLimitFilter` (coding pad) | `/api/v1/{join,panel-access}/{token}/pad/**` | 300 then 25/s per link; 600 then 50/s per address | `interview-capability` (and `interview-sse` for the pad streams): `interview-pad-link` GUEST `{token}` 25 / 300, `interview-pad-ip` CLIENT_IP 50 / 600 |
| `CapabilityRateLimitFilter` (`sso-discovery`) | `/api/v1/sso-discovery/**` | 600 then 10/s per address | none: no such endpoint, no route |
| `platform/web/StaffRateLimitFilter` (every call) | every `/api/**` call of a person, except `interviews/{id}/join-link:reissue` | 600 then 10/s per person; 6000 then 100/s per organization | `interview-app-api` (and `interview-sse` for `interviews/{id}/live`, `/pad/stream`): `interview-staff-user` USER 10 / 600, `interview-staff-org` ORG 100 / 6000, reissue excluded. The service's person bucket was per person *in* an organization |
| `StaffRateLimitFilter` (search) | GET `/api/v1/search`, `/backlog`, `/analytics/performance[/by-job]`, `/platform/audit-log` | 60 then 1/s per person; 600 then 10/s per organization | `interview-app-api`: `interview-search-user` USER 1 / 60, `interview-search-org` ORG 10 / 600 |
| `StaffRateLimitFilter` (export) | POST `/api/v1/assessment-runs/{id}:export-pdf`, `/analytics/exports`, `/candidates:import`; GET `/api/v1/fairness/export[.csv]`, `/platform/regulated-features/export`, `/platform/audit-log:verify` | 20/min per person; 200/min per organization | `interview-app-api`: `interview-export-user` USER 1 / 60 / 3, `interview-export-org` ORG 10 / 600 / 3 (one limit per method set, one bucket) |
| `StaffRateLimitFilter` (AI) | POST `/api/v1/jobs/{id}:draft-description`, `/jobs/{id}/competency-framework-drafts`, `/interviews/{id}/kit:generate`, `/interviews/{id}/assessment:rerun`, `/resumes/{id}:parse`, `/platform/prompts/{key}/versions/{version}/eval-runs` | 30/min per person; 300/min per organization | `interview-app-api`: `interview-ai-user` USER 1 / 60 / 2, `interview-ai-org` ORG 5 / 300 |
| `platform/security/ApiKeyAuthenticationFilter` (per key) | `/api/v1/**` with `Authorization: Bearer iak_…` | 600/min per key (`default-rate-limit-per-minute`) | `interview-api-keys`: API_KEY `Authorization`, 10 / 600 |
| `portal/internal/CandidateSignInService` (start) | POST `/candidate-api/v1/auth/sign-in:start` | 30/h per address | `interview-candidate`: CLIENT_IP 1 / 3600 / 120 |
| `CandidateSignInService` (verify) | POST `/candidate-api/v1/auth/sign-in:verify-code`, `:verify-link` | 60 per 10 min per address, together | `interview-candidate`: CLIENT_IP 1 / 600 / 10 |
| `portal/internal/CareersService` (start) | POST `/public/v1/orgs/{slug}/jobs/{jobId}/applications:start` | 20/h per address | `interview-public` and `interview-api-public`: bucket `interview-careers-start-ip` CLIENT_IP 1 / 3600 / 180 |
| `CareersService` (submit) | POST `/candidate-api/v1/careers/{slug}/jobs/{jobId}/applications` | 30/h per address | `interview-candidate`: CLIENT_IP 1 / 3600 / 120 |

### Runvance CRM (`PayVizio-LeadOS.gateway-auth`, `backend/app/src/main/java/com/payvizio/leados/app/`) → `leados.yml`

| Product limiter | Requests | Limit, key | Gateway rule |
|---|---|---|---|
| `config/AuthRateLimitFilter` | POST `/api/v1/public/leads` | 60/min per address | `leados-public`: CLIENT_IP 1 / 60 |
| `AuthRateLimitFilter` | POST `/api/v1/public/demo-requests` | 10/min per address | `leados-public`: CLIENT_IP 1 / 60 / 6 |
| `AuthRateLimitFilter` | POST `/api/v1/public/questionnaires/{token}`, `/demo-feedback/{token}` | 10/min per address and link | `leados-public`: GUEST `{token}` 1 / 60 / 6 (per link) |
| `AuthRateLimitFilter` | POST `/api/v1/public/agreements/{token}/code`, `/sign` (counted apart) | 10/min per address and link | `leados-public`: two limits, GUEST `{token}` 1 / 60 / 6 each |
| `AuthRateLimitFilter` | POST `/api/v1/billing/webhooks/payvizio` | 60/min per address | `leados-api`: CLIENT_IP 1 / 60 |
| `AuthRateLimitFilter` | POST `/oauth/register` | 30/min per address | `leados-mcp`: CLIENT_IP 1 / 60 / 2 (replaces the coarse register-and-token rule) |
| `AuthRateLimitFilter` | POST `/oauth/token` | 300/min per address | `leados-mcp`: CLIENT_IP 5 / 300 |
| `mcp/web/McpServerController` | POST `/mcp`, `/mcp/` | 120/min per connection (`lok_` key or `loa_` token) | `leados-mcp`: API_KEY `Authorization`, 2 / 120. A `loa_` token is refreshed hourly, and its new value is a new bucket |
| `integration/IntegrationGate` | `/api/v1/integrations/**` except `demo-access/resolve` | 120/min per `lok_` key | `leados-integrations`: API_KEY `Authorization`, 2 / 120 |

### Runvance Payroll (`hr-ai-solution.gateway-auth`) → `payroll.yml`

| Product limiter | Requests | Limit, key | Gateway rule |
|---|---|---|---|
| `backend/shared-lib/…/security/AuthThrottleFilter` (deleted with sign-in in the working tree) | any method on `/api/demo`, `/api/newsletter` (and the sign-in paths now gone) | 30 per 5 min per address, shared | `payroll-public-forms`: CLIENT_IP 1 / 300 / 10 on both paths |

### Runvance Meet (`ai-demo-agent.gateway-auth/meet/api`, `common/RateLimiter` in `meetings/MeetingService`) → `meet.yml`

| Product limiter | Requests | Limit, key | Gateway rule |
|---|---|---|---|
| `join-ip` | POST `/api/v1/public/invites/{token}/join` | 60 per 10 min per address | `meet-people-api`: CLIENT_IP 1 / 600 / 10 |
| `invite-user` | POST `/api/v1/meetings/{id}/invites` (a person) | 100/h per person | `meet-people-api`: USER 1 / 3600 / 36 |
| `invite-app` | POST `/api/v1/meetings/{id}/invites` (a platform, `mk_` key) | 600/h per platform (one key each) | `meet-platform-api`: API_KEY `Authorization`, 1 / 3600 / 6 |

### Runvance Connect (`ai-demo-agent.gateway-auth/services/platform-core`, `security/PublicRateLimitFilter`, deleted in the working tree) → `demoagent.yml`

| Product limiter | Requests | Limit, key | Gateway rule |
|---|---|---|---|
| `PUBLIC` | `/api/v1/public/**` | 60 then 2/s per address | `demoagent-api-public`: CLIENT_IP 2 / 60 (already there) |
| `SOCKET` | `/ws/**` (`/ws/sessions/{id}`) | 20 then 0.5/s per address | `demoagent-api-socket`: CLIENT_IP 1 / 40 / 2 (already there) |
| `AUTH` | `/api/v1/auth/**` | 10 then 0.2/s per address | none: sign-in is pv-auth-service's, the endpoints are gone |

### Runvance Notify (`notification-service/backend`) → `notification-api.yml`

| Product limiter | Requests | Limit, key | Gateway rule |
|---|---|---|---|
| `config/AppProperties` `app.rate-limit.default-per-minute` | API-key traffic | 120/min per tenant, **declared but never enforced** | `notification-server-api`: API_KEY `X-API-Key` or `Authorization: ApiKey`, 2 / 120 on `/api/notifications/**`. New in effect: per key (the gateway doesn't know a key's tenant). The product's owner confirms it or changes the numbers |

### Runvance Health (`hms`), Runvance accelerator (`runvance/accelerator`)

No limiter of their own. Their gateway rules are the gateway's own (below).

## Gateway rules with no product counterpart (kept)

`accounts-sign-in` (pv-auth-service: posts 10/min per address), `hms-public` (inquiries 10/min
per address), `hms-api` (20/s, burst 40, per person), `leados-public` (every post: 60, then 2/s
per address), `meet-people-api` (`/api/v1/public/**`: 60, then 1/s per address),
`payroll-public-forms` (posts 10/min per address, the careers application included),
`runvance-demo-requests` (posts 10/min per address). The "10 a minute" rules use burst-capacity
10 with 6 tokens a request: one request at once, then one every 6 s.

## What products keep, and why

The gateway sees a request's address, person, organization, headers and path, not the body, the
database, or what the service decided. These stay with the product:

| Product | Kept | Why |
|---|---|---|
| Interview | `ApiKeyAuthenticationFilter`: a key's own `rate_limit_per_minute` when it isn't the default 600 | a per-key setting stored with the key; the gateway applies the default. If any key has a higher limit, raise the gateway rule to the 6000 maximum and let the service hold each key to its own |
| Interview | `ApiKeyAuthenticationFilter` (20 failed keys/min per address), `CandidateSessionFilter` (30 failed sessions/min per address) | they count failed credentials, which only the service knows |
| Interview | `CandidateSignInService`, `CareersService`: the per-email limits | the address is in the body or the session |
| Interview | the per-address limits on `/candidate-api/**` and `/public/v1/**`, until the web app's server calls the API through the gateway | the web app's server (BFF) calls these inside the cluster (`API_BASE_URL`), so the gateway sees only direct callers |
| Interview | `JoinGrantService` reissue (3/h per interview, 100/h per organization), `StaffPhones` codes, `PracticeService` (5/day), `RunQuotas` code runs, `CopilotRequests` (one at a time), AI budgets | they count what was done (emails, SMS, runs, a plan's quota) or concurrency, not requests |
| CRM | `AgreementService` signing codes (1/min, 5/h per agreement, 5 tries), AI calls and budget per tenant, plan limits, Ad Studio jobs and renders, WhatsApp and Meta pacing | per resource or business quotas, or outbound |
| Connect | the demo room's per-session quotas (turns, speech, audio), the TTS allowance, booking quotas (per email, per link), 100 invitation emails/day per organization, `OrgThrottle` | inside the WebSocket, keyed by the body or query, or counting what was done |
| Notify | monthly sends per plan (`PlanQuotas`) | a business quota |
| Health | plan limits, storage quota | business quotas |
| Meet, Payroll, accelerator | nothing | |
