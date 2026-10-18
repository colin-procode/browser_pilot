from __future__ import annotations

from urllib.parse import urlsplit

import keyring

from .storage import data_home, read_json, write_json

SERVICE_PREFIX = "browser-pilot/site/"


class CredentialStore:
    """Store website passwords in the system keyring, never in project files."""

    @staticmethod
    def _require_secure_backend() -> None:
        backend = keyring.get_keyring()
        backend_name = f"{backend.__class__.__module__}.{backend.__class__.__name__}".lower()
        if getattr(backend, "priority", 0) <= 0 or "plaintext" in backend_name or "keyring.backends.file" in backend_name:
            raise RuntimeError(
                "No secure OS keyring is available. Configure a system keyring before saving credentials."
            )

    @staticmethod
    def normalize_site(site: str) -> str:
        parsed = urlsplit(site if "://" in site else "https://" + site)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Site must be an http(s) URL or hostname")
        if parsed.username or parsed.password:
            raise ValueError("Do not include credentials in the site URL")
        if parsed.scheme == "http" and parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Save credentials only for HTTPS sites (HTTP is allowed for localhost)")
        return parsed.hostname.lower().rstrip(".")

    @staticmethod
    def _index_path():
        return data_home() / "credentials-index.json"

    def save(self, site: str, username: str, password: str) -> None:
        self._require_secure_backend()
        host = self.normalize_site(site)
        if not username or not password:
            raise ValueError("Username and password must not be empty")
        keyring.set_password(SERVICE_PREFIX + host, username, password)
        index = read_json(self._index_path(), [])
        entry = {"site": host, "username": username}
        if entry not in index:
            index.append(entry)
        write_json(self._index_path(), index, private=True)

    def list(self) -> list[dict[str, str]]:
        return read_json(self._index_path(), [])

    def get(self, site: str, username: str) -> str:
        self._require_secure_backend()
        host = self.normalize_site(site)
        password = keyring.get_password(SERVICE_PREFIX + host, username)
        if password is None:
            raise KeyError(f"No saved credential for {username!r} at {host}")
        return password

    def delete(self, site: str, username: str) -> None:
        self._require_secure_backend()
        host = self.normalize_site(site)
        try:
            keyring.delete_password(SERVICE_PREFIX + host, username)
        except keyring.errors.PasswordDeleteError:
            pass
        index = [entry for entry in self.list() if not (entry["site"] == host and entry["username"] == username)]
        write_json(self._index_path(), index, private=True)
