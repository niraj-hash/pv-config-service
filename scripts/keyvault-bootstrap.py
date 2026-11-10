#!/usr/bin/env python3
"""Fills Key Vault with the entries the Runvance services read at start, never showing a value.

    scripts/keyvault-bootstrap.py                     # report only: what exists, what it would make, what is yours to give
    scripts/keyvault-bootstrap.py --apply             # also generate every missing internal entry
    scripts/keyvault-bootstrap.py --apply --prompt    # and ask (hidden input) for each missing entry only you can give
    options: --vault <name> (default kv-iai-staging)

It never changes an entry that exists. Kinds of entry:
  generated  made here in the format the service checks (passwords, AES keys, HMAC secrets, the RSA
             signing key, service-client secrets); the same value for entries that must match
  yours      a value from outside: a provider's key, a connection string, an address
  existing   protects data that already exists (Payroll's database and payout key, the platform
             Redis): copied from the running deployment, never generated
  consul     the services' Consul tokens: scripts/bootstrap-acl.sh --tokens (docs/runtime.md)

Interview's entries exist already and are not listed. A database password generated here is also the
password of that database's role, created afterwards (docs/staging-go-live.md). Values go to the Azure
CLI through a private temporary file, never on a command line. Needs the Azure CLI signed in, your IP
allowed through the vault's firewall, and openssl. Exit status 1 while a required entry is missing.
"""
import argparse
import base64
import getpass
import os
import secrets
import string
import subprocess
import sys
import tempfile

