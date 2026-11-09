# Moving every product onto runvance.com

Owner's decision, 9 November 2026: no product keeps a domain of its own. Each one is served only
from a single-level `runvance.com` subdomain, so one `*.runvance.com` certificate covers them all.
The code and configuration already use the new hosts. Nothing below has been done yet: it is the
work outside the repositories, in the order to do it.

Product names changed the same day: CliniqSpace is **Runvance Health**, NotifyHub is
**Runvance Notify**, AI Demo Agent is **Runvance Connect** and Interview AI is **Runvance Interview**.

## Host map

| Product | Old | New |
|---|---|---|
| Runvance CRM | `leads.payvizio.com` | `crm.runvance.com` |
| Runvance CRM (API, webhooks, embedded forms) | `leads-api.payvizio.com` | `crm-api.runvance.com` |
| Runvance Health | `hms.cliniqspace.com`, `api.cliniqspace.com` | `health.runvance.com` |
| Runvance Health marketing site | `cliniqspace.com`, `www.cliniqspace.com` | retired → `www.runvance.com/products/health/` |
| Runvance Notify | `notify.payvizio.com` | `notify.runvance.com` |
| Runvance Notify (API keys) | `notify-api.payvizio.com` | `notify-api.runvance.com` |
| Runvance Connect (API) | `api.demo.eduvizio.com` | `connect-api.runvance.com` |
| Runvance Connect (prospect site) | `demo.eduvizio.com` | `demo.runvance.com` |
| Payroll (old marketing) | `peoplevizio.com` | already retired; `payroll.runvance.com` |

Unchanged: `accounts`, `payroll`, `connect`, `assessment`, `meetings`, `meet-api`, `interview`,
`api.interview` (nested: needs its own certificate) and `accelerator`, all on `runvance.com`, and
`www.runvance.com`.

Left on other domains on purpose: calls to the EduVizio product and the PayVizio payments product
(`api.eduvizio.com`, `api.payvizio.com`, `checkout.payvizio.com`), Runvance Health's FHIR identifier
URIs on `cliniqspace.in` (lab systems match on them), and the legal entity name
"Payvizio Technologies Pvt Ltd".

## 1. DNS and certificates

- [ ] `*.runvance.com` certificate on pg-api-gateway's ingress (or one per host).
- [ ] Point at pg-api-gateway: `crm`, `crm-api`, `health`, `notify`, `notify-api`,
      `connect-api`, `demo` (`.runvance.com`), with the `asuid` TXT records Azure asks for.
