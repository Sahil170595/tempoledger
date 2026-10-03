"""Review the exact Git index or commit for disallowed assets and common secret forms."""

import argparse
import ast
import json
import re
import subprocess
from pathlib import PurePosixPath


def git(*args):
    return subprocess.check_output(["git", *args])


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true")
    group.add_argument("--ref")
    parser.add_argument("--deny-pattern", action="append", default=[])
    args = parser.parse_args()
    paths = (
        (
            git("ls-files", "--cached", "-z")
            if args.staged
            else git("ls-tree", "-rz", "--name-only", args.ref)
        )
        .decode()
        .split("\0")
    )
    secrets = [
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
        r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b",
        r"(?i)bearer\s+[A-Za-z0-9_-]{24,}",
        r"(?i)https?://[^\s/]+:[^\s/]+@",
        r"(?i)[A-Z]:[/\\]Users[/\\]",
    ]
    allowed = {".py", ".md", ".json", ".toml", ".ini", ".yml"}
    allowed_names = {"LICENSE", ".gitignore", ".env.example"}
    errors = []
    total = 0
    for path in filter(None, paths):
        p = PurePosixPath(path)
        if p.name not in allowed_names and p.suffix not in allowed:
            errors.append(f"Unexpected file type: {path}")
        if any(part in {".claude", "__pycache__", ".venv", "output", "logs"} for part in p.parts):
            errors.append(f"Disallowed directory: {path}")
        raw = git("show", f":{path}" if args.staged else f"{args.ref}:{path}")
        total += len(raw)
        if not raw or len(raw) > 128 * 1024 or b"\0" in raw:
            errors.append(f"Empty, oversized or binary content: {path}")
            continue
        text = raw.decode("utf-8")
        patterns = secrets + ([] if path in {"LICENSE", "NOTICE.md"} else args.deny_pattern)
        for pattern in patterns:
            if re.search(pattern, path + "\n" + text):
                errors.append(f"Pattern match in {path}: {pattern}")
        if p.suffix == ".py":
            ast.parse(text, filename=path)
        if p.suffix == ".json":
            json.loads(text)
    if errors:
        raise SystemExit("\n".join(errors))
    print(
        f"PASS: {len(list(filter(None, paths)))} text files, {total} bytes; exact Git content scanned"
    )


if __name__ == "__main__":
    main()
