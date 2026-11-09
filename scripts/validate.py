#!/usr/bin/env python3
"""Checks config/ before it is published to Consul. Exit code 1 on any problem.

1. Every file is valid YAML.
2. No secret values: a key that names a secret (password, secret, token, private key, API key,
   credential) may only hold an ${ENV_VAR} placeholder. Secrets come from the environment.
   A count of tokens (a key with the word "tokens" holding a number, e.g. ai-service's
   max-output-tokens or tokens-per-minute) is not a secret.
3. Each conf.d/<name>.yml declares the product <name> (gateway entries may add <name>-<suffix>).
4. Gateway routes: unique ids across products, an upstream on every route, and every permission
   or role a rule names exists in that product's catalog in pv-auth-service.
6. Each context fits in one Consul key (512 KB limit).
5. Catalog: permission keys use [a-z0-9_.-] (Spring Boot strips other characters from map keys),
   roles only grant declared permissions, and a self-signup role exists.
7. Pricing (pv.pricing, conf.d/pricing.yml): only in a product service's own context (never
   "application", which every service reads), one file per catalog product, and well-formed plans.
8. Gateway rate limits (rate-limit, rate-limits) and circuit breakers (pv.gateway.circuit-breaker,
   a route's circuit-breaker): known fields only (a misspelt one would be ignored), a known key
   (CLIENT_IP, USER, ORG, API_KEY, GUEST) with what it reads (headers, a path-variable every path
   has), buckets that can pass a request, limits sharing a bucket that agree, sane breaker numbers.
9. Resilience4j (resilience4j.*): known property names (the same in Spring Boot 3 and 4), durations
   with a unit, exception classes from the JDK or spring-web only (on every service's classpath),
   no inbound rate limiter; config/application has the defaults and the platform services'
   instances (ai-service, notification-service, pv-auth-service) for breakers, retries and time
   limiters; no gateway route is named like one of them (one breaker registry in the gateway).
"""
import os
import re
import sys

import yaml

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
SECRET_KEY = re.compile(r"(password|passwd|secret|token|private[-_]?key|api[-_]?key|credential)", re.I)
# Model token counts, not credentials: "tokens" as a whole word of the key, with a numeric value.
TOKEN_COUNT = re.compile(r"(^|[-_.])tokens($|[-_.])", re.I)
NOT_SECRET_SUFFIXES = ("-uri", "-url", "-minutes", "-days", "-seconds", "-prefix", "-path", "-ttl", "-enabled", "requested-tokens")
PERMISSION = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")
ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
# Declared by pv-auth-service for every product without its own manage-members-permission.
MANAGE_MEMBERS = "org.members.manage"

problems = []
PLAN_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
PER = ("organization", "employee")

RATE_KEYS = {"CLIENT_IP", "USER", "ORG", "API_KEY", "GUEST"}
RATE_FIELDS = {"bucket", "key", "replenish-rate", "burst-capacity", "requested-tokens", "paths", "exclude-paths",
               "methods", "headers", "path-variable"}
HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
BUCKET = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
BREAKER_FIELDS = {"enabled", "failure-rate-threshold", "slow-call-rate-threshold", "slow-call-duration",
                  "wait-in-open-state", "minimum-calls", "sliding-window", "half-open-calls", "failure-statuses", "retries"}
