"""Compare what the Mac and the phone engine returned for the fake account.

Every value must be identical, except the clock: when each run happened.
Prints each difference with its path; exits 1 if there is any.
"""

from __future__ import annotations

import json
import sys

# When the run happened, and how long it took: differ by nature, carry no
# figure. Anything else that differs is a failure.
CLOCK_KEYS = {"as_of", "started_at", "finished_at", "priced_at", "last_sync", "generated_at", "at", "elapsed"}
RUN_KEYS = {"_runtime", "_python"}


def diff(a, b, path="") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            if key in CLOCK_KEYS or (not path and key in RUN_KEYS):
                continue
            where = f"{path}.{key}" if path else key
            if key not in a or key not in b:
                out.append(f"{where}: only on the {'phone' if key in b else 'Mac'}")
            else:
                out.extend(diff(a[key], b[key], where))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: {len(a)} items on the Mac, {len(b)} on the phone"]
        return [d for i, (x, y) in enumerate(zip(a, b, strict=True)) for d in diff(x, y, f"{path}[{i}]")]
    if a != b or type(a) is not type(b):
        return [f"{path}: Mac {a!r} vs phone {b!r}"]
    return []


def count(value) -> int:
    if isinstance(value, dict):
        return sum(count(v) for k, v in value.items() if k not in CLOCK_KEYS)
    if isinstance(value, list):
        return sum(count(v) for v in value)
    return 1


def main() -> int:
    mac, phone = (json.load(open(p)) for p in sys.argv[1:3])
    print(f"Mac:   {mac['_runtime']} Python {mac['_python']}")
    print(f"Phone: {phone['_runtime']} Python {phone['_python']}")
    failures = 0
    for endpoint in [k for k in mac if k not in RUN_KEYS]:
        if endpoint not in phone:
            print(f"FAIL {endpoint}: missing on the phone")
            failures += 1
            continue
        problems = diff(mac[endpoint], phone[endpoint])
        values = count(mac[endpoint]["body"])
        status = mac[endpoint]["status"]
        if problems:
            failures += 1
            print(f"FAIL {endpoint} ({status}): {len(problems)} of {values} values differ")
            for p in problems[:20]:
                print(f"       {p}")
        else:
            print(f"same {endpoint} ({status}): {values} values")
    print()
    print("Parity: identical" if not failures else f"Parity: {failures} endpoint(s) differ")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
