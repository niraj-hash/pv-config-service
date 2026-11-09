# Deploy and roll back with Argo CD

Owner's decision, 9 November 2026: **every Runvance service deploys only when someone presses Sync in
Argo CD, and rolls back from Argo CD's History.** Images are built by hand (`az acr build`, through
`scripts/release.sh`); there is no CI and nothing deploys by itself. Argo CD is the one on the staging
cluster (`https://argocd.runvance.com`, argo-cd chart 10.9.2 / v3.5.3, managed from
interview-ai-management `infrastructure/argocd/`).

```
scripts/release.sh <svc> <env> --build    az acr build -> <acr>/<image>:<short sha>
        |                                  writes deploy/releases/<env>/<svc>.yaml (chart commit + tags)
git push (pv-config-service main)          the ApplicationSet picks it up: <svc>-<env> is OutOfSync
        |                                  ... nothing happens until ...
Sync (UI or argocd app sync)               Argo CD renders the chart at that commit with the release file
History -> Rollback                         re-applies an earlier deployment (chart commit + release file)
```

## What is where

| What | Where |
|---|---|
| The root, synced by a platform admin | interview-ai-management `infrastructure/argocd/projects/runvance/runvance-platform.yaml` -> this repository's `deploy/argocd/` |
| Project, roles, allowed repositories and namespaces | `deploy/argocd/appproject.yaml` (AppProject `runvance`) |
| One Application per service and environment | `deploy/argocd/applicationset-staging.yaml`, `applicationset-prod.yaml`: `<service>-staging`, `<service>-prod` |
| What a Sync deploys | `deploy/releases/<env>/<service>.yaml`: `chartRevision` (the chart's commit in the service's repository) and the image tags; it is the chart's last value file |
| How to build each service | `deploy/services.yaml` (repository, images, Dockerfiles, contexts, registries) |
| Repository and ACR credentials | interview-ai-management `infrastructure/argocd/platform/repo-credentials-runvance-externalsecret.yaml` |
| Consul configuration | the Application `consul-config-<env>` (chart `deploy/consul-config`) |

Services (`<service>` in every command): `pg-api-gateway`, `pv-auth-service`, `notification-service`
(with notification-web), `ai-service`, `consul-config`, `leados` (Runvance CRM), `peoplevizio`
(Payroll), `hms` (Health), `demoagent` (Connect), `meet`, `interview`, `runvance-accelerator`.

