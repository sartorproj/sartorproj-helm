#!/usr/bin/env python3
"""Tests for leakcheck.py. Only synthetic terms are used.

Run:  python3 -m unittest discover -s .github/scripts -p 'test_*.py' -v
Bypass matrix:  python3 .github/scripts/test_leakcheck.py --matrix
"""

from __future__ import annotations

import base64
import codecs
import contextlib
import gzip
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leakcheck  # noqa: E402

TERM = "secret-cluster-xyz"
TERM2 = "Acme-Internal"
SUFFIX = leakcheck.PRIVATE_HOST_SUFFIXES[0]


def ip(*octets) -> str:
    """Build dotted IPs at runtime so this file's own diff stays free of private IP literals."""
    return ".".join(str(o) for o in octets)


def b64(s: str | bytes, urlsafe: bool = False, pad: bool = True) -> str:
    data = s.encode() if isinstance(s, str) else s
    out = (base64.urlsafe_b64encode if urlsafe else base64.b64encode)(data).decode()
    return out if pad else out.rstrip("=")


class GenericPatternTest(unittest.TestCase):
    CASES = [
        # (text, expected category or None)
        (ip(10, 0, 0, 1), "private IPv4 (10/8)"),
        (ip(10, 255, 255, 255), "private IPv4 (10/8)"),
        (ip("010", 0, 0, 1), "private IPv4 (10/8)"),
        (ip(172, 16, 0, 1), "private IPv4 (172.16/12)"),
        (ip(172, 31, 255, 254), "private IPv4 (172.16/12)"),
        (ip(172, 15, 0, 1), None),
        (ip(172, 32, 0, 1), None),
        (ip(192, 168, 1, 1), "private IPv4 (192.168/16)"),
        (ip(192, 169, 1, 1), None),
        (ip(100, 64, 0, 1), "CGNAT IPv4 (100.64/10)"),
        (ip(100, 127, 255, 255), "CGNAT IPv4 (100.64/10)"),
        (ip(100, 63, 255, 255), None),
        (ip(100, 128, 0, 1), None),
        (ip(10, 0, 0, 256), None),
        (ip(10, 300, 0, 1), None),
        (ip(8, 8, 8, 8), None),
        ("1.10.2.3", None),
        ("version 1.10.2.3 released", None),
        ("v" + ip(1, 2, 10, 0, 0, 1), None),
        (ip(10, 1, 2, 3, 4), None),
        ("image: app:" + ip(10, 2, 3, 4), "private IPv4 (10/8)"),
        ("cidr: " + ip(10, 0, 0, 0) + "/8", "private IPv4 (10/8)"),
        ("[" + ip(192, 168, 0, 0) + "/16]", "private IPv4 (192.168/16)"),
        ("::ffff:" + ip(10, 0, 0, 1), "private IPv4 (10/8)"),
        ("end of sentence " + ip(172, 20, 1, 1) + ".", "private IPv4 (172.16/12)"),
        ("node ip-" + "-".join(["10", "0", "1", "23"]) + " ready", "private IPv4 (10/8)"),
        ("ip-" + "-".join(["10", "0", "1", "300"]), None),
        ("vip-" + "-".join(["10", "0", "1", "23"]), None),
        ("foo" + SUFFIX, "private DNS hostname"),
        ("FOO" + SUFFIX.upper(), "private DNS hostname"),
        ("foo" + SUFFIX + ".", "private DNS hostname"),
        ("https://a.b-c" + SUFFIX + ":443/x", "private DNS hostname"),
        ("foo" + SUFFIX + "work", None),
        ("foo" + SUFFIX + ".example.com", None),
        ("*" + SUFFIX, None),
        ("no hostname here", None),
    ]

    def test_table(self):
        for text, want in self.CASES:
            with self.subTest(text=text):
                got = [cat for cat, _ in leakcheck.generic_hits(text.casefold())]
                if want is None:
                    self.assertEqual(got, [])
                else:
                    self.assertIn(want, got)


