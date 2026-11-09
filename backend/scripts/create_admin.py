"""Create the first administrator interactively (password is prompted, never passed on the command line).

Usage (from backend/):  python -m scripts.create_admin --username alice --email alice@company.com
"""
import argparse
import getpass
import sys

from pydantic import ValidationError

from app.core import snowflake as sf
from app.schemas.user import UserCreate
from app.services import user_service


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--full-name", default=None)
    args = ap.parse_args()
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Confirm password: "):
        print("Passwords do not match", file=sys.stderr)
        return 1
    try:
        body = UserCreate(username=args.username, email=args.email, password=pw,
                          full_name=args.full_name, roles=["ADMIN"])
        user = user_service.create_user(body, actor=None, ip="cli", action="ADMIN_BOOTSTRAPPED")
    except (ValidationError, Exception) as e:  # noqa: BLE001
        print(f"Failed: {e}", file=sys.stderr)
        return 1
    finally:
        sf.close_connection()
    print(f"Created administrator '{user.username}' ({user.user_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
