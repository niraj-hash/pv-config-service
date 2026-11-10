#!/usr/bin/env python3
"""Checks what one nameserver answers against deploy/dns/runvance.com.zone, record set by record set.

    scripts/dns-check.py dns1.registrar-servers.com    # Namecheap, before the move: what is missing from the file
    scripts/dns-check.py ns1-01.azure-dns.com          # Azure DNS, after the import, before switching nameservers
    scripts/dns-check.py 1.1.1.1                       # a public resolver, after the switch

Prints ok, MISSING or DIFFERS for each record set and exits 1 when any is not ok. The wildcard is
checked by asking for a random name under it. Reads only the subset of BIND the zone file uses
($ORIGIN, $TTL, name [ttl] IN type data). Needs dig.
"""
import pathlib
import secrets
import shlex
import subprocess
import sys

ZONE = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "dns" / "runvance.com.zone"


def tokens(line):
    lex = shlex.shlex(line, posix=True)
    lex.commenters = ";"
    lex.whitespace_split = True
    return list(lex)


def parse(path):
    origin, records = "", {}
    for raw in path.read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith(";"):
            continue
        t = tokens(raw)
        if not t:
            continue
        if t[0] == "$ORIGIN":
            origin = t[1]
            continue
        if t[0] == "$TTL":
            continue
        name, rest = t[0], t[1:]
        if rest and rest[0].isdigit():
            rest = rest[1:]
        if rest and rest[0] == "IN":
            rest = rest[1:]
        rtype, data = rest[0].upper(), rest[1:]
        fqdn = origin if name == "@" else f"{name}.{origin}"
        if rtype == "TXT":
            value = "".join(data)
        elif rtype == "MX":
            value = f"{data[0]} {data[1].lower()}"
        else:
            value = data[0].lower()
        records.setdefault((fqdn, rtype), set()).add(value)
    return records


def answer(server, name, rtype):
    out = subprocess.run(["dig", f"@{server}", "+short", "+time=5", "+tries=2", name, rtype],
                         capture_output=True, text=True).stdout
    values = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if rtype == "TXT":
            values.add("".join(tokens(line)))
        else:
            values.add(line.lower())
    return values


def main():
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        print(__doc__.strip())
        return 2
    server, failed = sys.argv[1], 0
    for (fqdn, rtype), expected in sorted(parse(ZONE).items()):
        query = fqdn.replace("*", f"wildcard-check-{secrets.token_hex(4)}", 1) if fqdn.startswith("*.") else fqdn
        got = answer(server, query, rtype)
        if got == expected:
            status = "ok"
        else:
            failed += 1
            status = "MISSING" if not got else "DIFFERS"
        line = f"{status:8} {rtype:5} {fqdn}"
        if status != "ok":
            line += f"\n           file:   {sorted(expected)}\n           server: {sorted(got)}"
        print(line)
    print(f"\n{server}: {failed} record set(s) not as in {ZONE.name}" if failed else f"\n{server}: every record set matches {ZONE.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
