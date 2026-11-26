#!/usr/bin/env python3
"""Scan a git range for leaked internal terms, private IPv4 addresses and private DNS hostnames.

Scope: added diff lines, commit messages (and author/committer identities),
added/modified/renamed file paths, PR title and body.

Matched text is never printed: denylist hits are reported as "denylist entry N",
generic hits by category, and any path that itself contains a hit is redacted.

Exit codes: 0 clean, 1 findings, 2 usage or git error.
See docs/leakcheck.md.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import fnmatch
import html
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Iterator
from urllib.parse import unquote

ZERO_SHA = "0" * 40
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

MAX_DECODE_DEPTH = 3
MAX_VARIANTS = 64
MIN_B64_LEN = 16
MIN_HEX_LEN = 16
PRINTABLE_RATIO = 0.9

IPV4_RE = re.compile(
    r"(?<![0-9.])([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})(?![0-9]|\.[0-9])",
    re.ASCII,
)
DASHED_IPV4_RE = re.compile(
    r"(?<![0-9a-z])ip-([0-9]{1,3})-([0-9]{1,3})-([0-9]{1,3})-([0-9]{1,3})(?![0-9])",
    re.ASCII,
)
# Always-checked private DNS suffixes. Spelled as code points so the literal never appears in
# this file's own diffs.
PRIVATE_HOST_SUFFIXES = ("".join(map(chr, (46, 116, 115, 46, 110, 101, 116))),)
SUFFIX_RES = [
    re.compile(r"(?<=[a-z0-9_-])" + re.escape(s) + r"(?![a-z0-9-]|\.[a-z0-9])", re.ASCII)
    for s in PRIVATE_HOST_SUFFIXES
]
HOST_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")

B64_RE = re.compile(r"[A-Za-z0-9+/_-]{%d,}={0,2}" % MIN_B64_LEN, re.ASCII)
HEX_RE = re.compile(r"(?:0x)?((?:[0-9a-f]{2}){%d,})" % (MIN_HEX_LEN // 2), re.ASCII)
ESC_RE = re.compile(
    r"\\x([0-9a-fA-F]{2})|\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})|\\([0-7]{1,3})", re.ASCII
)


@dataclass(frozen=True)
class Finding:
    location: str
    what: str
    via: str


@dataclass
class Unit:
    """A piece of text to scan, with a printable location and an optional file path."""

    location: str
    text: str
    path: str | None = None
    denylist_only: bool = False
    group: str | None = None


# --- normalization and decoding -------------------------------------------------


def clean(text: str) -> str:
    """NFKC-normalize and drop invisible format characters (zero-width, BOM, soft hyphen, bidi)."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


def _mostly_printable(s: str) -> bool:
    if not s:
        return False
    good = sum(1 for c in s if c != "\ufffd" and (c.isprintable() or c in "\t\r\n"))
    return good / len(s) >= PRINTABLE_RATIO


def _bytes_to_text(b: bytes) -> str | None:
    if len(b) < 4:
        return None
    s = b.decode("utf-8", errors="replace")
    return s if _mostly_printable(s) else None


def decode_url(s: str) -> list[str]:
    if "%" not in s:
        return []
    out = unquote(s, errors="replace")
    return [out] if out != s else []


def decode_escapes(s: str) -> list[str]:
    if "\\" not in s and "&" not in s:
        return []

    def repl(m: re.Match) -> str:
        hex2, hex4, hex8, octal = m.groups()
        code = int(hex2 or hex4 or hex8, 16) if not octal else int(octal, 8)
        try:
            return chr(code)
        except (ValueError, OverflowError):
            return m.group(0)

    out = html.unescape(ESC_RE.sub(repl, s))
    return [out] if out != s else []


def decode_hex(s: str) -> list[str]:
    out = []
    for m in HEX_RE.finditer(s.lower()):
        text = _bytes_to_text(bytes.fromhex(m.group(1)))
        if text:
            out.append(text)
    return out


def decode_base64(s: str) -> list[str]:
    out = []
    for m in B64_RE.finditer(s):
        token = m.group(0).rstrip("=")
        alt = "-_" if ("-" in token or "_" in token) else None
        # Try every alignment so a token glued to other alphanumerics still decodes.
        for offset in range(4):
            chunk = token[offset:]
            chunk = chunk[: len(chunk) - len(chunk) % 4] if len(chunk) % 4 == 1 else chunk
            if len(chunk) < MIN_B64_LEN - 3:
                break
            chunk += "=" * (-len(chunk) % 4)
            try:
                raw = base64.b64decode(chunk, altchars=alt, validate=True)
            except (binascii.Error, ValueError):
                continue
            text = _bytes_to_text(raw)
            if text:
                out.append(text)
                break
    return out