# (name, kind, how, required, note). `how`: the generator for generated entries, the source entry for
# "same", otherwise unused.
ENTRIES = [
    # --- platform ---
    ("pv-gateway-client-secret", "generated", "token", True, "pg-api-gateway and pv-auth-service: the gateway's clients' secret"),
    ("runvance-redis-password", "existing", None, True, "the platform Redis (runvance-platform namespace); gateway, ai-service, Payroll"),
    ("pv-auth-service-db-password", "generated", "password", True, "pv-auth-service: role pv_auth"),
    ("pv-auth-service-signing-key", "generated", "rsa", True, "pv-auth-service: the token signing key (PKCS#8 RSA 2048)"),
    ("pv-auth-service-bootstrap-admin-email", "yours", None, True, "the first platform admin's email address"),
    ("pv-auth-service-bootstrap-admin-password", "generated", "admin-password", True, "the first platform admin's first password (changed at first sign-in)"),
    ("pv-auth-service-bootstrap-client-id", "generated", "literal:runvance-automation", True, "pv-auth-service: the platform automation client"),
    ("pv-auth-service-bootstrap-client-secret", "generated", "token", True, "pv-auth-service: the platform automation client's secret"),
    ("notification-service-encryption-key", "generated", "key32", True, "notification-service: APP_ENCRYPTION_KEY"),
    ("notification-service-mongodb-uri", "yours", None, True, "notification-service: its MongoDB connection string"),
    ("ai-service-db-password", "generated", "password", True, "ai-service: role ai_service"),
    ("ai-service-credentials-key", "generated", "key32", True, "ai-service: AI_CREDENTIALS_KEY (tenants' own provider keys)"),
    ("ai-service-openai-api-key", "yours", None, True, "OpenAI API key"),
    ("ai-service-anthropic-api-key", "yours", None, True, "Anthropic API key"),
    ("ai-service-gemini-api-key", "yours", None, True, "Google Gemini API key"),
    ("ai-service-deepgram-api-key", "yours", None, True, "Deepgram API key"),
    ("ai-service-azure-speech-key", "yours", None, True, "Azure Speech key"),
    # --- CRM ---
    ("leados-api-db-password", "generated", "password", True, "CRM: role leados"),
    ("leados-api-field-encryption-key", "generated", "key32", True, "CRM: FIELD_ENCRYPTION_KEY"),
    ("leados-api-integrations-callback-secret", "generated", "token", False, "CRM: WhatsApp status callbacks to Connect (the same value as Connect's)"),
    ("leados-api-service-client-secret", "generated", "token", False, "CRM: its service client (AI); register it in pv-auth-service"),
    # --- Payroll ---
    ("peoplevizio-db-password", "existing", None, True, "Payroll: the existing peoplevizio database's password"),
    ("peoplevizio-payout-enc-key", "existing", None, True, "Payroll: the key existing bank accounts are encrypted with"),
    ("peoplevizio-superadmin-email", "existing", None, True, "Payroll: its super admin's email"),
    ("peoplevizio-service-client-secret", "generated", "token", False, "Payroll: its service client; register it in pv-auth-service"),
    # --- Health ---
    ("hms-api-mongodb-url", "yours", None, True, "Health: its MongoDB connection string"),
    ("hms-api-phi-encryption-key", "generated", "key32", True, "Health: PHI_ENCRYPTION_KEY (never rotate)"),
    ("hms-api-redis-password", "yours", None, True, "Health: the access key of its Azure Cache for Redis (hms-staging-redis, not created yet)"),
    ("hms-api-service-client-secret", "generated", "token", False, "Health: its service client; register it in pv-auth-service"),
    # --- Connect ---
    ("demoagent-platform-db-password", "generated", "password", True, "Connect: role of the demoagent database"),
    ("demoagent-platform-encryption-key", "generated", "key32", True, "Connect: PLATFORM_ENCRYPTION_KEY (exactly 32 bytes)"),
    ("demoagent-platform-jwt-secret", "generated", "token", True, "Connect: PLATFORM_JWT_SECRET (48+ characters)"),
    ("demoagent-platform-service-client-secret", "generated", "token", True, "Connect: its service client; register it in pv-auth-service"),
    ("demoagent-platform-leados-status-secret", "same", "leados-api-integrations-callback-secret", True, "Connect: checks CRM's WhatsApp status callbacks"),
    ("demoagent-platform-leados-api-key", "yours", None, True, "Connect: a CRM workspace API key (made in CRM once it runs; a placeholder lets Connect start)"),
    ("demoagent-worker-token", "generated", "token", True, "Connect: its browser worker's token (32+ characters)"),
    ("runvance-jitsi-jwt-secret", "yours", None, True, "Connect and Meet: the Jitsi VM's JWT_APP_SECRET"),
    # --- Meet ---
    ("meet-api-db-password", "generated", "password", True, "Meet: role of the meet database"),
    ("meet-api-secret", "generated", "token", True, "Meet: MEET_SECRET, the key behind invite links (32+ characters)"),
    ("meet-api-service-client-secret", "generated", "token", True, "Meet: its service client; register it in pv-auth-service"),
    # --- Accelerator ---
    ("runvance-accelerator-db-password", "generated", "password", True, "Accelerator: role of the runvance_accelerator database"),
    # --- Consul ---
    ("pv-config-service-config-loader-token", "consul", None, True, "consul-config"),
] + [
    (f"{svc}-consul-token", "consul", None, True, svc)
    for svc in ["pg-api-gateway", "pv-auth-service", "notification-service", "ai-service", "leados-api",
                "peoplevizio", "hms-api", "demoagent-platform", "meet-api", "runvance-accelerator", "interview-api"]
]

PROVIDER_NOTE = ("a provider you won't use still needs an entry for ai-service to start; ai-service then "
                 "treats it as configured, so route nothing to it (config/ai-service)")


def generate(how):
    if how == "password":            # PostgreSQL role passwords: letters and digits, safe in URLs
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(32))
    if how == "admin-password":      # pv-auth-service wants 10+ characters; changed at first sign-in
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(24))
    if how == "key32":               # AES-256: base64 of exactly 32 random bytes
        return base64.b64encode(secrets.token_bytes(32)).decode()
    if how == "token":               # HMAC and client secrets: 64 URL-safe characters
        return secrets.token_urlsafe(48)
    if how == "rsa":                 # pv-auth-service: PKCS#8 PEM
        return subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
                              check=True, capture_output=True, text=True).stdout
    if how.startswith("literal:"):
        return how.split(":", 1)[1]
    raise ValueError(how)