# Spring Boot durations: a number with a unit (30s, 500ms, 2m) or ISO-8601 (PT30S). A bare number means
# milliseconds, which is rarely what was meant: a unit is required.
DURATION = re.compile(r"^(\d+(ns|us|ms|s|m|h|d)|[pP](?=.*\d)[0-9dDtThHmMsS.]+)$")
# resilience4j-framework-common's property names, the same in 2.2 (Spring Cloud 2025.0, Boot 3) and 2.3+
# (Spring Cloud 2025.1, Boot 4; resilience4j-spring-boot4).
R4J_FIELDS = {
    "circuitbreaker": {
        "allow-health-indicator-to-fail", "automatic-transition-from-open-to-half-open-enabled", "base-config",
        "enable-exponential-backoff", "enable-randomized-wait", "event-consumer-buffer-size",
        "exponential-backoff-multiplier", "exponential-max-wait-duration-in-open-state", "failure-rate-threshold",
        "ignore-exception-predicate", "ignore-exceptions", "max-wait-duration-in-half-open-state",
        "minimum-number-of-calls", "permitted-number-of-calls-in-half-open-state", "randomized-wait-factor",
        "record-exceptions", "record-failure-predicate", "record-result-predicate", "register-health-indicator",
        "sliding-window-size", "sliding-window-type", "slow-call-duration-threshold", "slow-call-rate-threshold",
        "wait-duration-in-open-state", "writable-stack-trace-enabled"},
    "retry": {
        "base-config", "consume-result-before-retry-attempt", "enable-exponential-backoff", "enable-randomized-wait",
        "event-consumer-buffer-size", "exponential-backoff-multiplier", "exponential-max-wait-duration",
        "fail-after-max-attempts", "ignore-exceptions", "interval-bi-function", "max-attempts", "randomized-wait-factor",
        "result-predicate", "retry-exception-predicate", "retry-exceptions", "wait-duration"},
    "timelimiter": {"base-config", "cancel-running-future", "event-consumer-buffer-size", "timeout-duration"},
}
R4J_DURATIONS = {"slow-call-duration-threshold", "wait-duration-in-open-state", "max-wait-duration-in-half-open-state",
                 "exponential-max-wait-duration-in-open-state", "wait-duration", "exponential-max-wait-duration",
                 "timeout-duration"}
R4J_CLASSES = {"record-exceptions", "ignore-exceptions", "retry-exceptions"}
# Properties naming a class of the service's own: never in a context every service reads.
R4J_OWN_CLASS = {"ignore-exception-predicate", "record-failure-predicate", "record-result-predicate",
                 "result-predicate", "retry-exception-predicate", "interval-bi-function",
                 "consume-result-before-retry-attempt"}
SHARED_CLASS = re.compile(r"^(java\.[a-z.]+|org\.springframework\.web\.client)\.[A-Z][A-Za-z0-9]*(\$[A-Z][A-Za-z0-9]*)*$")
PLATFORM_TARGETS = ("ai-service", "notification-service", "pv-auth-service")


def problem(path, message):
    problems.append(f"{os.path.relpath(path, os.path.dirname(ROOT))}: {message}")


def walk_secrets(path, node, trail=""):
    if isinstance(node, dict):
        for key, value in node.items():
            name = f"{trail}.{key}" if trail else str(key)
            # A catalog's permission keys are names with descriptions, never settings: a permission
            # called api_key.manage is not a secret.
            if re.search(r"(^|\.)catalog\.[^.]+\.permissions$", trail):
                continue
            k = str(key).lower()
            token_count = (TOKEN_COUNT.search(k) and isinstance(value, (int, float)) and not isinstance(value, bool)
                           and not SECRET_KEY.search(TOKEN_COUNT.sub("-", k)))
            if (SECRET_KEY.search(k) and not k.endswith(NOT_SECRET_SUFFIXES) and isinstance(value, (str, int))
                    and not token_count):
                text = str(value).strip()
                if text and not re.fullmatch(r"\$\{[A-Z0-9_]+(:[^}]*)?\}", text):
                    problem(path, f"{name} looks like a secret; use an ${{ENV_VAR}} placeholder")
            walk_secrets(path, value, name)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            walk_secrets(path, item, f"{trail}[{i}]")


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            docs = [d for d in yaml.safe_load_all(f) if d is not None]
    except yaml.YAMLError as e:
        problem(path, f"invalid YAML: {e}")
        return []
    for d in docs:
        walk_secrets(path, d)
    return docs


def dig(doc, *keys):
    for k in keys:
        if not isinstance(doc, dict) or k not in doc:
            return None
        doc = doc[k]
    return doc