DECODERS = (
    ("url-decoded", decode_url),
    ("escape-decoded", decode_escapes),
    ("hex-decoded", decode_hex),
    ("base64-decoded", decode_base64),
)


def variants(text: str) -> list[tuple[str, str]]:
    """Return (label, cleaned text) for the text and everything it plausibly decodes to."""
    first = [("plain", clean(text))]
    if "\x00" in text:
        first.append(("nul-stripped", clean(text.replace("\x00", ""))))
    seen = {v for _, v in first}
    result = list(first)
    frontier = list(first)
    for _ in range(MAX_DECODE_DEPTH):
        nxt = []
        for label, value in frontier:
            for dlabel, decoder in DECODERS:
                for decoded in decoder(value):
                    decoded = clean(decoded)
                    if decoded in seen or len(result) >= MAX_VARIANTS:
                        continue
                    seen.add(decoded)
                    item = (dlabel if label == "plain" else f"{label}>{dlabel}", decoded)
                    result.append(item)
                    nxt.append(item)
        frontier = nxt
        if not frontier:
            break
    return result


# --- matchers ---------------------------------------------------------------------


def compile_term(term: str) -> re.Pattern:
    """Case-insensitive fixed-substring match. A term with several alphanumeric parts also
    matches as a whole word with each separator swapped for one other character or dropped
    (so "a-b" also matches "a_b", "a.b", "a b" and "ab", but not inside "xa by")."""
    folded = clean(term).casefold()
    exact = re.escape(folded)
    parts = [p for p in re.split(r"[\W_]+", folded) if p]
    if len(parts) < 2:
        return re.compile(exact)
    fuzzy = r"(?<![a-z0-9])" + r"[\W_]?".join(re.escape(p) for p in parts) + r"(?![a-z0-9])"
    return re.compile(f"{exact}|{fuzzy}")


def _octets_ok(groups: Iterable[str]) -> list[int] | None:
    nums = [int(g) for g in groups]
    return nums if all(n <= 255 for n in nums) else None


def ip_category(octets: list[int]) -> str | None:
    a, b = octets[0], octets[1]
    if a == 10:
        return "private IPv4 (10/8)"
    if a == 172 and 16 <= b <= 31:
        return "private IPv4 (172.16/12)"
    if a == 192 and b == 168:
        return "private IPv4 (192.168/16)"
    if a == 100 and 64 <= b <= 127:
        return "CGNAT IPv4 (100.64/10)"
    return None


def generic_hits(text: str) -> Iterator[tuple[str, str]]:
    """Yield (category, matched text) for generic patterns. text must already be casefolded."""
    for rx in (IPV4_RE, DASHED_IPV4_RE):
        for m in rx.finditer(text):
            octets = _octets_ok(m.groups())
            if octets:
                cat = ip_category(octets)
                if cat:
                    yield cat, ".".join(str(o) for o in octets)
    for rx in SUFFIX_RES:
        for m in rx.finditer(text):
            start = m.start()
            while start > 0 and text[start - 1] in HOST_CHARS:
                start -= 1
            yield "private DNS hostname", text[start : m.end()].strip(".")


class Allowlist:
    def __init__(self, lines: Iterable[str] = ()):
        self.paths: list[str] = []
        self.strings: set[str] = set()
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("path:"):
                self.paths.append(line[len("path:") :].strip())
            else:
                self.strings.add(line.casefold())

    def path_allowed(self, path: str | None) -> bool:
        return bool(path) and any(fnmatch.fnmatchcase(path, g) for g in self.paths)

    def string_allowed(self, s: str) -> bool:
        return s.casefold() in self.strings


class Scanner:
    def __init__(self, terms: list[str], allow: Allowlist, allow_file: str):
        self.terms = [compile_term(t) for t in terms]
        self.allow = allow
        self.allow_file = allow_file

    def denylist_hits(self, text: str) -> list[int]:
        folded = text.casefold()
        return [i for i, rx in enumerate(self.terms, 1) if rx.search(folded)]

    def scan(self, unit: Unit) -> list[Finding]:
        found: dict[str, Finding] = {}
        generic_ok = not unit.denylist_only and unit.path != self.allow_file
        generic_ok = generic_ok and not self.allow.path_allowed(unit.path)
        for label, value in variants(unit.text):
            for n in self.denylist_hits(value):
                found.setdefault(f"d{n}", Finding(unit.location, f"denylist entry {n}", label))
            if not generic_ok:
                continue
            for cat, matched in generic_hits(value.casefold()):
                if self.allow.string_allowed(matched):
                    continue
                found.setdefault(f"g{cat}", Finding(unit.location, cat, label))
        return list(found.values())


