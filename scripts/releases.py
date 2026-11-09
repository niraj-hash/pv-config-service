#!/usr/bin/env python3
"""Release files: what Argo CD deploys for each Runvance service (docs/deploy-with-argocd.md).

    python3 scripts/releases.py check
    scripts/release.sh <service> <env> [options]        (= python3 scripts/releases.py release ...)

check   Every release file deploy/releases/<env>/<service>.yaml against the service registry
        (deploy/services.yaml) and the ApplicationSets (deploy/argocd/applicationset-<env>.yaml):
        one file per service and environment, the keys Argo CD and the chart read, quoted tags and
        revisions (an unquoted 1048e29 is a number to Helm), prod pinned to a commit SHA, no automated
        sync anywhere, the AppProject admitting every repository and namespace, and each local
        checkout's origin matching the ApplicationSet's repoURL.

release Writes one release file: the image tags and the chart commit to deploy. Nothing is deployed:
        the Application turns OutOfSync once the file is on main, and someone presses Sync.
          --build                az acr build each image of the service from its repository's HEAD
                                 (tag: the short SHA); refuses uncommitted changes unless --force
          --tag TAG              every image of the service at TAG (built earlier)
          --tag IMAGE=TAG        one image (repeat for each)
          --from ENV             the tags and chart commit another environment's release file names
                                 (promotion, e.g. staging to prod); --import copies the images into
                                 this environment's registry with az acr import
          --chart-revision REF   the chart's commit (default: the commit of the tag, or HEAD); prod
                                 needs a commit, staging may name a branch to track (e.g. main)
          --repo-dir DIR         the repository checkout (default: ../<repo> beside this repository)
          --skip-image-check     do not ask ACR whether the tags exist
          --dry-run              print the release file instead of writing it
"""
import argparse
import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
WORKSPACE = os.path.dirname(ROOT)
SERVICES = os.path.join(ROOT, "deploy", "services.yaml")
ARGOCD = os.path.join(ROOT, "deploy", "argocd")
RELEASES = os.path.join(ROOT, "deploy", "releases")
UNRELEASED = "unreleased"
SHA = re.compile(r"^[0-9a-f]{40}$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
VALUE_PATH = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z][A-Za-z0-9_-]*)*$")
# Keys of a release file that Argo CD reads; it is also the chart's last value file, so no chart may use them.
ARGOCD_KEYS = ("releaseOf", "chartRevision")
ELEMENT_KEYS = {"releaseOf", "repoURL", "chartPath", "releaseName", "namespace", "createNamespace", "valueFiles"}


class Problem(Exception):
    pass


# --- reading -------------------------------------------------------------------------------------

def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_services():
    doc = load_yaml(SERVICES)
    return doc["registries"], doc["services"]


def load_appset(env):
    path = os.path.join(ARGOCD, f"applicationset-{env}.yaml")
    doc = load_yaml(path)
    merge = doc["spec"]["generators"][0]["merge"]
    git = next(g["git"] for g in merge["generators"] if "git" in g)
    elements = next(g["list"]["elements"] for g in merge["generators"] if "list" in g)
    return path, doc, git, {e["releaseOf"]: e for e in elements}


def release_path(env, service):
    return os.path.join(RELEASES, env, f"{service}.yaml")


def get_path(tree, dotted):
    node = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_path(tree, dotted, value):
    parts = dotted.split(".")
    node = tree
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def image_tags(service_def, release):
    """{image name: tag} as a release file states them ("" when not set)."""
    tags = {}
    for image in service_def["images"]:
        values = {get_path(release, p) for p in image["values"]}
        tags[image["name"]] = values.pop() if len(values) == 1 else None
    return tags


def normalize_repo_url(url):
    url = url.strip()
    m = re.match(r"^(?:git@|ssh://git@)github\.com[:/](.+?)(?:\.git)?/?$", url) or \
        re.match(r"^https://github\.com/(.+?)(?:\.git)?/?$", url)
    return m.group(1).lower() if m else url.lower()


