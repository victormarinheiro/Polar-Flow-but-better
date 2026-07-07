"""
Polar AccessLink API v3 client.
Handles OAuth, user registration, and all data fetching.
"""
import requests
from urllib.parse import urlencode
from datetime import date, timedelta
from typing import Optional


class PolarClient:
    BASE_URL = "https://www.polaraccesslink.com/v3"
    AUTH_URL = "https://auth.polar.com/oauth/authorize"
    TOKEN_URL = "https://auth.polar.com/oauth/token"

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    # ── OAuth ──────────────────────────────────────────────────────────────

    def get_auth_url(self) -> str:
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": "accesslink.read_all",
            "redirect_uri": self.redirect_uri,
        }
        return f"{self.AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        """Exchange auth code for access token. Returns token dict with x_user_id."""
        r = requests.post(
            self.TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
        )
        r.raise_for_status()
        return r.json()

    def register_user(self, access_token: str, polar_user_id: int) -> dict:
        """
        Required step after OAuth — must be called once before any data requests.
        409 = already registered, which is fine.
        """
        r = requests.post(
            f"{self.BASE_URL}/users",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"member-id": str(polar_user_id)},
        )
        if r.status_code == 409:
            return {"polar-user-id": polar_user_id, "already-registered": True}
        r.raise_for_status()
        return r.json()

    # ── Helpers ────────────────────────────────────────────────────────────

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _get(self, token: str, path: str, params: dict = None):
        r = requests.get(
            f"{self.BASE_URL}{path}",
            headers=self._headers(token),
            params=params,
        )
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    # ── Sleep ──────────────────────────────────────────────────────────────

    def get_sleep_range(self, token: str, user_id: int, from_date: date, to_date: date) -> list:
        """
        GET /v3/users/{userId}/sleep?from=YYYY-MM-DD&to=YYYY-MM-DD
        Returns list of sleep night objects.
        """
        data = self._get(
            token,
            f"/users/{user_id}/sleep",
            params={"from": from_date.isoformat(), "to": to_date.isoformat()},
        )
        if data is None:
            return []
        return data.get("nights", [])

    # ── Nightly Recharge ───────────────────────────────────────────────────

    def get_nightly_recharge(self, token: str, user_id: int, day: date) -> Optional[dict]:
        """
        GET /v3/users/{userId}/nightly-recharge/{date}
        Returns ANS/recovery metrics for a single night.
        """
        try:
            return self._get(token, f"/users/{user_id}/nightly-recharge/{day.isoformat()}")
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    # ── Activity (transaction model) ───────────────────────────────────────

    def get_activity_transaction(self, token: str, user_id: int) -> Optional[dict]:
        """
        Creates a new activity transaction and returns all available activity summaries.
        Automatically commits the transaction after fetching.
        Returns list of activity dicts, or None if no new data.
        """
        # 1. Create transaction
        r = requests.post(
            f"{self.BASE_URL}/users/{user_id}/activity-transactions",
            headers=self._headers(token),
        )
        if r.status_code == 204:
            return None  # No new data
        r.raise_for_status()

        transaction_url = r.headers.get("Location")
        transaction_id = transaction_url.rstrip("/").split("/")[-1]

        # 2. List available activities in transaction
        r2 = requests.get(transaction_url, headers=self._headers(token))
        r2.raise_for_status()
        activity_links = r2.json().get("activity-log", [])

        # 3. Fetch each activity
        activities = []
        for url in activity_links:
            ra = requests.get(url, headers=self._headers(token))
            if ra.status_code == 200:
                activities.append(ra.json())

        # 4. Commit transaction (marks data as delivered)
        r_commit = requests.put(transaction_url, headers=self._headers(token))
        r_commit.raise_for_status()

        return activities

    # ── Continuous Heart Rate ──────────────────────────────────────────────

    def get_continuous_hr(self, token: str, user_id: int, day: date) -> Optional[dict]:
        """
        GET /v3/users/{userId}/continuous-heart-rate/{date}
        Returns 24/7 HR samples for a day.
        """
        try:
            return self._get(token, f"/users/{user_id}/continuous-heart-rate/{day.isoformat()}")
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    # ── User info ──────────────────────────────────────────────────────────

    def get_user_info(self, token: str, user_id: int) -> dict:
        return self._get(token, f"/users/{user_id}")
