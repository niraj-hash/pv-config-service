# DNS for runvance.com

`runvance.com` is registered at Namecheap and its DNS is served by **Azure DNS**: the zone `runvance.com`
in the resource group `rg-runvance-dns`. Moving it there (below, once) lets cert-manager issue the
`*.runvance.com` certificate by DNS-01, which pg-api-gateway's chart expects (docs/runtime.md, "The edge").

- **What the zone holds:** `deploy/dns/runvance.com.zone`, every record. Change a record in Azure and in
  that file together.
- **Check a nameserver against the file:** `scripts/dns-check.py <nameserver>` prints ok, MISSING or
  DIFFERS for each record set (the wildcard is checked with a random name). It cannot see records the
  file lacks.

## What points where

| Name | Points at | What it is |
|---|---|---|
| `*` | A `20.219.214.38` | The staging cluster's ingress. pg-api-gateway answers every product host of one label: accounts, crm, crm-api, health, connect-api, demo, notify, notify-api, meet-api, accelerator, and payroll, interview, connect, assessment and meetings once they move to it. Names it does not know get 404. |
| `payroll`, `interview`, `argocd` | A `20.219.214.38` | Explicit copies of what the wildcard gives; they predate it. |
| `api.interview` | A `20.219.214.38` | Two labels deep: the wildcard does not cover it, nor does the wildcard certificate. |
| `connect`, `assessment`, `meetings` | CNAME to Azure Container Apps, TTL 300 | The old Connect and Meet deployments, until their cutover (below). |
| `asuid.connect`, `asuid.assessment`, `asuid.meetings` | TXT | Container Apps' domain verification; removed with the CNAMEs. |
| `meet` | A `20.219.8.76` | Not in any configuration here (an older Meet deployment?). Find what serves it before removing it. |
| apex, `www` | A `20.109.151.31`, CNAME to Azure Static Web Apps | The website, with `asuid` and `asuid.www` TXT records verifying the domain to Azure. |
| apex MX, SPF TXT, `google._domainkey`, `_dmarc` | Google Workspace | The company mailboxes (@runvance.com). |
| apex TXT `google-site-verification=…` ×2, `_p99vdgnbmbnmbg0woj9ey55lcgzcrhg` | | Ownership checks; keep them. |

`20.219.214.38` is the AKS app-routing load balancer of `aks-interviewai-staging`; confirm with
`kubectl get svc -n app-routing-system nginx -o jsonpath='{.status.loadBalancer.ingress[0].ip}'`. When
production owns the hosts (docs/runtime.md: one environment at a time), `*`, `payroll`, `interview`,
`api.interview` change to its load balancer.

## Moving the zone from Namecheap (once)

Mail, the website and every product keep working through the move as long as Azure answers exactly
what Namecheap does. Change no record in either place from step 2 until step 6 has passed.

1. **Compare with Namecheap's list.** Namecheap -> Domain List -> runvance.com -> Advanced DNS (host
   records and mail settings). Every record there must be in `deploy/dns/runvance.com.zone`; add any
   that is not. `scripts/dns-check.py dns1.registrar-servers.com` should then report only the wildcard
   (new) as MISSING.
2. **Create the zone and import the file:**
   ```bash
   az group create -n rg-runvance-dns -l centralindia
   az network dns zone create -g rg-runvance-dns -n runvance.com
   az network dns zone import -g rg-runvance-dns -n runvance.com -f deploy/dns/runvance.com.zone
   az network dns zone show -g rg-runvance-dns -n runvance.com --query nameServers -o tsv
   ```
   The last command prints the four Azure nameservers. No resource lock: a CanNotDelete lock on the
   group or the zone also forbids deleting record sets (tested), which breaks cert-manager's DNS-01
   (it deletes its challenge record) and every record removal; the zone's own resource group, which
   nothing else shares, is the protection. Done on 2026-11-10: the zone exists, nameservers
   `ns1-03.azure-dns.com`, `ns2-03.azure-dns.net`, `ns3-03.azure-dns.org`, `ns4-03.azure-dns.info`.
3. **Check Azure before anyone uses it:** `scripts/dns-check.py <the first Azure nameserver>` must
   report every record set ok.
4. **Switch the nameservers:** Namecheap -> Domain List -> runvance.com -> Nameservers -> Custom DNS ->
   the four Azure names, without the trailing dot.