def git(repo_dir, *args, check=True):
    result = subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise Problem(f"git {' '.join(args)} in {repo_dir}: {result.stderr.strip()}")
    return result.stdout.strip() if result.returncode == 0 else None


def repo_dir_for(service_def, override=None):
    if override:
        return os.path.abspath(override)
    if service_def["repo"] == os.path.basename(ROOT):
        return ROOT
    return os.path.join(WORKSPACE, service_def["repo"])


# --- writing -------------------------------------------------------------------------------------

def yaml_tree(tree, indent=0):
    lines = []
    for key, value in tree.items():
        pad = "  " * indent
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.extend(yaml_tree(value, indent + 1))
        else:
            lines.append(f"{pad}{key}: {json.dumps(str(value))}")
    return lines


def render_release(service, env, service_def, element, registry, chart_revision, tags):
    values = {}
    for image in service_def["images"]:
        for path in image["values"]:
            set_path(values, path, tags.get(image["name"], ""))
    width = max(len(i["name"]) for i in service_def["images"])
    images = [f"#   {i['name']:<{width}}  {registry}.azurecr.io/{i['name']}:{tags.get(i['name']) or '(none)'}"
              for i in service_def["images"]]
    state = ("Not released yet: no Application until a release names the chart's commit"
             if chart_revision == UNRELEASED
             else f"The commit (or, staging only, branch) of {element['repoURL']} whose chart Argo CD renders")
    lines = [
        f"# {service} in {env}: what Argo CD deploys when someone presses Sync on {service}-{env}.",
        "# Written by scripts/release.sh: review the diff, push it to main, then Sync. Roll back with",
        "# History -> Rollback in Argo CD, or revert this file's commit and Sync (docs/deploy-with-argocd.md).",
        "#",
        *images,
        f"releaseOf: {service}",
        f"# {state}.",
        f"chartRevision: {json.dumps(chart_revision)}",
        "# Chart values (this file is the chart's last value file): the image tags.",
        *yaml_tree(values),
    ]
    return "\n".join(lines) + "\n"


# --- check ---------------------------------------------------------------------------------------