def check_pricing(path, context, pricing, seen):
    """One product's prices (see config/<service>/conf.d/pricing.yml for the schema)."""
    if context.split(",")[0] == "application" or context.split(",")[0] in ("pg-api-gateway", "pv-auth-service"):
        problem(path, "pricing belongs only in a product service's own context, never one other services read")
        return
    if os.path.basename(path) != "pricing.yml":
        problem(path, "pv.pricing goes in conf.d/pricing.yml")
    product = pricing.get("product")
    if not product:
        problem(path, "pv.pricing.product (the catalog key) is required")
    elif "," not in context:
        if product in seen:
            problem(path, f"pricing for '{product}' is also in {seen[product]}: one file per product")
        seen[product] = path
    if not CURRENCY.match(str(pricing.get("currency") or "")):
        problem(path, "currency must be an ISO 4217 code, e.g. INR")
    tax = pricing.get("tax-percent")
    if tax is not None and not (isinstance(tax, (int, float)) and 0 <= tax <= 100):
        problem(path, "tax-percent must be between 0 and 100 (or null)")
    if pricing.get("per") not in PER:
        problem(path, f"per must be one of {', '.join(PER)}")
    plans = pricing.get("plans")
    if not isinstance(plans, dict):
        problem(path, "plans must be a map of plan code to plan ({} when there are none yet)")
        return
    recommended = 0
    for code, plan in plans.items():
        where = f"plan {code}"
        if not PLAN_CODE.match(str(code)):
            problem(path, f"{where}: code must match {PLAN_CODE.pattern}")
        if not isinstance(plan, dict) or not str(plan.get("name") or "").strip():
            problem(path, f"{where}: name is required")
            continue
        for price in ("monthly", "yearly"):
            v = plan.get(price)
            if v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0):
                problem(path, f"{where}: {price} must be a number >= 0, or null (quoted on request)")
        if "monthly" not in plan:
            problem(path, f"{where}: monthly is required (null = quoted on request)")
        days = plan.get("trial-days", 0)
        if not (isinstance(days, int) and not isinstance(days, bool) and 0 <= days <= 365):
            problem(path, f"{where}: trial-days must be a whole number of days, 0-365")
        if not isinstance(plan.get("order", 0), int):
            problem(path, f"{where}: order must be a whole number")
        if plan.get("recommended"):
            recommended += 1
        for k, v in (plan.get("limits") or {}).items():
            if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and v >= 0):
                problem(path, f"{where}: limit {k} must be a whole number >= 0, or null (unlimited)")
        features = plan.get("features")
        if features is not None and not (
                (isinstance(features, list) and all(isinstance(f, str) for f in features))
                or (isinstance(features, dict) and all(isinstance(v, bool) for v in features.values()))):
            problem(path, f"{where}: features must be a list of text or a map of flags")
    if recommended > 1:
        problem(path, "at most one plan can be recommended")


def whole(value, minimum):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def check_rate_limit(path, rid, access, limit, buckets):
    """One rate-limit block of route rid (GatewayProperties.RateLimit)."""
    where = f"route {rid}: rate limit"
    if not isinstance(limit, dict):
        problem(path, f"{where} must be a map")
        return
    for field in sorted(set(limit) - RATE_FIELDS):
        problem(path, f"{where}: unknown field {field} (known: {', '.join(sorted(RATE_FIELDS))})")
    key = limit.get("key", "CLIENT_IP")
    if key not in RATE_KEYS:
        problem(path, f"{where}: key {key} isn't one of {', '.join(sorted(RATE_KEYS))}")
    rate, burst, requested = limit.get("replenish-rate"), limit.get("burst-capacity"), limit.get("requested-tokens", 1)
    if not whole(rate, 1) or not whole(burst, 1):
        problem(path, f"{where}: replenish-rate and burst-capacity are whole numbers, at least 1")
    elif not whole(requested, 1):
        problem(path, f"{where}: requested-tokens is a whole number, at least 1")
    elif requested > burst:
        problem(path, f"{where}: requested-tokens {requested} is more than burst-capacity {burst}: nothing would pass")
    for field in ("paths", "exclude-paths", "methods", "headers"):
        value = limit.get(field)
        if value is not None and not (isinstance(value, list) and all(isinstance(v, str) and v for v in value)):
            problem(path, f"{where}: {field} must be a list of text")
    for p in (limit.get("paths") or []) + (limit.get("exclude-paths") or []):
        if isinstance(p, str) and not p.startswith("/"):
            problem(path, f"{where}: path {p} must start with /")
    for m in limit.get("methods") or []:
        if isinstance(m, str) and m.upper() not in HTTP_METHODS:
            problem(path, f"{where}: method {m} isn't one of {', '.join(sorted(HTTP_METHODS))}")
    headers, variable = limit.get("headers"), limit.get("path-variable")
    if headers and key not in ("API_KEY", "GUEST"):
        problem(path, f"{where}: headers are only for the API_KEY and GUEST keys")
    if variable is not None and key != "GUEST":
        problem(path, f"{where}: path-variable is only for the GUEST key")
    if key == "GUEST":
        if not variable and not headers:
            problem(path, f"{where}: a GUEST key reads a path-variable or headers")
        if variable:
            paths = limit.get("paths") or []
            missing = [p for p in paths if isinstance(p, str) and "{%s}" % variable not in p and "{%s:" % variable not in p]
            if not paths or missing:
                problem(path, f"{where}: every path of a GUEST limit must have {{{variable}}} ({', '.join(missing) or 'no paths'})")
    if key in ("USER", "ORG") and access == "PUBLIC":
        problem(path, f"{where}: a PUBLIC route has no signed-in person, so {key} is always the client address; use CLIENT_IP")
    bucket = limit.get("bucket")
    if bucket is not None:
        if not isinstance(bucket, str) or not BUCKET.match(bucket):
            problem(path, f"{where}: bucket {bucket} must match {BUCKET.pattern}")
            return
        default_headers = ["Authorization"] if key == "API_KEY" else []
        signature = (key, rate, burst, requested, tuple(headers or default_headers), variable)
        first = buckets.setdefault(bucket, (signature, f"{os.path.basename(path)} route {rid}"))
        if first[0] != signature:
            problem(path, f"{where}: bucket {bucket} is also used by {first[1]} with another key or numbers")


