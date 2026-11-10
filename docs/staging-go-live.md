# Staging go-live: hosts, sign-in, and deploying every service

State on 10 November 2026, and what remains before every Runvance service runs on the staging cluster
(`aks-interviewai-staging`) behind pg-api-gateway. Deploying itself is in
[deploy-with-argocd.md](deploy-with-argocd.md); DNS and certificates in [dns.md](dns.md).

## Where things stand

| | State |
|---|---|
| Argo CD | `argocd.runvance.com`. `runvance-platform` synced: AppProject `runvance`, ApplicationSets `runvance-staging` and `runvance-prod`. |
| Consul | Installed (`consul` namespace, one server). The 12 ACL tokens exist in Consul; storing them in Key Vault is pending. |
| consul-config | Released (`pv-config-sync:ef53bd8`), not synced. |
| Interview | Its old Application `interview-ai-staging` is gone (handover merged); the pods it deployed keep running, unmanaged, until `interview-staging` is released. |
| Every other Runvance service | Not deployed. No image built, most databases and Key Vault entries missing (below). |
| Old production, still serving | CRM `leads.payvizio.com`, Health `hms.cliniqspace.com`, Connect `demo.eduvizio.com` / `connect.runvance.com` / `assessment.runvance.com`, Meet `meetings.runvance.com`, the old Payroll on `payroll.runvance.com` (the superseded `runvance-platform` chart). |

## Hosts and sign-in

Every product signs people in through **Runvance accounts** (`accounts.runvance.com`, pv-auth-service
behind the gateway): one email and password for every app. A product's **Sign in** goes to
`accounts.runvance.com/login` and comes back signed in; products never see a password or a token
(the gateway holds the session).

| App | Address | Who can join |
|---|---|---|
| Runvance accounts | `accounts.runvance.com`: `/login`, `/signup`, `/home` (the app launcher), `/team` (members), `/account` | Anyone, by signing up for an app below |
| Runvance CRM | `crm.runvance.com` (API `crm-api.runvance.com`) | Self sign-up |
| Runvance Payroll | `payroll.runvance.com` | Self sign-up |
| Runvance Health | `health.runvance.com` | Self sign-up |
| Runvance Connect | `connect.runvance.com` and `assessment.runvance.com` (consoles), `demo.runvance.com` (the public demo), API `connect-api.runvance.com` | Self sign-up |
| Runvance Meet | `meetings.runvance.com` (API `meet-api.runvance.com`) | Self sign-up; a new person waits for Meet's super admin to approve them |
| Runvance Notify | `notify.runvance.com` (API `notify-api.runvance.com`) | Self sign-up; a new organization waits for approval |
| Runvance Interview | `interview.runvance.com` (API `api.interview.runvance.com`) | Invitation only |
| Runvance Accelerator | `accelerator.runvance.com` | Invitation only (Runvance staff) |
| Argo CD | `argocd.runvance.com` | Microsoft (Entra ID) sign-in; the CLI: `argocd login argocd.runvance.com --sso --grpc-web` |

- **Sign up:** `accounts.runvance.com/signup` (or `/signup?apps=leados,payroll`) creates a new
  organization with the chosen apps; the person becomes its admin. Joining an existing organization
  is never self-service: its admins invite people from `/team` (each app links to it as `/bff/team`).
- **The first account** is the platform admin pv-auth-service creates once at its first start, from
  the Key Vault entries `pv-auth-service-bootstrap-admin-email` and `-password` (set them before
  releasing pv-auth-service). Sign in with it at `accounts.runvance.com/login`; delete both entries
  afterwards (they are read once). Invitation-only apps (Interview, Accelerator) start from it.
- **Argo CD's web sign-in** fails until the Secret `argocd-oidc-entra` carries the label
  `app.kubernetes.io/part-of: argocd` (Argo CD otherwise sends a placeholder instead of the client
  secret). The command-line sign-in does not use that secret and works.

## What each service needs before its first Sync

