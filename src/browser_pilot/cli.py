from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys

from .browser import BrowserPilot
from .credentials import CredentialStore
from .storage import profile_dir, profile_name


def _credential_command(args: argparse.Namespace) -> int:
    store = CredentialStore()
    if args.credential_action == "add":
        password = getpass.getpass("Password (saved in your OS keyring): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            print("Passwords did not match.", file=sys.stderr)
            return 2
        store.save(args.site, args.username, password)
        print(f"Saved credential for {args.username} at {store.normalize_site(args.site)} in the OS keyring.")
    elif args.credential_action == "list":
        print(json.dumps(store.list(), indent=2))
    elif args.credential_action == "remove":
        store.delete(args.site, args.username)
        print(f"Removed credential for {args.username} at {store.normalize_site(args.site)}.")
    return 0


def _login(args: argparse.Namespace) -> int:
    pilot = BrowserPilot(headless=False)
    try:
        if args.url:
            pilot.navigate(args.url)
        print(f"Browser profile: {profile_dir()}")
        print("Complete sign-in in the browser window. Press Enter here when you are finished.")
        input()
    finally:
        pilot.close()
    print("Browser session saved locally.")
    return 0


def _install_browser(_args: argparse.Namespace) -> int:
    return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])


def main() -> None:
    parser = argparse.ArgumentParser(prog="browser-pilot", description="Persistent, human-supervised Playwright browser tools")
    commands = parser.add_subparsers(dest="command", required=True)

    credentials = commands.add_parser("credential", help="Manage OS-keyring website credentials")
    credential_actions = credentials.add_subparsers(dest="credential_action", required=True)
    add = credential_actions.add_parser("add", help="Save a credential using a hidden password prompt")
    add.add_argument("--site", required=True, help="Website hostname or URL")
    add.add_argument("--username", required=True)
    listing = credential_actions.add_parser("list", help="List saved site and username labels")
    remove = credential_actions.add_parser("remove", help="Delete a saved credential")
    remove.add_argument("--site", required=True)
    remove.add_argument("--username", required=True)

    login = commands.add_parser("login", help="Open the saved browser profile for manual login")
    login.add_argument("url", nargs="?", help="Optional login page URL")
    commands.add_parser("install-browser", help="Install the Playwright Chromium browser")
    commands.add_parser("profile", help="Show the active persistent browser profile")
    commands.add_parser("workflows", help="List saved workflows")
    commands.add_parser("mcp", help="Run the MCP server over stdio")
    args = parser.parse_args()

    try:
        if args.command == "credential":
            code = _credential_command(args)
        elif args.command == "login":
            code = _login(args)
        elif args.command == "install-browser":
            code = _install_browser(args)
        elif args.command == "profile":
            print(f"{profile_name()}: {profile_dir()}")
            code = 0
        elif args.command == "workflows":
            print(json.dumps(BrowserPilot().list_workflows(), indent=2))
            code = 0
        else:
            from .server import main as run_server

            run_server()
            code = 0
    except (RuntimeError, ValueError, OSError) as error:
        print(f"browser-pilot: {error}", file=sys.stderr)
        code = 1
    raise SystemExit(code)