def check_breaker(path, where, breaker):
    """A circuit-breaker block (GatewayProperties.CircuitBreaker): the defaults or a route's own."""
    if not isinstance(breaker, dict):
        problem(path, f"{where} must be a map")
        return
    for field in sorted(set(breaker) - BREAKER_FIELDS):
        problem(path, f"{where}: unknown field {field} (known: {', '.join(sorted(BREAKER_FIELDS))})")
    if "enabled" in breaker and not isinstance(breaker["enabled"], bool):
        problem(path, f"{where}: enabled is true or false")
    for field in ("failure-rate-threshold", "slow-call-rate-threshold"):
        v = breaker.get(field)
        if v is not None and not (whole(v, 1) and v <= 100):
            problem(path, f"{where}: {field} is a percentage, 1-100")
    for field in ("minimum-calls", "sliding-window", "half-open-calls"):
        v = breaker.get(field)
        if v is not None and not whole(v, 1):
            problem(path, f"{where}: {field} is a whole number, at least 1")
    for field in ("slow-call-duration", "wait-in-open-state"):
        v = breaker.get(field)
        if v is not None and not DURATION.match(str(v)):
            problem(path, f"{where}: {field} {v} needs a unit (e.g. 30s)")
    statuses = breaker.get("failure-statuses")
    if statuses is not None and not (isinstance(statuses, list) and all(whole(c, 500) and c <= 599 for c in statuses)):
        problem(path, f"{where}: failure-statuses are 5xx status codes")
    retries = breaker.get("retries")
    if retries is not None and not (whole(retries, 0) and retries <= 3):
        problem(path, f"{where}: retries is 0-3 (GET, HEAD and OPTIONS only)")