- [ ] After `scripts/sync.sh` and a gateway restart, check each host answers (`/bff/session` on a
      product host, `/actuator/health` isn't public).

## 2. Running apps' settings (the defaults in code don't reach a live app)

- [ ] **Runvance CRM:** `FRONTEND_ORIGIN=https://crm.runvance.com`,
      `META_REDIRECT_URI=https://crm-api.runvance.com/api/v1/meta/oauth/callback`,
      `META_WEBHOOK_CALLBACK_URL=https://crm-api.runvance.com/api/v1/meta/webhooks/leadgen`, and any
      `PUBLIC_BASE_URL`, `GOOGLE_OAUTH_REDIRECT_URI`, `MCP_PUBLIC_BASE_URL` still on the old hosts.
- [ ] **Runvance Connect:** in its `.env`, `WEB_HOST`, `API_HOST`, `EMAIL_FROM`, `EMAIL_FROM_NAME`,
      `LEADOS_BASE_URL`; then `deploy.sh domains` and `deploy.sh apps` (it keeps live values otherwise).
- [ ] **Runvance Health:** `ESIGN_CALLBACK_URL` (if set), `DIGILOCKER_REDIRECT_URI`, `SUPER_ADMIN_EMAIL`.
- [ ] **Payroll:** `SENDGRID_FROM_EMAIL` if set to the old address.
- [ ] **Runvance Notify:** `PROVIDER_WEBHOOK_BASE_URL=https://notify-api.runvance.com` (provider
      webhook URLs and Twilio signature checks use it), `PLATFORM_TENANT_ID` (the tenant pv-auth-service
      sends account emails through: the only one that may use `SENDGRID_API_KEY`; any other tenant
      without an email provider is sandbox), `SENDGRID_FROM_ADDRESS` (a verified sender on
      runvance.com), `SENDGRID_WEBHOOK_VERIFICATION_KEY` (see section 3).
- [ ] pv-auth-service and pg-api-gateway: nothing; their hosts and sign-in callbacks come from
      pv-config-service (sync, then restart both).

## 3. Outside services

- [ ] **Meta app (CRM):** OAuth redirect URIs, app domains, leadgen and WhatsApp webhook URLs,
      privacy / terms / data-deletion URLs.
- [ ] **Google OAuth clients:** CRM's redirect URI; Runvance Connect's calendar callbacks
      `https://connect-api.runvance.com/api/v1/integrations/google-calendar/callback`.
- [ ] **Microsoft app (Runvance Connect):** `.../integrations/microsoft-calendar/callback` on `connect-api`.
- [ ] **e-Sign provider (Runvance Health):** webhook `https://health.runvance.com/api/v1/public/esign/webhook`.
- [ ] **Lab systems (Runvance Health):** push URLs `/api/v1/lab-interface/ingest/results` and `/ingest/hl7`.
- [ ] **MyOperator, TeleCMI, demo-booking webhooks (CRM):** each workspace re-pastes its URL.
- [ ] **PayVizio payments:** any return URL or webhook registered on the old CRM host.
- [ ] **Search Console:** add `crm.runvance.com`, submit its sitemap.
- [ ] **SendGrid Event Webhook (Runvance Notify), platform account:** set the URL to
      `https://notify-api.runvance.com/api/webhooks/providers/sendgrid/platform`, turn on **Signed
      Event Webhook**, and put its verification key in Notify's `SENDGRID_WEBHOOK_VERIFICATION_KEY`.
      The old `/api/webhooks/providers/sendgrid` (unsigned, unscoped) now answers 410, and the gateway
      no longer routes it.
- [ ] **SendGrid Event Webhook, each tenant with its own SendGrid account:** re-register the URL its
      Channels page shows (`https://notify-api.runvance.com/api/webhooks/providers/sendgrid/{configId}`)
      with Signed Event Webhook on, and paste the verification key into that configuration. Until then
      its events are refused (no delivered / bounce updates, no auto-suppression).
- [ ] **Twilio (Runvance Notify tenants):** nothing for new messages: each one carries its own
      `StatusCallback` (`https://notify-api.runvance.com/api/webhooks/providers/twilio/{configId}`,
      checked with that configuration's auth token). A tenant that set a status callback on a Twilio
      Messaging Service to the old `/api/webhooks/providers/twilio` removes it or uses the Channels URL.

## 4. Email

- [ ] Authenticate `runvance.com` at SendGrid / the email provider (SPF, DKIM, DMARC).
- [ ] Mailboxes: `info@`, `sales@`, `hello@`, `support@`, `admin@`, `privacy@`, `legal@` (`runvance.com`).
- [ ] Runvance Notify's platform sender tenant: sender address on runvance.com.

## 5. Customers and links already sent

- [ ] Customers using Runvance Notify's API move to `https://notify-api.runvance.com`.
- [ ] Customers' embedded CRM forms / snippets posting to `leads-api.payvizio.com/api/v1/public/leads`
      move to `crm-api.runvance.com`; check site keys' allowed websites.
- [ ] Links already emailed (CRM forms, questionnaires, proposals; demo invites on `demo.eduvizio.com`)
      break when the old hosts go: keep a redirect from each old host for a while, then remove it.
- [ ] WhatsApp flows saved from the CRM example still hold the old `/m/payvizio-pg` link in the database.

## 6. Remove the old domains (after the redirects have run their course)

- [ ] Azure: unbind `api.cliniqspace.com` (hms-prod-backend), `hms.cliniqspace.com` (hms-prod-frontend),
      and the CRM, Runvance Notify and Runvance Connect old hosts; delete the Runvance Health marketing Static Web
      App (`hms-prod-marketing` / `hms-marketing`) and its `AZURE_STATIC_WEB_APPS_TOKEN_MARKETING` secret.
- [ ] DNS: remove or redirect the old records (`leads`, `leads-api`, `notify`, `notify-api` on
      payvizio.com; cliniqspace.com, www, `hms`, `api`; `demo`, `api.demo` on eduvizio.com).
