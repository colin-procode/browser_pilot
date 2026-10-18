from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from .credentials import CredentialStore
from .storage import profile_dir, read_json, workflow_dir, write_json


class BrowserPilotError(RuntimeError):
    pass


SENSITIVE_ACTION = re.compile(
    r"\b(pay|purchase|buy now|place order|submit order|checkout|transfer|delete|send payment)\b",
    re.IGNORECASE,
)
MAX_WORKFLOW_STEPS = 100


class BrowserPilot:
    """A persistent Playwright session controlled through explicit browser tools."""

    def __init__(self, *, profile: str | None = None, headless: bool | None = None, timeout_ms: int = 15_000):
        self.profile = profile
        self.user_data_dir = profile_dir(profile)
        if headless is None:
            headless = os.environ.get("BROWSER_PILOT_HEADLESS", "0").lower() in {"1", "true", "yes"}
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._checkpoint: dict[str, Any] | None = None
        self._credentials = CredentialStore()
        self._workflow_state_path = self.user_data_dir / "workflow-state.json"

    def _ensure_browser(self) -> Page:
        if self._context is not None:
            pages = self._context.pages
            self._page = pages[-1] if pages else self._context.new_page()
            if not self._page.is_closed():
                return self._page
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.user_data_dir),
            headless=self.headless,
            accept_downloads=False,
        )
        self._context.set_default_timeout(self.timeout_ms)
        self._page = self._context.pages[-1] if self._context.pages else self._context.new_page()
        last_url = read_json(self.user_data_dir / "session.json", {}).get("url")
        if last_url and self._page.url == "about:blank":
            try:
                self._page.goto(last_url, wait_until="domcontentloaded")
            except Exception:
                pass
        return self._page

    def _ready_page(self) -> Page:
        if self._checkpoint is not None:
            raise BrowserPilotError(
                "Browser actions are paused for user assistance. The user should take over in the open browser, "
                "then call resume_after_user with the checkpoint_id."
            )
        return self._ensure_browser()

    def _save_session(self) -> None:
        if self._page is not None and not self._page.is_closed():
            write_json(self.user_data_dir / "session.json", {"url": self._page.url}, private=True)

    def navigate(self, url: str) -> dict[str, str]:
        page = self._ready_page()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Only http(s) web addresses can be opened")
        page.goto(url, wait_until="domcontentloaded")
        self._save_session()
        return {"url": page.url, "title": page.title()}

    def inspect_page(self, max_chars: int = 18_000) -> dict[str, Any]:
        page = self._ready_page()
        content = page.locator("body").inner_text(timeout=self.timeout_ms)
        controls = page.locator("a,button,input,textarea,select,[role='button'],[role='link']").evaluate_all(
            "els => els.slice(0, 250).map(e => ({tag:e.tagName.toLowerCase(), role:e.getAttribute('role'), "
            "type:e.type || null, text:(e.innerText || e.getAttribute('aria-label') || e.getAttribute('placeholder') || '').trim().slice(0,180), "
            "name:e.getAttribute('name'), placeholder:e.getAttribute('placeholder'), href:e.href || null, "
            "disabled:!!e.disabled}))"
        )
        snapshot = {
            "url": page.url,
            "title": page.title(),
            "text": content[:max(500, min(max_chars, 40_000))],
            "truncated": len(content) > max_chars,
            "controls": controls,
            "login_form_detected": page.locator("input[type='password']").count() > 0,
        }
        captcha_present = page.locator(
            "iframe[src*='captcha' i],iframe[title*='captcha' i],[class*='captcha' i],[id*='captcha' i]"
        ).count() > 0
        payment_form_present = page.locator(
            "input[autocomplete*='cc-' i],input[name*='card' i],input[id*='card' i],iframe[src*='stripe' i]"
        ).count() > 0
        if captcha_present or payment_form_present:
            reason = "A CAPTCHA is present" if captcha_present else "A payment form is present"
            snapshot["human_handoff"] = self.request_user_assistance(reason)
        return snapshot

    def click(self, selector: str) -> dict[str, Any]:
        page = self._ready_page()
        target = page.locator(selector).first
        label = " ".join(
            filter(None, [target.get_attribute("aria-label"), target.get_attribute("title"), target.inner_text()])
        )
        submits_form = target.evaluate("el => el.type === 'submit' || el.getAttribute('type') === 'image'")
        if submits_form or SENSITIVE_ACTION.search(label):
            return self.request_user_assistance(
                f"This control may submit a form or perform a sensitive action ({label[:160]}). "
                "Review and click it yourself if intended."
            )
        target.click()
        self._save_session()
        return {"clicked": selector, "url": page.url}

    def fill(self, selector: str, value: str) -> dict[str, Any]:
        page = self._ready_page()
        if re.search(r"password|passcode|secret|token", selector, re.IGNORECASE):
            raise BrowserPilotError("Use the OS-keyring credential tool for password, passcode, secret, or token fields")
        page.locator(selector).fill(value)
        return {"filled": selector, "value_length": len(value), "url": page.url}

    def press(self, selector: str, key: str) -> dict[str, Any]:
        page = self._ready_page()
        target = page.locator(selector)
        if key.lower() == "enter" and target.evaluate("el => !!el.form"):
            return self.request_user_assistance(
                "Pressing Enter here would submit a form. Review the form and submit it yourself if intended."
            )
        target.press(key)
        self._save_session()
        return {"pressed": key, "selector": selector, "url": page.url}

    def select_option(self, selector: str, value: str) -> dict[str, Any]:
        page = self._ready_page()
        selected = page.locator(selector).select_option(value=value)
        return {"selected": selected, "selector": selector}

    def screenshot(self) -> dict[str, str]:
        page = self._ready_page()
        path = self.user_data_dir / "screenshots" / f"page-{int(time.time())}.png"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        page.screenshot(path=str(path), full_page=True)
        return {"path": str(path), "url": page.url}

    def request_user_assistance(self, reason: str) -> dict[str, Any]:
        if self._checkpoint is not None:
            return {**self._checkpoint, "status": "still_waiting_for_user"}
        page = self._ensure_browser()
        checkpoint_id = str(uuid.uuid4())
        image_path = self.user_data_dir / "screenshots" / f"handoff-{checkpoint_id}.png"
        image_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        page.screenshot(path=str(image_path), full_page=True)
        self._checkpoint = {
            "status": "waiting_for_user",
            "checkpoint_id": checkpoint_id,
            "reason": reason,
            "url": page.url,
            "screenshot": str(image_path),
            "instructions": "The browser is open for you. Complete the requested step there, then call resume_after_user.",
        }
        self._save_session()
        return self._checkpoint.copy()

    def resume_after_user(self, checkpoint_id: str) -> dict[str, Any]:
        if self._checkpoint is None:
            raise BrowserPilotError("There is no pending user assistance checkpoint")
        if checkpoint_id != self._checkpoint["checkpoint_id"]:
            raise BrowserPilotError("That checkpoint_id does not match the pending handoff")
        checkpoint = self._checkpoint
        self._checkpoint = None
        page = self._ensure_browser()
        self._save_session()
        return {"status": "resumed", "url": page.url, "title": page.title(), "completed_reason": checkpoint["reason"]}

    def credential_accounts(self) -> list[dict[str, str]]:
        return self._credentials.list()

    def fill_saved_credential(
        self, site: str, username: str, username_selector: str, password_selector: str
    ) -> dict[str, Any]:
        page = self._ready_page()
        expected_host = self._credentials.normalize_site(site)
        actual_host = (urlsplit(page.url).hostname or "").lower().rstrip(".")
        if actual_host != expected_host:
            raise BrowserPilotError(f"Credential host mismatch: browser is at {actual_host!r}, saved site is {expected_host!r}")
        if urlsplit(page.url).scheme != "https" and actual_host not in {"localhost", "127.0.0.1", "::1"}:
            raise BrowserPilotError("Saved credentials may only be filled on HTTPS pages")
        password = self._credentials.get(expected_host, username)
        page.locator(username_selector).fill(username)
        page.locator(password_selector).fill(password)
        del password
        return {
            "status": "filled_without_submitting",
            "site": expected_host,
            "username": username,
            "message": "Review the page and submit the login form yourself.",
        }

    @staticmethod
    def _workflow_path(name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}", name):
            raise ValueError("Workflow name may contain letters, numbers, spaces, underscores, and hyphens")
        return workflow_dir() / (name.replace(" ", "_") + ".json")

    def save_workflow(self, name: str, steps: list[dict[str, Any]], description: str = "") -> dict[str, Any]:
        allowed = {"navigate", "click", "fill", "fill_saved_credential", "press", "select_option", "wait_for_user"}
        fields = {
            "navigate": {"action", "url"},
            "click": {"action", "selector"},
            "fill": {"action", "selector", "value"},
            "fill_saved_credential": {"action", "site", "username", "username_selector", "password_selector"},
            "press": {"action", "selector", "key"},
            "select_option": {"action", "selector", "value"},
            "wait_for_user": {"action", "reason"},
        }
        if not steps or len(steps) > MAX_WORKFLOW_STEPS:
            raise ValueError(f"A workflow needs 1 to {MAX_WORKFLOW_STEPS} steps")
        for number, step in enumerate(steps, 1):
            if not isinstance(step, dict) or step.get("action") not in allowed:
                raise ValueError(f"Workflow step {number} has an unsupported action")
            if not set(step).issubset(fields[step["action"]]):
                raise ValueError(f"Workflow step {number} contains unsupported fields")
            if step["action"] == "navigate":
                parsed = urlsplit(str(step.get("url", "")))
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise ValueError(f"Workflow step {number} needs an http(s) URL")
            elif step["action"] == "fill":
                if "value" not in step or not step.get("selector"):
                    raise ValueError(f"Workflow step {number} needs selector and value")
                if re.search(r"password|passcode|secret|token", str(step["selector"]), re.IGNORECASE):
                    raise ValueError("Store login secrets in the OS keyring, not workflow files")
            elif step["action"] == "fill_saved_credential":
                required = {"site", "username", "username_selector", "password_selector"}
                if not required.issubset(step):
                    raise ValueError(f"Workflow step {number} is missing credential selectors or account")
            elif step["action"] in {"click", "press", "select_option"} and not step.get("selector"):
                raise ValueError(f"Workflow step {number} needs a selector")
        path = self._workflow_path(name)
        write_json(path, {"name": name, "description": description, "steps": steps})
        return {"saved": name, "step_count": len(steps), "path": str(path)}

    def list_workflows(self) -> list[dict[str, Any]]:
        summaries = []
        for path in sorted(workflow_dir().glob("*.json")):
            if path.name == "workflow-state.json":
                continue
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                summaries.append({"name": item["name"], "description": item.get("description", ""), "step_count": len(item["steps"])})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return summaries

    def run_workflow(self, name: str) -> dict[str, Any]:
        if self._checkpoint is not None:
            raise BrowserPilotError("Resume the pending user checkpoint before continuing the workflow")
        item = json.loads(self._workflow_path(name).read_text(encoding="utf-8"))
        return self._execute_workflow(item, 0)

    def resume_workflow(self) -> dict[str, Any]:
        if self._checkpoint is not None:
            raise BrowserPilotError("Call resume_after_user first")
        state = read_json(self._workflow_state_path, None)
        if not state:
            raise BrowserPilotError("There is no paused workflow to resume")
        item = json.loads(self._workflow_path(state["name"]).read_text(encoding="utf-8"))
        return self._execute_workflow(item, int(state["next_step"]))

    def _execute_workflow(self, workflow: dict[str, Any], start: int) -> dict[str, Any]:
        steps = workflow["steps"]
        for index in range(start, len(steps)):
            step = steps[index]
            action = step["action"]
            if action == "wait_for_user":
                write_json(self._workflow_state_path, {"name": workflow["name"], "next_step": index + 1}, private=True)
                handoff = self.request_user_assistance(step.get("reason", "Workflow checkpoint"))
                return {"workflow": workflow["name"], "completed_steps": index, **handoff}
            if action == "navigate":
                self.navigate(step["url"])
            elif action == "click":
                result = self.click(step["selector"])
                if result.get("status") == "waiting_for_user":
                    write_json(self._workflow_state_path, {"name": workflow["name"], "next_step": index + 1}, private=True)
                    return {"workflow": workflow["name"], "completed_steps": index, **result}
            elif action == "fill":
                self.fill(step["selector"], step["value"])
            elif action == "fill_saved_credential":
                self.fill_saved_credential(
                    step["site"], step["username"], step["username_selector"], step["password_selector"]
                )
            elif action == "press":
                self.press(step["selector"], step["key"])
            elif action == "select_option":
                self.select_option(step["selector"], step["value"])
        try:
            self._workflow_state_path.unlink()
        except FileNotFoundError:
            pass
        page = self._ensure_browser()
        return {"workflow": workflow["name"], "status": "completed", "completed_steps": len(steps), "url": page.url}

    def close(self) -> None:
        self._save_session()
        if self._context is not None:
            try:
                self._context.close()
            finally:
                self._context = None
                self._page = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
