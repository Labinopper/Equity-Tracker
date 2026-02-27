#!/usr/bin/env python3
"""
Equity Tracker — TOTP secret setup and rotation utility.

Usage
─────
First-time setup (generates a new secret):
    python scripts/setup_totp.py

Reset (generates a new secret to replace an existing one):
    python scripts/setup_totp.py --reset

Verify (shows the current code from an existing secret in env):
    python scripts/setup_totp.py --verify

After running setup or reset
────────────────────────────
1. Copy the printed EQUITY_TOTP_SECRET line into your .env file.
2. Open 1Password → New Item → One-Time Password.
3. Paste the otpauth:// URI into the OTP field.
4. Save the item in 1Password.
5. Run --verify to confirm the secret is correctly configured.
6. Restart the server (the new secret takes effect on next startup).

The TOTP secret persists unchanged across restarts and updates as long as
the EQUITY_TOTP_SECRET environment variable is preserved.  It does NOT
auto-rotate — only running this script with --reset changes it.
"""

from __future__ import annotations

import argparse
import sys
import time


def _generate_secret() -> tuple[str, str]:
    """Return (secret, otpauth_uri) for a new TOTP entry."""
    import pyotp
    secret = pyotp.random_base32()  # 32-char uppercase base32, 160 bits entropy
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(
        name="equity-tracker",
        issuer_name="EquityTracker",
    )
    return secret, uri


def cmd_generate(*, is_reset: bool = False) -> None:
    """Generate a new TOTP secret and print setup instructions."""
    secret, uri = _generate_secret()

    if is_reset:
        print("=" * 60)
        print("TOTP SECRET RESET")
        print("=" * 60)
        print()
        print("WARNING: Your old TOTP secret will stop working.")
        print("Update 1Password BEFORE restarting the server.")
        print()
    else:
        print("=" * 60)
        print("TOTP Secret generated.")
        print("=" * 60)
        print()

    print("Step 1 — Add to your .env file (replace any existing value):")
    print()
    print(f"  EQUITY_TOTP_SECRET={secret}")
    print()
    print("Step 2 — Add to 1Password:")
    print("  Open 1Password → New Item → One-Time Password")
    print("  Paste this URI into the OTP field:")
    print()
    print(f"  {uri}")
    print()
    print("Step 3 — Verify the setup:")
    print("  EQUITY_TOTP_SECRET=<value> python scripts/setup_totp.py --verify")
    print()
    print("Step 4 — Restart the server.")
    print()
    if is_reset:
        print("Your previous TOTP entries in 1Password are now invalid.")
        print("Remove or archive them after adding the new one.")


def cmd_verify() -> None:
    """Verify an existing EQUITY_TOTP_SECRET from the environment."""
    import os
    import pyotp

    secret = os.environ.get("EQUITY_TOTP_SECRET", "").strip()
    if not secret:
        print("ERROR: EQUITY_TOTP_SECRET is not set in the environment.")
        print()
        print("Set it first:")
        print("  Windows PowerShell:  $env:EQUITY_TOTP_SECRET = '<value>'")
        print("  Bash/Linux:          export EQUITY_TOTP_SECRET=<value>")
        sys.exit(1)

    try:
        totp = pyotp.TOTP(secret)
    except Exception as exc:
        print(f"ERROR: Invalid TOTP secret format: {exc}")
        print("Ensure EQUITY_TOTP_SECRET is a base32-encoded string.")
        sys.exit(1)

    current_code = totp.now()
    remaining = 30 - (int(time.time()) % 30)

    print(f"Secret length : {len(secret)} characters")
    print(f"Current code  : {current_code}")
    print(f"Valid for     : ~{remaining}s")
    print()

    if totp.verify(current_code, valid_window=1):
        print("Verification: PASSED — TOTP secret is correctly configured.")
    else:
        print("Verification: FAILED — check the secret value.")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Equity Tracker TOTP secret management.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--reset",
        action="store_true",
        help="Generate a new secret to replace an existing one.",
    )
    group.add_argument(
        "--verify",
        action="store_true",
        help="Verify an existing EQUITY_TOTP_SECRET from the environment.",
    )
    args = parser.parse_args()

    if args.verify:
        cmd_verify()
    elif args.reset:
        cmd_generate(is_reset=True)
    else:
        cmd_generate(is_reset=False)


if __name__ == "__main__":
    main()