def check():
    problems = []
    registries, services = load_services()
    project = load_yaml(os.path.join(ARGOCD, "appproject.yaml"))["spec"]
    source_repos = {normalize_repo_url(r) for r in project["sourceRepos"]}
    destinations = {(d.get("server") or d.get("name"), d["namespace"]) for d in project["destinations"]}

    for name, sdef in services.items():
        if not isinstance(sdef.get("repo"), str) or not sdef.get("images"):
            problems.append(f"deploy/services.yaml: {name} needs repo and images")
            continue
        for image in sdef["images"]:
            for key in ("name", "dockerfile", "context", "values"):
                if key not in image:
                    problems.append(f"deploy/services.yaml: {name} image {image.get('name')} has no {key}")
            for path in image.get("values", []):
                if not VALUE_PATH.match(path):
                    problems.append(f"deploy/services.yaml: {name} value path {path!r} is not a dotted key")

    for env, registry in registries.items():
        try:
            path, appset, gitgen, elements = load_appset(env)
        except (OSError, KeyError, StopIteration, TypeError) as e:
            problems.append(f"deploy/argocd/applicationset-{env}.yaml: unreadable ({e})")
            continue
        rel = os.path.relpath(path, ROOT)
        if "automated" in json.dumps(appset):
            problems.append(f"{rel}: has an automated sync policy; every Application syncs by hand")
        if gitgen.get("files", [{}])[0].get("path") != f"deploy/releases/{env}/*.yaml":
            problems.append(f"{rel}: the git generator must read deploy/releases/{env}/*.yaml")
        destination = appset["spec"]["template"]["spec"]["destination"]
        cluster = destination.get("server") or destination.get("name")
        for name in sorted(set(services) | set(elements)):
            if name not in elements:
                problems.append(f"{rel}: no list element for {name} (deploy/services.yaml has it)")
                continue
            if name not in services:
                problems.append(f"{rel}: {name} is not in deploy/services.yaml")
                continue
            element = elements[name]
            missing = ELEMENT_KEYS - set(element)
            if missing:
                problems.append(f"{rel}: {name} lacks {', '.join(sorted(missing))}")
                continue
            if normalize_repo_url(element["repoURL"]) not in source_repos:
                problems.append(f"{rel}: {name}'s repository is not in the AppProject's sourceRepos")
            if (cluster, element["namespace"]) not in destinations:
                problems.append(f"{rel}: {name}'s namespace {element['namespace']} on {cluster} is not an AppProject destination")
            local = repo_dir_for(services[name])
            if os.path.isdir(os.path.join(local, ".git")) or os.path.isfile(os.path.join(local, ".git")):
                origin = git(local, "remote", "get-url", "origin", check=False)
                if origin and normalize_repo_url(origin) != normalize_repo_url(element["repoURL"]):
                    problems.append(f"{rel}: {name} repoURL {element['repoURL']} but {local} is {origin}")
            clash = chart_value_keys(os.path.join(local, element["chartPath"])) & set(ARGOCD_KEYS)
            if clash:
                problems.append(f"{rel}: {name}'s chart uses the value(s) {', '.join(sorted(clash))}, which the release file sets for Argo CD")
            problems.extend(check_release(env, name, services[name]))

        known = {f"{s}.yaml" for s in services}
        env_dir = os.path.join(RELEASES, env)
        for f in sorted(os.listdir(env_dir)) if os.path.isdir(env_dir) else []:
            if f not in known:
                problems.append(f"deploy/releases/{env}/{f}: no such service in deploy/services.yaml")

    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        print(f"{len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"release files ok: {len(services)} services x {len(registries)} environments")
    return 0


def chart_value_keys(chart):
    """Top-level value keys a local chart reads (its values.yaml, and the library's defaults)."""
    keys = set()
    values = os.path.join(chart, "values.yaml")
    if os.path.isfile(values):
        keys |= set((load_yaml(values) or {}).keys())
        chart_yaml = load_yaml(os.path.join(chart, "Chart.yaml")) or {}
        if any(d.get("name") == "runvance-service" for d in chart_yaml.get("dependencies") or []):
            with open(os.path.join(ROOT, "deploy/charts/runvance-service/templates/_defaults.tpl"), encoding="utf-8") as f:
                keys |= set(re.findall(r"^([A-Za-z][A-Za-z0-9_]*):", f.read(), re.M))
    return keys


def check_release(env, name, sdef):
    path = release_path(env, name)
    rel = os.path.relpath(path, ROOT)
    if not os.path.isfile(path):
        return [f"{rel}: missing (write it with scripts/release.sh; `chartRevision: unreleased` for none yet)"]
    try:
        doc = load_yaml(path)
    except yaml.YAMLError as e:
        return [f"{rel}: not YAML ({e})"]
    problems = []
    if not isinstance(doc, dict):
        return [f"{rel}: not a map"]
    if doc.get("releaseOf") != name:
        problems.append(f"{rel}: releaseOf must be {name}")
    allowed = set(ARGOCD_KEYS) | {p.split(".")[0] for i in sdef["images"] for p in i["values"]}
    for key in doc:
        if key not in allowed:
            problems.append(f"{rel}: unexpected key {key} (a release file holds the chart commit and image tags only)")
    revision = doc.get("chartRevision")
    released = revision != UNRELEASED
    if not isinstance(revision, str) or not REF.match(revision):
        problems.append(f"{rel}: chartRevision must be a commit SHA, a branch or {UNRELEASED!r}")
    elif released and env != "staging" and not SHA.match(revision):
        problems.append(f"{rel}: chartRevision must be a full commit SHA in {env} (not {revision!r})")
    for image in sdef["images"]:
        for vpath in image["values"]:
            tag = get_path(doc, vpath)
            if not isinstance(tag, str):
                problems.append(f"{rel}: {vpath} must be a quoted string")
            elif released and not tag:
                problems.append(f"{rel}: {vpath} is empty in a release")
            elif tag and (not TAG.match(tag) or tag == "latest"):
                problems.append(f"{rel}: {vpath} {tag!r} is not an immutable image tag")
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            m = re.match(r"^\s*(chartRevision|tag):\s*([^#\s][^#]*?)\s*(#.*)?$", line)
            if m and not m.group(2).startswith('"'):
                problems.append(f"{rel}:{n}: quote {m.group(1)} (Helm reads an unquoted 1048e29 as a number)")
    return problems


# --- release -------------------------------------------------------------------------------------

def run(cmd, cwd, dry_run=False):
    print("+ " + " ".join(shlex.quote(c) for c in cmd) + (f"   (in {cwd})" if cwd else ""))
    if dry_run:
        return
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        raise Problem(f"{cmd[0]} failed ({result.returncode})")


def resolve_commit(repo_dir, ref):
    if not os.path.isdir(repo_dir):
        return None
    return git(repo_dir, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)


def is_branch(repo_dir, ref):
    if not os.path.isdir(repo_dir):
        return False
    for candidate in (f"refs/heads/{ref}", f"refs/remotes/origin/{ref}"):
        if git(repo_dir, "rev-parse", "--verify", "--quiet", candidate, check=False):
            return True
    return False


def release(argv):
    parser = argparse.ArgumentParser(prog="scripts/release.sh", description="Write one release file.")
    parser.add_argument("service")
    parser.add_argument("env")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--from", dest="source_env")
    parser.add_argument("--import", dest="do_import", action="store_true")
    parser.add_argument("--chart-revision")
    parser.add_argument("--repo-dir")
    parser.add_argument("--skip-image-check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    registries, services = load_services()
    if args.service not in services:
        raise Problem(f"unknown service {args.service} (deploy/services.yaml: {', '.join(services)})")
    if args.env not in registries:
        raise Problem(f"unknown environment {args.env} ({', '.join(registries)})")
    if sum(map(bool, (args.build, args.tag, args.source_env))) > 1:
        raise Problem("--build, --tag and --from exclude each other")
    if args.do_import and not args.source_env:
        raise Problem("--import goes with --from")
    sdef = services[args.service]
    _, _, _, elements = load_appset(args.env)
    element = elements[args.service]
    registry = registries[args.env]
    repo_dir = repo_dir_for(sdef, args.repo_dir)
    path = release_path(args.env, args.service)
    current = load_yaml(path) if os.path.isfile(path) else {}
    tags = {k: v for k, v in image_tags(sdef, current).items() if v}
    names = [i["name"] for i in sdef["images"]]
    revision = None

    if args.build:
        if not os.path.isdir(repo_dir):
            raise Problem(f"{repo_dir} is not checked out (--repo-dir)")
        if not shutil.which("az") and not args.dry_run:
            raise Problem("az (Azure CLI) is needed for --build")
        dirty = git(repo_dir, "status", "--porcelain")
        tag = git(repo_dir, "rev-parse", "--short=7", "HEAD")
        if dirty:
            if not args.force:
                raise Problem(f"{repo_dir} has uncommitted changes; commit them, or --force (the tag then says -dirty)")
            tag += "-dirty-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M")
        revision = git(repo_dir, "rev-parse", "HEAD")
        prebuilt = set()
        for image in sdef["images"]:
            if image.get("prebuild") and image["prebuild"] not in prebuilt:
                run(["bash", "-c", image["prebuild"]], repo_dir, args.dry_run)
                prebuilt.add(image["prebuild"])
            cmd = ["az", "acr", "build", "--registry", registry, "--image", f"{image['name']}:{tag}",
                   "--file", image["dockerfile"]]
            if image.get("target"):
                cmd += ["--target", image["target"]]
            for key, value in (image.get("buildArgs") or {}).items():
                cmd += ["--build-arg", f"{key}={value}"]
            run(cmd + [image["context"]], repo_dir, args.dry_run)
            tags[image["name"]] = tag
        args.skip_image_check = True
    elif args.tag:
        for t in args.tag:
            image, _, value = t.rpartition("=")
            if image and image not in names:
                raise Problem(f"{args.service} has no image {image} ({', '.join(names)})")
            for n in ([image] if image else names):
                tags[n] = value
    elif args.source_env:
        source = release_path(args.source_env, args.service)
        if not os.path.isfile(source):
            raise Problem(f"no release file {os.path.relpath(source, ROOT)}")
        src = load_yaml(source)
        if src.get("chartRevision") == UNRELEASED:
            raise Problem(f"{args.service} is not released in {args.source_env}")
        tags = {k: v for k, v in image_tags(sdef, src).items() if v}
        revision = src.get("chartRevision")
        src_registry = registries[args.source_env]
        if src_registry != registry:
            for n in names:
                cmd = ["az", "acr", "import", "--name", registry, "--source",
                       f"{src_registry}.azurecr.io/{n}:{tags[n]}", "--image", f"{n}:{tags[n]}"]
                if args.do_import:
                    run(cmd, None, args.dry_run)
                else:
                    print("the image must be in this environment's registry: " + " ".join(cmd))
            args.skip_image_check = args.skip_image_check or args.do_import

    missing = [n for n in names if not tags.get(n)]
    if missing:
        raise Problem(f"no tag for {', '.join(missing)} (--build, --tag or --from)")
    for n, t in tags.items():
        if not TAG.match(t) or t == "latest":
            raise Problem(f"{n}:{t} is not an immutable tag (the short git SHA)")

    # The chart's commit.
    if args.chart_revision:
        ref = args.chart_revision
        if args.env == "staging" and is_branch(repo_dir, ref) and not SHA.match(ref):
            revision = ref  # staging tracks a branch: every Sync renders its latest chart
        else:
            revision = resolve_commit(repo_dir, ref) or ref
    elif revision is None:
        distinct = set(tags.values())
        commit = resolve_commit(repo_dir, next(iter(distinct))) if len(distinct) == 1 else None
        if commit:
            revision = commit  # the commit the images were built from
        elif os.path.isdir(repo_dir):
            revision = git(repo_dir, "rev-parse", "HEAD")
            print(f"chart: HEAD of {repo_dir} (the tags are not one commit there; --chart-revision to choose)")
        else:
            revision = current.get("chartRevision")
    if not revision or revision == UNRELEASED:
        raise Problem("no chart commit: --chart-revision <sha>")
    if args.env != "staging" and not SHA.match(revision):
        raise Problem(f"{args.env} pins the chart to a commit; {revision!r} is not one "
                      f"(the commit staging runs: argocd app get {args.service}-staging, then --chart-revision <sha>)")
    if os.path.isdir(repo_dir):
        tree = revision if SHA.match(revision) else f"origin/{revision}"
        if subprocess.run(["git", "-C", repo_dir, "cat-file", "-e", f"{tree}:{element['chartPath']}/Chart.yaml"],
                          capture_output=True).returncode != 0:
            message = f"{element['chartPath']} is not in {tree[:12]} of {repo_dir} (commit the chart, or --chart-revision)"
            if SHA.match(revision) and not args.dry_run:
                raise Problem(message)
            print(f"WARNING: {message}")
    if SHA.match(revision) and os.path.isdir(repo_dir):
        subject = git(repo_dir, "log", "-1", "--format=%h %s (%cr)", revision, check=False)
        print(f"chart: {subject or revision + ' (not in the local checkout)'}")
        if not git(repo_dir, "branch", "-r", "--contains", revision, check=False):
            print(f"WARNING: {revision[:12]} is on no remote branch of {repo_dir}: push it, Argo CD cannot fetch it")

    if not args.skip_image_check:
        if shutil.which("az"):
            for n, t in tags.items():
                result = subprocess.run(["az", "acr", "repository", "show", "--name", registry, "--image", f"{n}:{t}",
                                         "--output", "none"], capture_output=True, text=True)
                if result.returncode != 0:
                    raise Problem(f"{registry}.azurecr.io/{n}:{t} not found or not readable "
                                  f"({result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'az failed'}); "
                                  "--build it, or --skip-image-check")
        else:
            print("WARNING: az not installed: not checking that the tags exist in ACR")

    text = render_release(args.service, args.env, sdef, element, registry, revision, tags)
    rel = os.path.relpath(path, ROOT)
    if args.dry_run:
        print(f"--- {rel} (dry run, not written)\n{text}", end="")
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    previous = open(path, encoding="utf-8").read() if os.path.isfile(path) else None
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        if check() != 0:
            raise Problem("the release files do not check out (above)")
        if args.service == "consul-config" and subprocess.run(
                [sys.executable, os.path.join(ROOT, "scripts", "validate.py")]).returncode != 0:
            raise Problem("config/ does not validate")
        render_check(args.env, args.service, element, repo_dir, revision, path)
    except Problem:
        if previous is None:
            os.remove(path)
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(previous)
        print(f"{rel} left as it was", file=sys.stderr)
        raise
    print(f"wrote {rel}")

    app = f"{args.service}-{args.env}"
    first = ", ".join(f"{n}:{t}" for n, t in tags.items())
    print(f"""
Next (nothing is deployed until Sync):
  git -C {ROOT} switch -c release/{app}-{next(iter(tags.values()))}
  git -C {ROOT} add {rel}
  git -C {ROOT} commit -m "Release {args.service} to {args.env}: {first}"
  git -C {ROOT} push -u origin HEAD        # then merge it to main (or push straight to main: git push origin HEAD:main)
Once it is on main (Argo CD polls every 3 minutes):
  argocd app get {app} --refresh           # OutOfSync: the release is waiting
  argocd app diff {app}                    # what Sync will change
  argocd app sync {app} && argocd app wait {app} --health --timeout 900
Roll back: argocd app history {app}; argocd app rollback {app} <id>   (then revert this commit, or the next Sync redeploys it)""")
    return 0


def render_check(env, service, element, repo_dir, revision, release_file):
    """helm template the chart as Argo CD will: from the released commit (git archive, not the working
    tree), with its value files and the release file; the library packaged from this checkout."""
    if not shutil.which("helm") or not os.path.isdir(repo_dir):
        print("render: skipped (no helm or no local checkout)")
        return
    tree = revision if SHA.match(revision) else f"origin/{revision}"
    paths = [element["chartPath"]] + (["config", "scripts"] if os.path.abspath(repo_dir) == ROOT else [])
    with tempfile.TemporaryDirectory() as tmp:
        archive = subprocess.run(["git", "-C", repo_dir, "archive", "--format=tar", tree, "--", *paths], capture_output=True)
        if archive.returncode != 0:
            print(f"render: skipped (cannot read {element['chartPath']} at {tree[:12]})")
            return
        subprocess.run(["tar", "-x", "-C", tmp], input=archive.stdout, check=True)
        chart = os.path.join(tmp, element["chartPath"])
        meta = load_yaml(os.path.join(chart, "Chart.yaml")) or {}
        if any(d.get("name") == "runvance-service" for d in meta.get("dependencies") or []):
            subprocess.run(["helm", "package", os.path.join(ROOT, "deploy/charts/runvance-service"), "-d",
                            os.path.join(chart, "charts")], check=True, capture_output=True)
        cmd = ["helm", "template", element["releaseName"], chart, "-n", element["namespace"]]
        for vf in element["valueFiles"]:
            cmd += ["-f", os.path.join(chart, vf)]
        cmd += ["-f", release_file]
        result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Problem(f"helm template of {element['chartPath']} at {tree[:12]} failed:\n{result.stderr.strip()}")
    kinds = sorted(set(re.findall(r"^kind: (\S+)", result.stdout, re.M)))
    print(f"render: ok at {tree[:12]} ({', '.join(kinds)})")


def main():
    sys.stdout.reconfigure(line_buffering=True)  # keep stdout and stderr in order when piped
    if len(sys.argv) < 2 or sys.argv[1] not in ("check", "release"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        return check() if sys.argv[1] == "check" else release(sys.argv[2:])
    except Problem as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