5. **Wait.** Resolvers keep the old nameservers for up to 48 hours (the .com delegation's TTL), so
   both providers answer for that long; that is why nothing may change in either.
6. **Check:** `dig +short NS runvance.com` lists Azure's names, `scripts/dns-check.py 1.1.1.1` and
   `scripts/dns-check.py 8.8.8.8` report every record set ok, and mail to an @runvance.com address
   arrives. From here on, records change only in Azure (and in the zone file).

## Certificates (once per cluster)

pg-api-gateway's wildcard Certificate uses the ClusterIssuer `letsencrypt-dns01`
(`deploy/cert-manager/letsencrypt-dns01.yaml`). DNS-01 works only once step 5 above is over:
Let's Encrypt asks the zone's own nameservers.

1. **cert-manager signs in with workload identity.** Its pods need the label; keep the installed
   chart version:
   ```bash
   V=$(helm list -n cert-manager -f '^cert-manager$' -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["chart"].removeprefix("cert-manager-"))')
   helm upgrade cert-manager jetstack/cert-manager -n cert-manager --version "$V" --reuse-values \
     --set-string 'podLabels.azure\.workload\.identity/use=true' \
     --set-string 'serviceAccount.labels.azure\.workload\.identity/use=true'
   ```
2. **An identity allowed only TXT records in the zone** (DNS Zone Contributor would also let it rewrite
   MX and every other record):
   ```bash
   SUB=$(az account show --query id -o tsv)
   ZONE_ID=$(az network dns zone show -g rg-runvance-dns -n runvance.com --query id -o tsv)
   az role definition create --role-definition "{
     \"Name\": \"DNS TXT Record Contributor\",
     \"Description\": \"Create and delete TXT records in a DNS zone (ACME DNS-01), nothing else.\",
     \"Actions\": [\"Microsoft.Network/dnszones/read\", \"Microsoft.Network/dnszones/TXT/read\",
                   \"Microsoft.Network/dnszones/TXT/write\", \"Microsoft.Network/dnszones/TXT/delete\"],
     \"AssignableScopes\": [\"/subscriptions/$SUB/resourceGroups/rg-runvance-dns\"]}"
   az identity create -g rg-runvance-dns -n id-cert-manager-dns-staging
   CLIENT_ID=$(az identity show -g rg-runvance-dns -n id-cert-manager-dns-staging --query clientId -o tsv)
   PRINCIPAL_ID=$(az identity show -g rg-runvance-dns -n id-cert-manager-dns-staging --query principalId -o tsv)
   az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
     --role "DNS TXT Record Contributor" --scope "$ZONE_ID"
   OIDC=$(az aks show -g rg-interviewai-staging -n aks-interviewai-staging --query oidcIssuerProfile.issuerUrl -o tsv)
   az identity federated-credential create -g rg-runvance-dns --identity-name id-cert-manager-dns-staging \
     -n cert-manager --issuer "$OIDC" --subject system:serviceaccount:cert-manager:cert-manager \
     --audiences api://AzureADTokenExchange
   ```
   A new role can take a few minutes to be usable everywhere.
3. **The ClusterIssuer**, with the same contact address as the HTTP-01 issuer:
   ```bash
   ACME_EMAIL=$(kubectl get clusterissuer letsencrypt-prod -o jsonpath='{.spec.acme.email}')
   sed -e "s/REPLACE-WITH-SUBSCRIPTION-ID/$SUB/" -e "s/REPLACE-WITH-CLIENT-ID/$CLIENT_ID/" \
       -e "s/REPLACE-WITH-ACME-EMAIL/$ACME_EMAIL/" deploy/cert-manager/letsencrypt-dns01.yaml | kubectl apply -f -
   kubectl get clusterissuer letsencrypt-dns01      # READY True: the ACME account exists
   ```
4. **The real test** is pg-api-gateway's first release: `kubectl -n pg-api-gateway get certificate`
   shows `runvance-com-wildcard` Ready within a few minutes. If it is not,
   `kubectl -n pg-api-gateway describe challenge` names the step that failed (usually the role, the
   federated credential's subject, or the nameservers not yet switched).

Production, when its cluster exists: a second identity, `id-cert-manager-dns-prod`, with the same role
on the same zone and a federated credential for that cluster's OIDC issuer; its own ClusterIssuer.

## Changing a record

```bash
az network dns record-set a add-record -g rg-runvance-dns -z runvance.com -n <name> -a <ip>
az network dns record-set cname set-record -g rg-runvance-dns -z runvance.com -n <name> -c <target>
az network dns record-set <type> delete -g rg-runvance-dns -z runvance.com -n <name> --yes
```

Then the same change in `deploy/dns/runvance.com.zone`, and `scripts/dns-check.py <an Azure nameserver>`.

## Later changes

- **Cutover of Connect and Meet** (after `demoagent-staging` and `meet-staging` are healthy behind the
  gateway): delete `connect`, `assessment`, `meetings` (CNAME) and their `asuid.*` TXT records; the
  wildcard answers them from then on. Payroll and Interview need no DNS change: their records already
  point at the cluster, only the Ingress that answers moves to the gateway.
- **Platform mail:** notification-service sends as `noreply@runvance.com` through SendGrid. Authenticate
  the domain in SendGrid (Settings -> Sender Authentication -> Authenticate a domain) and add the three
  CNAMEs it gives (`em…`, `s1._domainkey`, `s2._domainkey`); SPF stays as it is. Without them that mail
  fails DMARC alignment and tends to land in spam.
- **Old product hosts** stay where they are (cliniqspace.com and eduvizio.com at Namecheap, payvizio.com
  in Google Cloud DNS) and keep serving production until their replacement is live:
  `leads`/`leads-api.payvizio.com` -> `crm`/`crm-api`, `hms`/`api.cliniqspace.com` -> `health`,
  `demo`/`api.demo.eduvizio.com` -> `demo`/`connect-api`. Then a 301 to the new host (Namecheap's URL
  Redirect record; for payvizio.com, on the web host) or removal.