**Images:** none of these is in `acrinterviewaistaging` yet; `scripts/release.sh <service> staging
--build` builds them (from the service's repository at `main`, committed and pushed).

**Databases** on `psql-interviewai-staging` (each with its own role, named in the chart's
`values-staging.yaml`, whose password is the `<service>-db-password` entry): to create `pv_auth`,
`ai_service`, `leados`, `demoagent`, `meet`, `runvance_accelerator`. `peoplevizio` and `interviewai`
exist. notification-service and Health use MongoDB: their connection strings go into Key Vault.

**Key Vault** (`kv-iai-staging`): each chart's required group; a missing entry keeps the pod from
starting. Optional groups (a payment provider, an OAuth app, WhatsApp, …) only switch a feature on.
`scripts/keyvault-bootstrap.py` reports what is missing; with `--apply` it generates every internal
entry in the format its service checks (never changing one that exists, never showing a value), and
with `--apply --prompt` asks for the ones only you can give. It never generates Payroll's database
password and payout key or the platform Redis password: those protect data that exists.

```bash
az keyvault network-rule add --name kv-iai-staging --ip-address <your IP>/32 -o none   # then wait a minute
scripts/keyvault-bootstrap.py                    # what exists, what it would make, what is yours to give
scripts/keyvault-bootstrap.py --apply --prompt   # make the internal ones; type yours (hidden), Enter skips
az keyvault network-rule remove --name kv-iai-staging --ip-address <your IP>/32 -o none
```

The bootstrap admin's first password is generated: read it once to sign in
(`az keyvault secret show --vault-name kv-iai-staging -n pv-auth-service-bootstrap-admin-password --query value -o tsv`);
pv-auth-service asks for a new one at that first sign-in.

| Service | Required entries | Supplied by |
|---|---|---|
| all | `<service>-consul-token` (Payroll: `peoplevizio-consul-token`), `pv-config-service-config-loader-token` | `scripts/bootstrap-acl.sh --tokens` (docs/runtime.md) |
| pg-api-gateway | `pv-gateway-client-secret`, `runvance-redis-password` | generated (the first shared with pv-auth-service); the platform Redis's password |
| pv-auth-service | `pv-auth-service-db-password`, `pv-auth-service-signing-key`, `pv-gateway-client-secret` | generated (the signing key: a PKCS#8 RSA key); optional: `-bootstrap-admin-*`, `-bootstrap-client-*`, `-notify-api-key` |
| notification-service | `notification-service-encryption-key`, `notification-service-mongodb-uri` | generated; your MongoDB. Optional: SendGrid, FCM, VAPID |
| ai-service | `ai-service-db-password`, `ai-service-credentials-key`, `runvance-redis-password`, `ai-service-{openai,anthropic,gemini,deepgram,azure-speech}-*-key` | generated; the AI providers' keys (all five are in the required group as the chart stands) |
| CRM (leados) | `leados-api-db-password`, `leados-api-field-encryption-key` | generated. Optional: Google, Meta, WhatsApp, SendGrid, PayVizio payments, service client |
| Payroll (peoplevizio) | `peoplevizio-db-password`, `peoplevizio-payout-enc-key`, `peoplevizio-superadmin-email`, `runvance-redis-password` | all four already used by the running Payroll (among them its database's password and the key its stored bank accounts are encrypted with): keep them, never generate new ones. Optional: Razorpay, Slack, Teams, WhatsApp |
| Health (hms) | `hms-api-mongodb-url`, `hms-api-phi-encryption-key` (never rotate), `hms-api-redis-password` | your MongoDB; generated; the access key of its Azure Cache for Redis, `hms-staging-redis`, which does not exist yet (create it, or point Health at the platform Redis in its chart). Optional: PayU, ABDM, DigiLocker, e-sign, GST, labs, PACS, storage |
| Connect (demoagent) | `demoagent-platform-db-password`, `-encryption-key`, `-jwt-secret`, `-service-client-secret`, `-leados-api-key`, `-leados-status-secret`, `demoagent-worker-token`, `runvance-jitsi-jwt-secret` | generated (`-leados-status-secret` is the same value as CRM's `leados-api-integrations-callback-secret`); a CRM workspace API key, made in CRM once it runs (a placeholder until then); the Jitsi VM's `JWT_APP_SECRET` (`meet.runvance.com`). Optional: Google, Microsoft, EduVizio, ipinfo |
| Meet | `meet-api-db-password`, `meet-api-secret`, `meet-api-service-client-secret`, `runvance-jitsi-jwt-secret` | generated; the Jitsi VM's `JWT_APP_SECRET` |
| Accelerator | `runvance-accelerator-db-password` | generated. Optional: Notify client, Odoo |
| Interview | `interviewai-staging-*` | exist (Interview runs today) |

A `-service-client-secret` is also the secret of that product's service client in pv-auth-service
(registered through its admin API once pv-auth-service runs), and `pv-auth-service-notify-api-key` is
the platform tenant's API key at notification-service.

**Also before the gateway's first Sync:**
- DNS moved to Azure DNS and the `letsencrypt-dns01` issuer in place ([dns.md](dns.md)); without
  them the `*.runvance.com` certificate never issues.
- The platform Redis's NetworkPolicy (in the old `runvance-platform` chart) admits `pg-api-gateway`
  and `runvance-ai`.
- The old chart's `edge` Ingress stops claiming hosts the gateway takes over (two Ingresses for one
  host conflict): do this at Payroll's cutover.

## Deploy order

Each step: the service's database and Key Vault entries; then, from this repository,
`scripts/release.sh <service> staging --build --repo-dir <its checkout at main>`; push the release
file to `main`; `argocd app diff <service>-staging`, then `argocd app sync <service>-staging` and wait
for Healthy (deploy-with-argocd.md).

1. **consul-config**: store the Consul tokens, then Sync (released already). Consul then holds every
   service's configuration.
2. **pv-auth-service** with the bootstrap admin and client entries. Sign in at
   `accounts.runvance.com/login` once the gateway is up.
3. **notification-service**; then create the platform tenant's API key and store it as
   `pv-auth-service-notify-api-key` (pv-auth-service picks it up after a restart).
4. **ai-service.**
5. **pg-api-gateway**, once the wildcard certificate can issue. `accounts.runvance.com` answers from
   here on.
6. **Products**, one at a time: CRM, Payroll, Health, Connect, Meet, Accelerator, then Interview.
   After each, register its service client in pv-auth-service with its `-service-client-secret`.
   - Payroll's Deployment selectors change: delete `peoplevizio-api` and `peoplevizio-web` just
     before its Sync (minutes of downtime), and remove the old chart's Ingress for
     `payroll.runvance.com` (deploy-with-argocd.md, "First deploys").
   - Interview: release `interview` from interview-ai-management `main`, then the cut-over in
     deploy-with-argocd.md, "Moving Interview", steps 3 and 4.
   - Connect and Meet: once healthy, switch `connect`, `assessment` and `meetings` from their
     Container Apps CNAMEs to the cluster (dns.md, "Later changes").
7. **Old hosts**: redirect `leads.payvizio.com`, `hms.cliniqspace.com`, `demo.eduvizio.com` and
   their APIs to the new hosts only after their replacement is live and its data has moved.

The repositories with a dirty checkout here (PayVizio-LeadOS, hr-ai-solution, ai-demo-agent) build from
a clean worktree at `main`: `git -C <repo> worktree add ../<repo>.release origin/main`, then
`--repo-dir ../<repo>.release`.