def check_resilience4j(path, context, r4j, route_ids):
    """resilience4j.* (outbound breakers, retries, time limiters) in any context."""
    shared = context.split(",")[0] == "application"
    if not isinstance(r4j, dict):
        problem(path, "resilience4j must be a map")
        return
    for section in r4j:
        if section == "ratelimiter":
            problem(path, "resilience4j.ratelimiter: inbound rate limits are pg-api-gateway's; quotas stay in code")
        elif section not in R4J_FIELDS and section != "bulkhead":
            problem(path, f"resilience4j.{section}: not a section the platform uses ({', '.join(sorted(R4J_FIELDS))})")
    for section, fields in R4J_FIELDS.items():
        block = r4j.get(section)
        if block is None:
            if shared:
                problem(path, f"resilience4j.{section}: the shared defaults are required here")
            continue
        configs, instances = (block or {}).get("configs") or {}, (block or {}).get("instances") or {}
        for extra in sorted(set(block or {}) - {"configs", "instances"}):
            problem(path, f"resilience4j.{section}.{extra}: settings go under configs.<name> or instances.<name>")
        if shared and "default" not in configs:
            problem(path, f"resilience4j.{section}.configs.default is required (every instance inherits it)")
        for target in PLATFORM_TARGETS if shared else ():
            if target not in instances:
                problem(path, f"resilience4j.{section}.instances.{target} is required (a platform service)")
        for kind, entries in (("configs", configs), ("instances", instances)):
            for name, entry in entries.items():
                where = f"resilience4j.{section}.{kind}.{name}"
                if not isinstance(entry, dict):
                    problem(path, f"{where} must be a map")
                    continue
                for field in sorted(set(entry) - fields):
                    problem(path, f"{where}: unknown property {field}")
                base = entry.get("base-config")
                if base is not None and base not in configs:
                    problem(path, f"{where}: base-config {base} isn't one of configs ({', '.join(configs) or 'none'})")
                for field in R4J_DURATIONS & set(entry):
                    if not DURATION.match(str(entry[field])):
                        problem(path, f"{where}: {field} {entry[field]} needs a unit (e.g. 10s)")
                for field in R4J_CLASSES & set(entry):
                    classes = entry[field]
                    if not isinstance(classes, list):
                        problem(path, f"{where}: {field} is a list of class names")
                        continue
                    for c in classes:
                        if not SHARED_CLASS.match(str(c)):
                            problem(path, f"{where}: {field} {c}: only JDK (java.*) and spring-web "
                                          "(org.springframework.web.client.*) classes are on every service's classpath")
                if shared:
                    for field in sorted(R4J_OWN_CLASS & set(entry)):
                        problem(path, f"{where}: {field} names a class of one service; set it in that service")
                for field in ("failure-rate-threshold", "slow-call-rate-threshold"):
                    v = entry.get(field)
                    if v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 100):
                        problem(path, f"{where}: {field} is a percentage, 1-100")
                for field in ("max-attempts", "sliding-window-size", "minimum-number-of-calls",
                              "permitted-number-of-calls-in-half-open-state"):
                    v = entry.get(field)
                    if v is not None and not whole(v, 1):
                        problem(path, f"{where}: {field} is a whole number, at least 1")
                factor = entry.get("randomized-wait-factor")
                if factor is not None and not (isinstance(factor, (int, float)) and 0 < factor < 1):
                    problem(path, f"{where}: randomized-wait-factor is between 0 and 1")
                multiplier = entry.get("exponential-backoff-multiplier")
                if multiplier is not None and not (isinstance(multiplier, (int, float)) and multiplier >= 1):
                    problem(path, f"{where}: exponential-backoff-multiplier is at least 1")
                window = entry.get("sliding-window-type")
                if window is not None and window not in ("COUNT_BASED", "TIME_BASED"):
                    problem(path, f"{where}: sliding-window-type is COUNT_BASED or TIME_BASED")
                if section == "circuitbreaker" and kind == "instances" and name in route_ids:
                    problem(path, f"{where}: a gateway route has this id; the gateway's route breakers share its registry")