A service whose release file says `chartRevision: "unreleased"` has no Application yet; its first
release creates it. Prod Applications appear once the prod cluster is registered as
`aks-runvance-prod` (until then the ApplicationSet's status says the destination does not exist).

## Deploy a version

1. **Release** (from this checkout; the service's repository checked out beside it, at the commit to
   deploy, committed and pushed):
   ```bash
   scripts/release.sh pg-api-gateway staging --build      # az acr build each image at HEAD, tag = short SHA
   scripts/release.sh pg-api-gateway staging --tag 3f2a9c1 # or: images built earlier
   ```
   `--build` refuses uncommitted changes (`--force` builds anyway and tags `<sha>-dirty-<time>`). The
   chart is pinned to the same commit as the images (`--chart-revision <ref>` to choose; in staging a
   branch name such as `main` makes every Sync render that branch's latest chart). The script checks
   the tags exist in ACR, checks every release file (`scripts/releases.py check`), renders the chart
   when the checkout is at that commit, and prints the commands below. `--dry-run` shows the file
   without writing it.
2. **Push** the release file to `main` (a branch and pull request, or straight to main):
   ```bash
   git add deploy/releases/staging/pg-api-gateway.yaml
   git commit -m "Release pg-api-gateway to staging: pg-api-gateway:3f2a9c1"
   git push origin HEAD:main
   ```
   Within about 3 minutes (the ApplicationSet's and Argo CD's polling) `pg-api-gateway-staging` is
   **OutOfSync**. Nothing has changed in the cluster.
3. **Look, then Sync.**
   - UI: Applications -> `pg-api-gateway-staging` -> **Refresh** (to skip the wait) -> **App Diff**
     -> **Sync** -> **Synchronize**. Leave the options unset; pruning is always last (`PruneLast`).
   - CLI:
     ```bash
     argocd login argocd.runvance.com --sso
     argocd app get pg-api-gateway-staging --refresh
     argocd app diff pg-api-gateway-staging
     argocd app sync pg-api-gateway-staging
     argocd app wait pg-api-gateway-staging --health --timeout 900
     ```
   A Sync applies with server-side apply, creates the namespace where the service owns it, waits
   for hooks (Interview's migration, consul-config's job) and fails if another Application already
   tracks the same resources (`FailOnSharedResource`).

## Roll back

- **UI**: the Application -> **History and Rollback** -> the deployment to return to -> **...** ->
  **Rollback**. It re-applies that deployment exactly: the chart's commit and the release file's
  commit it ran with (history keeps 20).
- **CLI**:
  ```bash
  argocd app history pg-api-gateway-staging          # ID, date, the revisions of both sources
  argocd app rollback pg-api-gateway-staging <ID>
  argocd app wait pg-api-gateway-staging --health
  ```
- **Then make it stick**: after a rollback the Application is OutOfSync again (main still names
  the newer release), and the next Sync would redeploy it. Revert the release commit and push:
  `git revert <commit>; git push origin HEAD:main`. (Reverting first and pressing Sync is the same
  rollback done through git.)
- Older than the history: release the old tag again, `scripts/release.sh <svc> <env> --tag <old sha>`.
- Databases are not rolled back: migrations are forward-only (Interview's PreSync job, Flyway at
  start elsewhere), so an earlier image must work with the newer schema (expand, migrate, contract).
- `consul-config`: a rollback re-publishes the earlier commit's configuration (`--prune` removes keys
  the later one added); restart the services that read it.

## Promote staging to prod

```bash
scripts/release.sh hms prod --from staging --import   # staging's tags and chart commit; az acr import into the prod ACR
git add deploy/releases/prod/hms.yaml && git commit -m "Promote hms to prod" && git push origin HEAD:main
argocd app diff hms-prod && argocd app sync hms-prod && argocd app wait hms-prod --health
```

Prod always pins a commit SHA. If staging tracks a branch, pass the commit staging actually runs:
`argocd app get hms-staging -o json | jq -r '.status.sync.revisions[0]'`, then
`--chart-revision <sha>`. Without `--import` the script prints the `az acr import` commands, and its
image check fails until the images are in the prod registry.

## See what is deployed

| Question | Answer |
|---|---|
| What runs, per service | `argocd app list -l runvance.com/environment=staging` (or the UI, filtered by that label): sync status, health, revision |
| Which images | `argocd app get <app> -o json \| jq '.status.summary.images'` |
| Which commits | `argocd app get <app> -o json \| jq '.status.sync.revisions'` (chart, release file) |
| What was deployed before, when, by whom | `argocd app history <app>`; the UI's History and Rollback |
| What is released but not yet deployed | the Application is OutOfSync; `deploy/releases/<env>/` on main is the release, `argocd app diff <app>` the difference |
| Logs | the UI: a pod -> Logs (deployers and viewers) |

## Who may do what

Roles come from Entra ID groups (the `groups` claim of Argo CD's OIDC login); the group object ids
go in `deploy/argocd/appproject.yaml`.

| Who | May |
|---|---|
| `role:platform-admin` (existing group, Interview's `install/values-argocd.yaml`) | everything, including syncing `runvance-platform` (the AppProject and the ApplicationSets), clusters and repositories |
| `proj:runvance:deployer` | see every Runvance Application, its diff, history and logs; **Sync**; **Rollback**; **Restart** a Deployment (resource action). Not: delete Applications, sync with local manifests or parameter overrides, exec |
| `proj:runvance:viewer` | see Applications, diffs, history, logs |
| Whoever can push to pv-config-service `main` | change what a Sync would deploy (release files) — never deploys by itself; deployers read the diff before pressing Sync |

`exec` is off for everyone (`exec.enabled: "false"`). Restart from the CLI:
`argocd app actions run <app> restart --kind Deployment --resource-name <name>`.

## Consul configuration

`consul-config-<env>` publishes `config/` like a deploy:

1. Merge the configuration change to `main` (`python3 scripts/validate.py` first: no CI runs it).
2. `scripts/release.sh consul-config staging` pins the chart to HEAD (its tools image is unchanged;
   `--build` only to rebuild that image), push the release file.
3. **App Diff** shows the configuration change (the ConfigMap `consul-config-files` holds `config/`
   and the two scripts at that commit). **Sync**: the ConfigMap is applied, then the PostSync job
   `consul-config-sync` runs `scripts/sync.sh --prune`, which validates first and publishes nothing
   if validation fails (the sync then fails and notifies). Its logs: the job's pod in the UI.
4. Restart what reads the change: pg-api-gateway (routes), pv-auth-service (catalog), or the service
   whose `config/<service>` changed (Restart action).

`scripts/sync.sh` by hand remains for a local Consul (README, "Local").

## Set up once

1. **Commit and push** this repository (`main`) and each service's chart to the commits the releases
   will name; merge interview-ai-management's `infrastructure/argocd/` changes to `phase-3/main`
   (the branch Argo CD reads) only after steps 2 to 4, or argocd-platform turns Degraded.
2. **The library chart in ACR** (once, then once per library version; below).
3. **Repository credentials**, in Key Vault `kv-iai-staging` (the commands are in the header of
   `infrastructure/argocd/platform/repo-credentials-runvance-externalsecret.yaml`): a read-only SSH
   deploy key for each of pv-config-service, pg-api-gateway, pv-auth-service, notification-service,
   runvance-ai-service, PayVizio-LeadOS, hr-ai-solution, hms, ai-demo-agent and runvance
   (interview-ai-management has one), and the ACR token `argocd-helm-pull` (pull-only on
   `helm/runvance-service`).
4. **Consul**, per environment: `scripts/bootstrap-acl.sh --tokens` creates the config-loader token;
   `az keyvault secret set --vault-name <vault> -n pv-config-service-config-loader-token --file secrets/config-loader.token`.
5. **Groups**: create two Entra ID security groups (Interview's `infrastructure/argocd/entra/README.md`
   shows the commands; the app registration already emits group claims), e.g. "Runvance deployers"
   and "Runvance viewers", and put their object ids in `deploy/argocd/appproject.yaml`.
6. **The root**: once `infrastructure/argocd/projects/runvance/runvance-platform.yaml` is on
   `phase-3/main`, argocd-platform creates `runvance-platform` (OutOfSync); a platform admin syncs it
   (`argocd app sync runvance-platform`). That creates the AppProject and the ApplicationSets; each
   service's Application appears with its first release.
7. **Notifications**: the triggers exist (`on-deployed` = sync succeeded and healthy,
   `on-sync-failed`, `on-health-degraded`; email through the ACS SMTP relay, already configured).
   Uncomment the three `notifications.argoproj.io/subscribe.*.email` annotations in
   `deploy/argocd/appproject.yaml` with a real mailbox, push, sync `runvance-platform`. Slack or Teams:
   their secrets and services, as Interview's `infrastructure/argocd/notifications/README.md` says.
8. **Prod**, when the cluster exists: `argocd cluster add <context> --name aks-runvance-prod`; the prod
   ACR (`acrrunvanceprod` in `deploy/services.yaml` and the charts' `values-prod.yaml` is a
   placeholder) attached to the cluster; the prod Key Vault entries; Consul and External Secrets
   there first (docs/runtime.md, install order).

## The library chart

`file://` dependencies across repositories cannot resolve in Argo CD, so the runvance-service
library is **published to ACR as an OCI chart**, `oci://acrinterviewaistaging.azurecr.io/helm` (one
registry for both environments: Argo CD's repo-server fetches it, not the clusters). Argo CD logs in
with the Helm OCI repository credential `argocd-repo-runvance-helm` (ExternalSecret, ACR token) and
runs `helm dependency build` from each chart's `Chart.lock`; `charts/` stays git-ignored. Offline,
`deploy/charts/check.sh` packages this checkout's library into the chart's `charts/` instead.

Publish (once per version; a published version is never overwritten):

```bash
helm package deploy/charts/runvance-service -d /tmp
az acr login -n acrinterviewaistaging
helm push /tmp/runvance-service-0.1.0.tgz oci://acrinterviewaistaging.azurecr.io/helm
az acr repository update -n acrinterviewaistaging --image helm/runvance-service:0.1.0 --write-enabled false
```

The platform charts (pg-api-gateway, pv-auth-service, notification-service) already depend on it;
ai-service's chart does not use the library. **A product chart changes one line** in `Chart.yaml`:

```yaml
dependencies:
  - name: runvance-service
    version: 0.1.0
    repository: oci://acrinterviewaistaging.azurecr.io/helm   # was file://../../../../pv-config-service/deploy/charts/runvance-service
```

and its `Chart.lock`: `helm dependency update` once the library is published, or copy
pg-api-gateway's `deploy/helm/pg-api-gateway/Chart.lock` (same dependency, same digest). A new library
version: bump `version` in `deploy/charts/runvance-service/Chart.yaml`, publish, then each chart bumps
its dependency and lock and is released like any chart change.

## First deploys: services already running

- **Changed selectors.** A Deployment's selector cannot change, so a Sync fails with "field is
  immutable" where a chart's labels differ from what runs. Payroll's do (`deploy/helm/peoplevizio`
  moved to the library), and Interview's (below). Before the first Sync compare
  `kubectl -n <ns> get deploy -o custom-columns=NAME:.metadata.name,SELECTOR:.spec.selector.matchLabels`
  with `argocd app diff <app>`; for Payroll, `kubectl -n peoplevizio delete deployment
  peoplevizio-api peoplevizio-web` (minutes of downtime), then Sync. Alternatively Sync with
  **Replace** and **Force** ticked, which deletes and recreates the conflicting objects.
- **Installed with Helm before.** Argo CD takes the objects over (server-side apply). Afterwards
  delete the old release records, `kubectl -n <ns> delete secret -l owner=helm,name=<release>`; never
  `helm uninstall`, which deletes what Argo CD now runs.
- **Leftovers** the new chart no longer renders stay; delete them by hand once the Application is
  healthy.

## Moving Interview

Interview's `interview-ai-staging` came from its own ApplicationSet with auto-sync, an Image Updater
annotation and a cascading finalizer. It becomes `interview-staging` here, from the library-based
chart (`infrastructure/helm/interview-ai`, release name `interview-ai`, `values-staging.yaml`), once
that chart and its code are on a pushed commit.

1. **Merge** interview-ai-management's `infrastructure/argocd/` changes to `phase-3/main`.
   `interview-ai-staging` is then manual, without Image Updater, and without the resources
   finalizer. Check both:
   `kubectl -n argocd get application interview-ai-staging -o jsonpath='{.spec.syncPolicy}{"\n"}{.metadata.finalizers}'`
   (no `automated`, no finalizer). If a hand-applied `interviewai-staging-azure` or
   `interviewai-staging` exists (`argocd app list`), `argocd app delete <it> --cascade=false`.
2. **Hand over**: delete `infrastructure/argocd/projects/interview-ai/staging.appset.yaml` on
   `phase-3/main`. Interview's ApplicationSet deletes `interview-ai-staging` and leaves everything
   running (no finalizer).
3. **Release**: `scripts/release.sh interview staging --build` (from the checkout at the commit with
   the new chart), push. `interview-staging` appears OutOfSync.
4. **Cut over**: the selectors change (release `interview-ai-staging` -> `interview-ai`, library
   labels), so delete the old Deployments the diff shows (`kubectl -n interviewai get deploy`), then
   Sync; then delete what the old release left
   (`kubectl -n interviewai get all,ingress,hpa,pdb,networkpolicy,configmap -l app.kubernetes.io/instance=interview-ai-staging`).
5. Later: the `interview-ai` AppProject and its RBAC block in `install/values-argocd.yaml` can go;
   `infrastructure/argocd/production-application.yaml` stays while Interview's compliance evidence
   (`scripts/compliance/evidence.py`, CC8.1) reads it.

## Add a service

1. Its chart (docs/runtime.md, "Add a service"), depending on the library from ACR.
2. `deploy/services.yaml`: repository, images, Dockerfiles, contexts, the values each tag sets.
3. An element in both `deploy/argocd/applicationset-*.yaml` (repoURL, chartPath, releaseName,
   namespace, createNamespace, valueFiles); its repository in `appproject.yaml`'s `sourceRepos` and
   its namespace in `destinations`, a deploy key (step 3 above) if the repository is new.
4. `deploy/releases/<env>/<service>.yaml` with `chartRevision: "unreleased"` (copy one; `releaseOf`
   is the service).
5. `python3 scripts/releases.py check`, push, sync `runvance-platform` (a platform admin), then
   release it.

## Checks before pushing

Nothing runs these for you:

```bash
python3 scripts/validate.py                     # config/
python3 scripts/releases.py check               # release files, services.yaml, ApplicationSets, AppProject agree
deploy/charts/check.sh [<chart dir>...]          # lint and render charts on the library, offline
argocd appset generate deploy/argocd/applicationset-staging.yaml   # the Applications it would make (needs a login)
```

## When something is off

| Symptom | Cause |
|---|---|
| No Application for a service | its release file is `unreleased`; or (prod) `aks-runvance-prod` is not registered; `kubectl -n argocd get applicationset runvance-<env> -o jsonpath='{.status.conditions}'` |
| ComparisonError "unable to resolve" | the release names a commit that is not pushed |
| "failed to ... runvance-service" | the library is not in ACR, or the `argocd-repo-runvance-helm` credential is missing |
| Sync fails: shared resource | another Application (Interview's old one) still tracks those objects (Moving Interview, step 2) |
| consul-config sync failed | the job's logs: validation output, or Consul refused the token (`pv-config-service-config-loader-token`) |
| OutOfSync right after a Sync | a field the chart sets is changed by a controller; add an `ignoreDifferences` to the ApplicationSet template |
