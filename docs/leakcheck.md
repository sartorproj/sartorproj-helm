# Leak check

This repository is public. The `Leak check` workflow (`.github/workflows/leakcheck.yml`) fails a
pull request or a push to `main` when it finds internal names, private network addresses or
private DNS hostnames in what the change makes public. The scanner is
`.github/scripts/leakcheck.py` (Python 3 standard library only) and runs the same way locally.

## What is scanned

For a pull request, the range is the merge base of the PR's base and head commits up to the
head; for a push, it is `before...after`. Within that range the scanner reads:

- every added diff line (binary files included, read as raw bytes),
- every commit message (subject, body and trailers) plus author and committer name and email,
- every added, modified, copied or renamed file path (the new name),
- the PR title and body, read from the event payload file (`$GITHUB_EVENT_PATH`), never
  interpolated into a shell command.

The workflow also runs on `edited`, so changing the PR title or body re-runs the scan.

## What it looks for

- **Denylist terms** from the `LEAKCHECK_DENYLIST` secret: case-insensitive fixed substrings.
  A term made of several words also matches as a whole word when each separator is swapped for
  one other character or dropped (`a-b` also matches `a_b`, `a.b`, `a b` and `ab`, but not the
  `a b` inside `xa by`).
- **Private IPv4**: 10/8, 172.16/12, 192.168/16, and **CGNAT** 100.64/10, including addresses
  written as CIDRs or as `ip-a-b-c-d` node names.
- **Private DNS hostnames**: any hostname ending in a suffix listed in `PRIVATE_HOST_SUFFIXES`
  in the scanner (any case, optional trailing dot). Add suffixes there only if they are generic;
  organization-specific names belong in the denylist.

Each added line, message line and PR text is also checked after these transforms (up to three
levels deep, so base64 of URL-encoded text is still found):

| Transform | Example of what gets decoded |
|---|---|
| Unicode NFKC, drop zero-width/format characters | fullwidth letters, zero-width spaces, soft hyphens, bidi marks |
| NUL bytes removed | UTF-16 text inside binary files |
| URL decoding | `%73%65...` |
| Escape decoding | `\x73`, `\u0073`, octal escapes, HTML entities |
| Hex | runs of 16+ hex characters, with or without `0x` |
| Base64 | tokens of 16+ characters, standard or URL-safe, padded or not, at any alignment |

Consecutive added lines, commit messages and the PR body are also joined (whitespace at each
line's edges removed), so a term split across lines is still found by the denylist check.

IPv4 matching checks every octet (0-255) and ignores dotted runs that have more than four parts.
A version string like `1.10.2.3` therefore passes, but a four-part version that happens to be
an in-range private address (for example one starting with `10.`) is flagged. If that is a
legitimate value, add it to the allowlist.

## What it does not catch

- Lines that a change deletes: they are already public on the base branch.
- Content inside compressed or packed files (archives, `.tgz` charts, images, PDFs).
- Homoglyphs (for example a Cyrillic letter in place of a Latin one), ROT13, reversed text,
  encryption or custom encodings.
- Separators inserted inside one word of a term, more than one separator character between
  its words, or a word split across lines with quotes or other code between the halves
  (`"ab" +` / `"cd"`).
- IPv4 written as a single integer or in hex, IPv4 split across lines, and IPv6 ULA addresses.
- Anything in a fork PR's denylist scan: GitHub withholds secrets and variables from fork PRs,
  so only the generic checks run there (the job logs a warning). The push to `main` after
  merging still runs the full scan.
- History before the scanned range, tags, issues, PR comments and review comments.

## Output

Matched text is never printed. Denylist hits are reported as `denylist entry N` (the N-th
non-empty line of the denylist), generic hits by category, each with its location
(`path:line`, `commit <sha> message line N`, `PR body line N`, ...) and the transform that
exposed it. A changed path that contains a hit is shown as `<redacted path #N>`. In CI every
denylist entry (as written, lower case and upper case) is registered with `::add-mask::`
before the scan.

## Configure the denylist

Create a repository secret named `LEAKCHECK_DENYLIST` (Settings, Secrets and variables, Actions,
New repository secret) with one term per line. Blank lines and lines starting with `#` are
ignored. Use a secret, not an Actions variable: the workflow falls back to a variable of the
same name, but variables are not masked in logs automatically and can be read by every
collaborator.

To make the check mandatory, add `Leak check` as a required status check in the branch
protection rule (or ruleset) for `main`.

## Run it locally

```sh
# Put the terms outside the repository so they can never be committed or packaged.
export LEAKCHECK_DENYLIST_FILE="$HOME/.config/leakcheck/denylist"

python3 .github/scripts/leakcheck.py --base origin/main
python3 .github/scripts/leakcheck.py --base origin/main --pr-title "feat: ..." --pr-body-file pr-body.md
```

The default denylist path is `.leakcheck-denylist` in the repository root. It is in `.gitignore`,
but `helm package .` would still include it, so prefer `LEAKCHECK_DENYLIST_FILE` or `--denylist`.

Run the scanner's tests and print the bypass matrix:

```sh
python3 -m unittest discover -s .github/scripts -p 'test_*.py' -v
python3 .github/scripts/test_leakcheck.py --matrix
```

## Allowlist

A committed `.leakcheck-allow` file (optional, absent by default) can suppress generic hits
that are legitimate, for example documentation examples. One entry per line:

```text
# Suppress generic hits in files matching a glob
path:docs/examples/*.md
# Suppress one exact address or hostname (case-insensitive)
192.0.2.10
```

The allowlist never suppresses denylist hits, and the allowlist file itself is exempt from the
generic checks only.