def main():
    gateway, catalog, pricing_files = {}, {}, {}
    resilience, breaker_defaults = [], []
    for context in sorted(os.listdir(ROOT)):
        base = os.path.join(ROOT, context)
        if not os.path.isdir(base):
            continue
        files = [os.path.join(base, "data.yml")] if os.path.exists(os.path.join(base, "data.yml")) else []
        confd = os.path.join(base, "conf.d")
        if os.path.isdir(confd):
            files += [os.path.join(confd, f) for f in sorted(os.listdir(confd)) if f.endswith((".yml", ".yaml"))]
        size = sum(os.path.getsize(f) for f in files)
        if size > 450 * 1024:
            problem(base, f"{size // 1024} KB joined: Consul's limit per key is 512 KB; split the context or trim comments")
        for path in files:
            docs = load(path)
            for doc in docs:
                if isinstance(doc, dict) and "resilience4j" in doc:
                    resilience.append((path, context, doc["resilience4j"]))
                defaults = dig(doc, "pv", "gateway", "circuit-breaker")
                if defaults is not None:
                    breaker_defaults.append((path, defaults))
            if not path.startswith(confd):
                continue
            name = os.path.splitext(os.path.basename(path))[0]
            for doc in docs:
                products = dig(doc, "pv", "gateway", "products")
                entries = dig(doc, "auth", "catalog")
                pricing = dig(doc, "pv", "pricing")
                if pricing is not None:
                    if not isinstance(pricing, dict):
                        problem(path, "pv.pricing must be a map")
                    else:
                        pricing_files[path] = (context, pricing)
                    continue
                if products is not None and isinstance(products, dict):
                    # A product's file may add entries '<name>-<suffix>' for hosts that need other
                    # routes (an API-key host, a public site). Tokens name the catalog product, so
                    # only '<name>' itself may have token-protected routes (checked below).
                    stray = [k for k in products if k != name and not str(k).startswith(name + "-")]
                    if name not in products or stray:
                        problem(path, f"must declare the product '{name}' (and only '{name}-*' entries besides it)")
                        continue
                    for key, entry in products.items():
                        gateway[key] = (path, entry)
                elif entries is not None and isinstance(entries, dict):
                    if list(entries) != [name]:
                        problem(path, f"must declare exactly the catalog '{name}'")
                        continue
                    catalog[name] = (path, entries[name])
                else:
                    problem(path, "declares neither pv.gateway.products nor auth.catalog")

    for name, (path, entry) in catalog.items():
        permissions = set((entry or {}).get("permissions") or {})
        own_manage = (entry or {}).get("manage-members-permission")
        if own_manage is not None and own_manage not in permissions:
            problem(path, f"manage-members-permission {own_manage} isn't one of the permissions")
        if own_manage is None:
            permissions.add(MANAGE_MEMBERS)  # pv-auth-service declares it for the product
        for p in permissions:
            if not PERMISSION.match(str(p)):
                problem(path, f"permission key '{p}' must match {PERMISSION.pattern}")
        roles = (entry or {}).get("roles") or {}
        for role, definition in roles.items():
            if not ROLE.match(str(role)):
                problem(path, f"role key '{role}' must match {ROLE.pattern}")
            for p in (definition or {}).get("permissions") or []:
                if p != "*" and p not in permissions:
                    problem(path, f"role {role} grants undeclared permission {p}")
        if (entry or {}).get("self-signup") and (entry or {}).get("signup-role") not in roles:
            problem(path, "self-signup needs a signup-role that is one of the roles")

    seen = {}
    for path, (context, pricing) in sorted(pricing_files.items()):
        check_pricing(path, context, pricing, seen)
        if pricing.get("product") and pricing.get("product") not in catalog:
            problem(path, f"pricing names product '{pricing.get('product')}', which has no catalog in pv-auth-service")

    route_ids, buckets = {}, {}
    for path, defaults in breaker_defaults:
        check_breaker(path, "pv.gateway.circuit-breaker", defaults)
    for name, (path, product) in gateway.items():
        declared = catalog.get(name, (None, {}))[1] or {}
        permissions = set(declared.get("permissions") or {})
        if declared and declared.get("manage-members-permission") is None:
            permissions.add(MANAGE_MEMBERS)
        roles = set(declared.get("roles") or {})
        if not (product or {}).get("hosts"):
            problem(path, "hosts are required")
        for route in (product or {}).get("routes") or []:
            rid = route.get("id")
            if rid in route_ids:
                problem(path, f"route id {rid} is also used in {route_ids[rid]}")
            route_ids[rid] = os.path.basename(path)
            if not route.get("upstream"):
                problem(path, f"route {rid}: upstream is required")
            protected = route.get("access", "PROTECTED") == "PROTECTED"
            needs_tokens = route.get("default-rule") == "AUTHENTICATED" or any(
                not r.get("anonymous") for r in route.get("rules") or [])
            if protected and needs_tokens and name not in catalog:
                problem(path, f"route {rid} is PROTECTED but pv-auth-service has no catalog for '{name}'")
            access = route.get("access", "PROTECTED")
            limits = route.get("rate-limits")
            if limits is not None and not isinstance(limits, list):
                problem(path, f"route {rid}: rate-limits is a list (rate-limit is one)")
                limits = []
            for limit in ([route["rate-limit"]] if route.get("rate-limit") is not None else []) + (limits or []):
                check_rate_limit(path, rid, access, limit, buckets)
            if route.get("circuit-breaker") is not None:
                check_breaker(path, f"route {rid}: circuit-breaker", route["circuit-breaker"])
            for rule in route.get("rules") or []:
                for p in rule.get("permissions") or []:
                    if p not in permissions:
                        problem(path, f"route {rid}: rule names permission {p}, not in the {name} catalog")
                for r in rule.get("roles") or []:
                    if r not in roles:
                        problem(path, f"route {rid}: rule names role {r}, not in the {name} catalog")

    for path, context, r4j in resilience:
        check_resilience4j(path, context, r4j, route_ids)
    if not any(context == "application" for _, context, _ in resilience):
        problem(os.path.join(ROOT, "application", "data.yml"), "resilience4j: the shared outbound defaults are missing")

    if problems:
        print("\n".join(problems), file=sys.stderr)
        print(f"{len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"config/ is valid: {len(gateway)} gateway products, {len(catalog)} catalogs, {len(pricing_files)} price lists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