class DenylistVariantTest(unittest.TestCase):
    """Single-text cases: does the scanner find TERM in this text?"""

    CASES = [
        ("plain", TERM, True),
        ("upper case", TERM.upper(), True),
        ("mixed case", "SeCrEt-ClUsTeR-XyZ", True),
        ("underscores", "secret_cluster_xyz", True),
        ("dots", "secret.cluster.xyz", True),
        ("spaces", "secret cluster xyz", True),
        ("no separators", "secretclusterxyz", True),
        ("fuzzy needs word boundaries", "xsecret cluster xyz", False),
        ("two separators", "secret - cluster - xyz", False),
        ("embedded", "url: https://" + TERM + ".example.com/x", True),
        ("base64", b64(TERM), True),
        ("base64 unpadded", b64(TERM, pad=False), True),
        ("base64 urlsafe", b64("~~" + TERM + "??", urlsafe=True), True),
        ("base64 of longer text", "token: " + b64("host=" + TERM + " port=443"), True),
        ("base64 glued to text", "abc" + b64("xx " + TERM), True),
        ("double base64", b64(b64(TERM)), True),
        ("url-encoded", quote(TERM, safe=""), True),
        ("url-encoded every byte", "".join(f"%{b:02X}" for b in TERM.encode()), True),
        ("hex", TERM.encode().hex(), True),
        ("hex 0x", "0x" + TERM.encode().hex(), True),
        ("\\x escapes", "".join(f"\\x{b:02x}" for b in TERM.encode()), True),
        ("\\u escapes", "".join(f"\\u{ord(c):04x}" for c in TERM), True),
        ("html entities", "".join(f"&#{ord(c)};" for c in TERM), True),
        ("zero-width space", "\u200b".join(TERM), True),
        ("soft hyphen + bidi", "\u00ad".join(TERM[:6]) + "\u202e" + TERM[6:], True),
        ("fullwidth", "".join(chr(ord(c) + 0xFEE0) if c.isalnum() else c for c in TERM), True),
        ("utf-16 nul bytes", "\x00".join(TERM), True),
        ("separator inside a part", "sec-ret-cluster-xyz", False),
        ("cyrillic homoglyph", TERM.replace("c", "\u0441"), False),
        ("reversed", TERM[::-1], False),
        ("rot13", codecs.encode(TERM, "rot13"), False),
        ("gzip+base64", b64(gzip.compress(TERM.encode())), False),
        ("unrelated", "nothing to see here", False),
    ]

    def setUp(self):
        self.scanner = leakcheck.Scanner([TERM], leakcheck.Allowlist(), ".leakcheck-allow")

    def test_table(self):
        for name, text, want in self.CASES:
            with self.subTest(name=name):
                hits = self.scanner.scan(leakcheck.Unit("t", text))
                self.assertEqual(bool(hits), want, hits)


class AllowlistTest(unittest.TestCase):
    CASES = [
        # (allow lines, unit path, text, expect finding)
        ([], "a.md", ip(192, 168, 1, 1), True),
        (["path:docs/*.md"], "docs/a.md", ip(192, 168, 1, 1), False),
        (["path:docs/*.md"], "src/a.md", ip(192, 168, 1, 1), True),
        ([ip(192, 168, 1, 1)], "a.md", ip(192, 168, 1, 1), False),
        ([ip(192, 168, 1, 1)], "a.md", ip(192, 168, 1, 2), True),
        (["# comment", "", "Foo" + SUFFIX.upper()], "a.md", "foo" + SUFFIX, False),
        (["path:docs/*.md"], "docs/a.md", TERM, True),
        ([TERM], "a.md", TERM, True),
        ([], ".leakcheck-allow", ip(10, 0, 0, 1), False),
        ([], ".leakcheck-allow", TERM, True),
    ]

    def test_table(self):
        for allow, path, text, want in self.CASES:
            with self.subTest(allow=allow, path=path):
                scanner = leakcheck.Scanner([TERM], leakcheck.Allowlist(allow), ".leakcheck-allow")
                self.assertEqual(bool(scanner.scan(leakcheck.Unit("t", text, path=path))), want)


# --- end-to-end bypass matrix against a throwaway git repo -------------------------


@dataclass
class Case:
    name: str
    expect: bool
    files: dict = field(default_factory=dict)  # path -> str|bytes|None (None deletes)
    base_files: dict = field(default_factory=dict)
    renames: dict = field(default_factory=dict)  # old -> new (applied in head commit)
    message: str = "chore: update"
    author: str = "Dev Example"
    title: str = ""
    body: str = ""
    denylist: tuple = (TERM, TERM2)
    note: str = ""