class Vault:
    def __init__(self, name):
        self.name = name
        self.tmp = tempfile.mkdtemp(prefix="kv-bootstrap-")
        os.chmod(self.tmp, 0o700)

    def az(self, *args):
        return subprocess.run(["az", "keyvault", *args, "--vault-name", self.name],
                              capture_output=True, text=True)

    def names(self):
        r = self.az("secret", "list", "--query", "[].name", "-o", "tsv")
        if r.returncode != 0:
            sys.exit(f"Could not list {self.name}'s entries (is your IP allowed through its firewall?):\n{r.stderr.strip()}")
        return set(r.stdout.split())

    def value(self, name):
        r = self.az("secret", "show", "-n", name, "--query", "value", "-o", "tsv")
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        return r.stdout.rstrip("\n")

    def put(self, name, value):
        path = os.path.join(self.tmp, "value")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(value)
        try:
            r = self.az("secret", "set", "-n", name, "--file", path, "--encoding", "utf-8",
                        "--tags", "set-by=scripts/keyvault-bootstrap.py", "-o", "none")
        finally:
            os.remove(path)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())

    def close(self):
        os.rmdir(self.tmp)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--vault", default="kv-iai-staging")
    p.add_argument("--apply", action="store_true", help="generate the missing internal entries")
    p.add_argument("--prompt", action="store_true", help="with --apply: ask for each missing entry only you can give")
    a = p.parse_args()
    if a.prompt and not a.apply:
        p.error("--prompt needs --apply")

    vault = Vault(a.vault)
    try:
        have = vault.names()
        made, given, missing, failed = [], [], [], []
        print(f"{a.vault}: {'creating what is missing' if a.apply else 'report only (nothing is changed; --apply to create)'}\n")
        for name, kind, how, required, note in ENTRIES:
            tag = "" if required else " (optional)"
            if name in have:
                print(f"  exists     {name}")
                continue
            if kind in ("generated", "same"):
                if not a.apply:
                    print(f"  would make {name}{tag}: {note}")
                    continue
                try:
                    if kind == "same":
                        # Generate the pair once: the source first if it is missing too, then copy it.
                        if how not in have:
                            vault.put(how, generate("token"))
                            have.add(how)
                            made.append(how)
                        vault.put(name, vault.value(how))
                    else:
                        vault.put(name, generate(how))
                    have.add(name)
                    made.append(name)
                    print(f"  made       {name}{tag}")
                except Exception as e:  # report and go on with the others
                    failed.append(name)
                    print(f"  FAILED     {name}: {e}")
                continue
            if kind == "yours" and a.prompt:
                hint = note + ("; " + PROVIDER_NOTE if name.startswith("ai-service-") and name.endswith("-key") and "credentials" not in name else "")
                print(f"  {name}: {hint}")
                # Hidden input keeps what a stray Escape or arrow key types (\x1b...): drop control
                # characters, which no secret here contains.
                value = "".join(c for c in getpass.getpass("    value (hidden; Enter to skip): ") if c.isprintable()).strip()
                if "mongodb" in name and value and not value.startswith(("mongodb://", "mongodb+srv://")):
                    print(f"  SKIPPED    {name}: a MongoDB connection string starts with mongodb:// or mongodb+srv://")
                    missing.append(name)
                    continue
                if value:
                    try:
                        vault.put(name, value)
                        have.add(name)
                        given.append(name)
                        print(f"  stored     {name}")
                    except Exception as e:
                        failed.append(name)
                        print(f"  FAILED     {name}: {e}")
                    continue
            label = {"yours": "YOURS", "existing": "EXISTING", "consul": "CONSUL"}[kind]
            why = {"yours": note,
                   "existing": note + ": copy it from the running deployment, never generate it",
                   "consul": f"{note}: scripts/bootstrap-acl.sh --tokens, then store it"}[kind]
            if required:
                missing.append(name)
            print(f"  {label:10} {name}{tag}: {why}")
    finally:
        vault.close()

    print(f"\nmade {len(made)}, stored {len(given)}, still missing {len(missing)} required"
          + (f", {len(failed)} failed" if failed else ""))
    return 1 if (missing or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