# --- git plumbing -----------------------------------------------------------------


def git(*args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def commit_exists(rev: str) -> bool:
    try:
        git("cat-file", "-e", f"{rev}^{{commit}}")
        return True
    except RuntimeError:
        return False


def resolve_range(base: str | None, head: str) -> tuple[str, str, str | None]:
    """Return (diff base, head, log base). Falls back to the head commit alone when base is
    missing, all zeros (branch creation) or unreachable (force-push)."""
    head = git("rev-parse", "--verify", f"{head}^{{commit}}").decode().strip()
    if base and base != ZERO_SHA and commit_exists(base):
        mb = git("merge-base", base, head).decode().strip()
        return mb, head, base
    warn(f"base {base or '(none)'} not available; scanning only the head commit")
    if commit_exists(f"{head}^"):
        parent = git("rev-parse", f"{head}^").decode().strip()
        return parent, head, parent
    return EMPTY_TREE, head, None


def _unquote_c(path: str) -> str:
    if not (path.startswith('"') and path.endswith('"')):
        return path
    raw = path[1:-1].encode("latin-1", errors="backslashreplace")
    return raw.decode("unicode_escape").encode("latin-1").decode("utf-8", errors="replace")


def diff_blocks(diff_base: str, head: str) -> Iterator[tuple[str, int, list[str]]]:
    """Yield (path, first new line number, [added lines]) for each run of consecutive added lines."""
    out = git(
        "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--text", "-U0", "-M",
        "--src-prefix=a/", "--dst-prefix=b/", diff_base, head,
    )
    path = None
    old_left = new_left = 0
    new_no = 0
    block: list[str] = []
    block_start = 0

    def flush():
        nonlocal block
        if block and path:
            yield path, block_start, block
        block = []

    for raw in out.split(b"\n"):
        line = raw.decode("utf-8", errors="replace")
        if old_left > 0 or new_left > 0:
            if line.startswith("+"):
                if not block:
                    block_start = new_no
                block.append(line[1:])
                new_no += 1
                new_left -= 1
                continue
            yield from flush()
            if line.startswith("-"):
                old_left -= 1
            continue
        if line.startswith("\\"):
            continue
        yield from flush()
        if line.startswith("diff --git "):
            path = None
        elif line.startswith("+++ "):
            target = line[4:]
            path = None if target == "/dev/null" else _unquote_c(target)
            if path and path.startswith(("b/", '"b/')):
                path = path[2:]
        elif line.startswith("@@ "):
            m = re.match(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                old_left = int(m.group(1)) if m.group(1) is not None else 1
                new_no = int(m.group(2))
                new_left = int(m.group(3)) if m.group(3) is not None else 1
    yield from flush()


def changed_paths(diff_base: str, head: str) -> list[str]:
    out = git("diff", "--no-color", "--name-status", "-z", "-M", diff_base, head)
    fields = out.decode("utf-8", errors="replace").split("\0")
    paths, i = [], 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status[0] in "RC":
            paths.append(fields[i + 2])
            i += 3
        else:
            if status[0] != "D":
                paths.append(fields[i + 1])
            i += 2
    return paths


def commits(log_base: str | None, head: str) -> list[tuple[str, str, str, str]]:
    """Return (sha, author, committer, full message) for every commit in the range."""
    rng = f"{log_base}..{head}" if log_base else head
    extra = [] if log_base else ["-n", "1"]
    out = git("log", *extra, "--format=%H%x00%an <%ae>%x00%cn <%ce>%x00%B%x1e", rng)
    result = []
    for rec in out.decode("utf-8", errors="replace").split("\x1e"):
        rec = rec.lstrip("\n")
        if rec:
            sha, author, committer, body = rec.split("\x00", 3)
            result.append((sha, author, committer, body))
    return result


# --- driver -----------------------------------------------------------------------


def warn(msg: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title=leakcheck::{msg}", file=sys.stderr)
    else:
        print(f"leakcheck: warning: {msg}", file=sys.stderr)


def read_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


def load_terms(path: str) -> list[str]:
    if not path or not os.path.isfile(path):
        return []
    return [t.strip() for t in read_lines(path) if t.strip() and not t.strip().startswith("#")]


def text_units(location: str, text: str) -> list[Unit]:
    """Per-line units plus one whole-text unit (denylist only) to catch terms split across lines."""
    lines = text.splitlines()
    units = [Unit(f"{location} line {i}", ln, group=location) for i, ln in enumerate(lines, 1) if ln.strip()]
    if len(lines) > 1:
        joined = "".join(ln.strip() for ln in lines)
        units.append(Unit(f"{location} lines 1-{len(lines)}", joined, denylist_only=True, group=location))
    return units


def build_units(args, scanner: Scanner) -> tuple[list[Unit], str]:
    diff_base, head, log_base = resolve_range(args.base, args.head)
    units: list[Unit] = []

    redacted: dict[str, str] = {}
    for n, path in enumerate(changed_paths(diff_base, head), 1):
        unit = Unit(f"changed path #{n}", path, path=path)
        units.append(unit)
        if scanner.scan(unit):
            redacted[path] = f"<redacted path #{n}>"

    for path, start, block in diff_blocks(diff_base, head):
        shown = redacted.get(path, path)
        group = f"{path}:{start}"
        for i, line in enumerate(block):
            units.append(Unit(f"{shown}:{start + i}", line, path=path, group=group))
        if len(block) > 1:
            joined = "".join(ln.strip() for ln in block)
            end = start + len(block) - 1
            units.append(Unit(f"{shown}:{start}-{end}", joined, path=path, denylist_only=True, group=group))

    for sha, author, committer, body in commits(log_base, head):
        units.append(Unit(f"commit {sha[:12]} author", author))
        units.append(Unit(f"commit {sha[:12]} committer", committer))
        units.extend(text_units(f"commit {sha[:12]} message", body))

    if args.pr_title:
        units.append(Unit("PR title", args.pr_title))
    if args.pr_body:
        units.extend(text_units("PR body", args.pr_body))

    span = f"{diff_base[:12]}...{head[:12]}"
    return units, span


def apply_event(args) -> None:
    if not args.event_file:
        return
    with open(args.event_file, encoding="utf-8") as fh:
        event = json.load(fh)
    pr = event.get("pull_request")
    if pr:
        args.base = args.base or pr["base"]["sha"]
        args.head = args.head or pr["head"]["sha"]
        args.pr_title = args.pr_title or pr.get("title") or ""
        args.pr_body = args.pr_body or pr.get("body") or ""
    else:
        args.base = args.base or event.get("before")
        args.head = args.head or event.get("after")


def parse_args(argv: list[str]):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", help="base ref (PR base or push 'before'); scans base...head")
    p.add_argument("--head", help="head ref (default HEAD)")
    p.add_argument("--event-file", default=os.environ.get("GITHUB_EVENT_PATH"),
                   help="GitHub event payload; supplies base/head and PR title/body")
    p.add_argument("--pr-title", default="")
    p.add_argument("--pr-body-file", help="file with the PR body")
    p.add_argument("--denylist", default=os.environ.get("LEAKCHECK_DENYLIST_FILE", ".leakcheck-denylist"),
                   help="newline-separated terms (default $LEAKCHECK_DENYLIST_FILE or .leakcheck-denylist)")
    p.add_argument("--allow", default=".leakcheck-allow", help="allowlist file (default .leakcheck-allow)")
    args = p.parse_args(argv)
    args.pr_body = ""
    if args.pr_body_file:
        with open(args.pr_body_file, encoding="utf-8", errors="replace") as fh:
            args.pr_body = fh.read()
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        apply_event(args)
    except (OSError, ValueError, KeyError) as exc:
        print(f"leakcheck: cannot read event file: {exc}", file=sys.stderr)
        return 2
    args.head = args.head or "HEAD"
    if not args.base:
        print("leakcheck: --base is required (or an event file)", file=sys.stderr)
        return 2

    terms = load_terms(args.denylist)
    if not terms:
        warn("no denylist configured (LEAKCHECK_DENYLIST is unset or empty, e.g. on fork PRs); "
             "running generic checks only")
    allow = Allowlist(read_lines(args.allow) if os.path.isfile(args.allow) else [])
    scanner = Scanner(terms, allow, allow_file=os.path.normpath(args.allow))

    try:
        units, span = build_units(args, scanner)
    except RuntimeError as exc:
        print(f"leakcheck: {exc}", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    seen = set()
    for unit in units:
        for f in scanner.scan(unit):
            # A joined multi-line unit only adds terms that no single line of its group matched.
            key = (unit.group, f.what) if unit.group else (f.location, f.what)
            if key not in seen:
                seen.add(key)
                findings.append(f)

    print(f"leakcheck: scanned {span}: {len(units)} units, {len(terms)} denylist entries")
    gha = os.environ.get("GITHUB_ACTIONS") == "true"
    for f in findings:
        print(f"leakcheck: FAIL {f.location}: {f.what} ({f.via})")
        if gha:
            print(f"::error title=leakcheck::{f.location}: {f.what} ({f.via})")
    if findings:
        print(f"leakcheck: {len(findings)} finding(s). Remove them from the branch (rewrite the "
              "commits before they are public, or edit the PR text) and push again.")
        return 1
    print("leakcheck: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
