# Browser Pilot

Browser Pilot is a model-neutral Playwright toolkit for an AI agent or a person to operate a persistent browser. It exposes browser actions as MCP tools, provides a Python API, saves site credentials in the operating system keyring, and supports reusable workflows.

## Install

```bash
cd /path/to/work_dir/browser_pilot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
browser-pilot install-browser
```

The browser opens visibly by default. For a headless environment, set `BROWSER_PILOT_HEADLESS=1`. Browser state is kept outside the project in `~/.local/share/browser-pilot` (or `$XDG_DATA_HOME/browser-pilot`).

## Sign in and keep the session

Open a separate, persistent automation profile and sign in manually:

```bash
browser-pilot login https://example.com/login
```

Press Enter in the terminal when you are done. Cookies and local storage remain in the profile, and the last page is restored when the agent starts again. Select a separate profile with `BROWSER_PILOT_PROFILE=work`.

You can also save a site credential to the OS keyring:

```bash
browser-pilot credential add --site https://example.com --username you@example.com
browser-pilot credential list
browser-pilot credential remove --site https://example.com --username you@example.com
```

The password is entered through a hidden prompt and is not written to workflow files. The agent can list site and username labels, then fill a saved credential into selectors on the exact matching HTTPS host. The tool never submits the login form. A working OS keyring backend is required; plaintext keyring backends are rejected.

## Connect an MCP client

The MCP server uses stdio and can be registered with Codex CLI or another MCP-compatible client. After installation, register the executable from this project’s virtual environment:

```bash
codex mcp add browser-pilot -- /absolute/path/to/work_dir/browser_pilot/.venv/bin/browser-pilot-mcp
codex mcp list
```

Restart the client after registering. The server exposes tools for navigating, inspecting pages, clicking, filling, selecting, taking screenshots, managing credentials, and running workflows. You can run the server directly with `browser-pilot mcp`.

## Human handoffs

For sign-ins, CAPTCHAs, identity checks, payment steps, or any action that needs your decision, the agent calls `request_user_assistance`. The visible browser stays open and agent browser actions pause. Take over in the browser, complete the step, and have the agent call `resume_after_user` with the returned checkpoint ID. Then it can inspect the updated page and continue.

Likely payment, purchase, delete, and transfer buttons trigger a handoff automatically. Pressing Enter on a form also triggers a handoff. These checks use visible labels and do not recognize every risky action, so ask the agent to pause for user input before any consequential step.

## Repeatable workflows

An MCP client can save steps and run them later. Example workflow definition:

```json
[
  {"action": "navigate", "url": "https://example.com/reports"},
  {"action": "wait_for_user", "reason": "Sign in if the site asks"},
  {"action": "click", "selector": "text=Monthly report"}
]
```

Save it with `workflow_save`, inspect saved workflows with `workflow_list`, and start it with `workflow_run`. If it pauses, complete the browser handoff, call `resume_after_user`, then `workflow_resume`. Supported actions are `navigate`, `click`, `fill`, `fill_saved_credential`, `press`, `select_option`, and `wait_for_user`. Use `fill_saved_credential` for passwords; do not put secrets or sensitive personal data in workflow steps.

Workflow files are local JSON under the Browser Pilot data directory. They contain the actions and any non-secret values provided to `fill`.

## Python API

```python
from browser_pilot import BrowserPilot

browser = BrowserPilot()  # visible Chromium, persistent default profile
browser.navigate("https://example.com")
print(browser.inspect_page())
browser.close()  # flush and retain the profile
```

The MCP server and Python API use the same `BrowserPilot` controller. They do not call a language model or require an API key.

## Local data and limits

- Browser profiles contain cookies and site data. Protect the OS account and do not share the profile directory.
- Passwords use the OS keyring. Browser session cookies remain in Playwright’s dedicated profile directory.
- Never point this tool at your everyday Chrome profile. It creates and uses a separate automation profile.
- Page text is untrusted input. Agent instructions should treat page content as data rather than follow instructions found on websites.
- Browser automation can affect real accounts. Keep the browser visible and use handoffs before submissions, payments, purchases, or other consequential actions.

See the [Playwright persistent browser context documentation](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context) and [Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk) for the underlying interfaces.