def _bin(payload: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x01" + payload + b"\x00\xff\xfe\x00"


BYPASS_CASES = [
    Case("term in added line", True, files={"a.txt": f"name: {TERM}\n"}),
    Case("term in commit subject", True, message=f"fix: talk to {TERM}"),
    Case("term in commit body", True, message=f"fix: x\n\nDetails about {TERM}\n"),
    Case("term in commit trailer", True, message=f"fix: x\n\nRefs: {TERM2}\n"),
    Case("term in author name", True, author=TERM2),
    Case("term in PR title", True, title=f"feat: {TERM}"),
    Case("term in PR body", True, body=f"Some text\n\nDeployed on {TERM}.\n"),
    Case("term split across PR body lines", True, body="Deployed on secret-clus\nter-xyz\n"),
    Case("different case", True, files={"a.txt": TERM.upper() + "\n"}),
    Case("base64-encoded", True, files={"s.yaml": f"data: {b64(TERM)}\n"}),
    Case("split across lines", True, files={"a.txt": "host: secret-clus\nter-xyz\n"}),
    Case("split across lines with string concat", False,
         files={"a.py": 'h = ("secret-clus"\n     "ter-xyz")\n'},
         note="quotes between halves of one word break the match"),
    Case("inside an added file path", True, files={f"docs/{TERM}.md": "hello\n"}),
    Case("inside a renamed file path", True, base_files={"docs/notes.md": "hello\n"},
         renames={"docs/notes.md": f"docs/{TERM2}-notes.md"}),
    Case("url-encoded", True, files={"a.txt": "https://x/?q=" + "".join(f"%{b:02X}" for b in TERM.encode()) + "\n"}),
    Case("hex", True, files={"a.txt": TERM.encode().hex() + "\n"}),
    Case("\\x escaped", True, files={"a.c": '"' + "".join(f"\\x{b:02x}" for b in TERM.encode()) + '"\n'}),
    Case("zero-width characters", True, files={"a.md": "\u200b".join(TERM) + "\n"}),
    Case("binary file (raw bytes)", True, files={"img.png": _bin(TERM.encode())}),
    Case("binary file (utf-16)", True, files={"img.bin": _bin(TERM.encode("utf-16-le"))}),
    Case("binary file (compressed)", False, files={"a.gz": gzip.compress(TERM.encode() * 3)},
         note="compressed archives/images are not unpacked"),
    Case("deleted line", False, base_files={"a.txt": f"keep\n{TERM}\n"}, files={"a.txt": "keep\n"},
         note="by design: removed lines are already public on the base branch"),
    Case("homoglyph (cyrillic)", False, files={"a.txt": TERM.replace("c", "\u0441") + "\n"}),
    Case("CGNAT 100.64/10 IP", True, files={"a.txt": f"addr: {ip(100, 100, 7, 9)}\n"}),
    Case("172.31.x IP", True, files={"a.txt": f"addr: {ip(172, 31, 4, 5)}\n"}),
    Case("192.168.x IP", True, files={"a.txt": f"addr: {ip(192, 168, 40, 2)}\n"}),
    Case("IP inside CIDR", True, files={"a.txt": f"allow: [{ip(10, 20, 0, 0)}/16]\n"}),
    Case("IP in commit message", True, message=f"fix: reach {ip(10, 9, 8, 7)}"),
    Case("IP base64-encoded", True, files={"a.txt": b64("http://" + ip(192, 168, 3, 3) + "/") + "\n"}),
    Case("IP as integer", False, files={"a.txt": "addr: 167772161\n"}, note="decimal/hex IP forms"),
    Case("version string 1.10.2.3", False, files={"Chart.yaml": "version: 1.10.2.3\n"},
         note="expected: not an in-range private IP"),
    Case("private DNS suffix, uppercase", True, files={"a.txt": "FOO" + SUFFIX.upper() + "\n"}),
    Case("private DNS suffix, trailing dot", True, files={"a.txt": "dns: foo" + SUFFIX + ".\n"}),
    Case("no denylist configured: generic still runs", True, denylist=(),
         files={"a.txt": f"{TERM} at {ip(192, 168, 1, 1)}\n"}),
    Case("no denylist configured: term not caught", False, denylist=(), files={"a.txt": TERM + "\n"},
         note="without the secret only generic checks run (fork PRs)"),
    Case("clean change", False, files={"a.txt": "replicaCount: 2\nimage: ghcr.io/example/app:1.2.3\n"}),
]


def _git(repo: str, *args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def _write(repo: str, files: dict) -> None:
    for path, data in files.items():
        full = os.path.join(repo, path)
        if data is None:
            os.remove(full)
            continue
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else data.encode())


def run_case(case: Case) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as repo:
        _git(repo, "init", "-q", "-b", "main")
        for k, v in (("user.name", "Dev Example"), ("user.email", "dev@example.com"),
                     ("commit.gpgsign", "false")):
            _git(repo, "config", k, v)
        _write(repo, {"README.md": "base\n", **case.base_files})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        base = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "-b", "feature")
        for old, new in case.renames.items():
            os.makedirs(os.path.dirname(os.path.join(repo, new)), exist_ok=True)
            _git(repo, "mv", old, new)
        _write(repo, case.files)
        _git(repo, "add", "-A")
        env = dict(os.environ, GIT_AUTHOR_NAME=case.author)
        _git(repo, "commit", "-q", "--allow-empty", "-m", case.message, env=env)
        head = _git(repo, "rev-parse", "HEAD")

        denylist = os.path.join(repo, ".git", "denylist")
        with open(denylist, "w") as fh:
            fh.write("\n".join(case.denylist) + ("\n" if case.denylist else ""))
        event = os.path.join(repo, ".git", "event.json")
        with open(event, "w") as fh:
            json.dump({"pull_request": {"title": case.title, "body": case.body,
                                        "base": {"sha": base}, "head": {"sha": head}}}, fh)

        out = io.StringIO()
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = leakcheck.main(["--event-file", event, "--denylist", denylist])
        finally:
            os.chdir(cwd)
        return code, out.getvalue()


class BypassMatrixTest(unittest.TestCase):
    def test_matrix(self):
        for case in BYPASS_CASES:
            with self.subTest(case=case.name):
                code, out = run_case(case)
                self.assertEqual(code, 1 if case.expect else 0, out)
                for term in (TERM, TERM2):
                    for form in (term, term.lower(), term.upper()):
                        self.assertNotIn(form, out, "matched text must never be printed")

    def test_no_denylist_warns(self):
        code, out = run_case(Case("x", False, denylist=()))
        self.assertEqual(code, 0)
        self.assertIn("no denylist configured", out)

    def test_redacts_paths_with_hits(self):
        code, out = run_case(Case("p", True, files={f"{TERM}/a.txt": f"{TERM}\n"}))
        self.assertEqual(code, 1)
        self.assertIn("<redacted path #1>", out)

    def test_reports_location(self):
        code, out = run_case(Case("loc", True, files={"a.txt": "x\ny\n" + b64(TERM) + "\n"}))
        self.assertEqual(code, 1)
        self.assertIn("a.txt:3: denylist entry 1 (base64-decoded)", out)


class RangeTest(unittest.TestCase):
    def test_push_from_zero_sha_scans_head_commit(self):
        with tempfile.TemporaryDirectory() as repo:
            _git(repo, "init", "-q", "-b", "main")
            _git(repo, "config", "user.name", "Dev Example")
            _git(repo, "config", "user.email", "dev@example.com")
            _write(repo, {"a.txt": TERM + "\n"})
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "root")
            deny = os.path.join(repo, ".git", "denylist")
            with open(deny, "w") as fh:
                fh.write(TERM + "\n")
            event = os.path.join(repo, ".git", "event.json")
            with open(event, "w") as fh:
                json.dump({"before": leakcheck.ZERO_SHA, "after": _git(repo, "rev-parse", "HEAD")}, fh)
            cwd = os.getcwd()
            os.chdir(repo)
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = leakcheck.main(["--event-file", event, "--denylist", deny])
            finally:
                os.chdir(cwd)
            self.assertEqual(code, 1)


def print_matrix() -> None:
    print("| Attempt | Expected | Result | Note |\n|---|---|---|---|")
    for case in BYPASS_CASES:
        code, _ = run_case(case)
        result = "caught" if code == 1 else "not flagged"
        ok = "ok" if (code == 1) == case.expect else "MISMATCH"
        print(f"| {case.name} | {'caught' if case.expect else 'not flagged'} | {result} ({ok}) | {case.note} |")


if __name__ == "__main__":
    if "--matrix" in sys.argv:
        print_matrix()
    else:
        unittest.main()
