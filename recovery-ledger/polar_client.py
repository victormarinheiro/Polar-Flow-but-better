"""
Polar AccessLink API v3 client.

The method names are deliberately boring and close to the official endpoint names.
No data-shape assumptions live here; parsing belongs in database.py / sync.py.
"""
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urlencode

import requests


class PolarClient:
    BASE_URL = "https://www.polaraccesslink.com/v3"
    AUTH_URL = "https://flow.polar.com/oauth2/authorization"
    TOKEN_URL = "https://polarremote.com/v2/oauth2/token"

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
        """Exchange authorization code for access token."""
        r = requests.post(
            self.TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            headers={"Accept": "application/json"},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def register_user(self, access_token: str, polar_user_id: int) -> dict:
        """
        Required once after OAuth before user-specific data requests.
        409 means the user is already registered, which is fine for this app.
        """
        r = requests.post(
            f"{self.BASE_URL}/users",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"member-id": str(polar_user_id)},
            timeout=30,
        )
        if r.status_code == 409:
            return {"polar-user-id": polar_user_id, "already-registered": True}
        r.raise_for_status()
        return r.json() if r.content else {}

    # ── Helpers ────────────────────────────────────────────────────────────

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _get(self, token: str, path: str, params: dict | None = None):
        r = requests.get(
            f"{self.BASE_URL}{path}",
            headers=self._headers(token),
            params=params,
            timeout=30,
        )
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json() if r.content else None

    @staticmethod
    def _date_iter(from_date: date, to_date: date):
        cursor = from_date
        while cursor <= to_date:
            yield cursor
            cursor += timedelta(days=1)

    # ── Sleep ──────────────────────────────────────────────────────────────

    def get_sleep_range(self, token: str, user_id: int, from_date: date, to_date: date) -> list[dict]:
        """
        GET /v3/users/sleep returns sleep data for the last 28 days.
        For a range that extends beyond what the list endpoint returns, backfill
        missing dates with GET /v3/users/sleep/{date}.
        """
        nights_by_date: dict[str, dict] = {}

        data = self._get(token, "/users/sleep")
        for night in (data or {}).get("nights", []):
            day = night.get("date")
            if day and from_date.isoformat() <= day <= to_date.isoformat():
                nights_by_date[day] = night

        for day in self._date_iter(from_date, to_date):
            key = day.isoformat()
            if key in nights_by_date:
                continue
            try:
                night = self._get(token, f"/users/sleep/{key}")
                if night:
                    nights_by_date[key] = night
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue
                raise

        return [nights_by_date[k] for k in sorted(nights_by_date)]

    # ── Nightly Recharge ───────────────────────────────────────────────────

    def get_nightly_recharge_range(self, token: str, user_id: int, from_date: date, to_date: date) -> list[dict]:
        """
        GET /v3/users/nightly-recharge returns the last 28 days.
        Backfill missing dates with GET /v3/users/nightly-recharge/{date}.
        """
        recharges_by_date: dict[str, dict] = {}

        data = self._get(token, "/users/nightly-recharge")
        for rec in (data or {}).get("recharges", []):
            day = rec.get("date")
            if day and from_date.isoformat() <= day <= to_date.isoformat():
                recharges_by_date[day] = rec

        for day in self._date_iter(from_date, to_date):
            key = day.isoformat()
            if key in recharges_by_date:
                continue
            try:
                rec = self._get(token, f"/users/nightly-recharge/{key}")
                if rec:
                    recharges_by_date[key] = rec
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue
                raise

        return [recharges_by_date[k] for k in sorted(recharges_by_date)]

    def get_nightly_recharge(self, token: str, user_id: int, day: date) -> Optional[dict]:
        try:
            return self._get(token, f"/users/nightly-recharge/{day.isoformat()}")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    # ── Daily Activity ─────────────────────────────────────────────────────

    def get_activity_range(self, token: str, user_id: int, from_date: date, to_date: date) -> list[dict]:
        """
        GET /v3/users/activities/{date} returns the daily activity summary,
        including steps and calories. Date cannot be older than 365 days.
        """
        activities: list[dict] = []
        for day in self._date_iter(from_date, to_date):
            try:
                act = self._get(token, f"/users/activities/{day.isoformat()}")
                if act:
                    act.setdefault("date", day.isoformat())
                    activities.append(act)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (400, 404):
                    continue
                raise
        return activities

    def get_activity_samples_range(self, token: str, from_date: date, to_date: date) -> list[dict]:
        """GET /v3/users/activities/samples with a date range."""
        data = self._get(
            token,
            "/users/activities/samples",
            params={"from": from_date.isoformat(), "to": to_date.isoformat()},
        )
        return data or []

    # Deprecated transaction model kept only for backwards debugging.
    def get_activity_transaction(self, token: str, user_id: int) -> Optional[list[dict]]:
        r = requests.post(
            f"{self.BASE_URL}/users/{user_id}/activity-transactions",
            headers=self._headers(token),
            timeout=30,
        )
        if r.status_code == 204:
            return None
        r.raise_for_status()

        transaction_url = r.headers.get("Location")
        if not transaction_url:
            return None

        r2 = requests.get(transaction_url, headers=self._headers(token), timeout=30)
        r2.raise_for_status()
        activity_links = r2.json().get("activity-log", [])

        activities = []
        for url in activity_links:
            ra = requests.get(url, headers=self._headers(token), timeout=30)
            if ra.status_code == 200:
                activities.append(ra.json())

        r_commit = requests.put(transaction_url, headers=self._headers(token), timeout=30)
        r_commit.raise_for_status()
        return activities

    # ── Continuous Heart Rate ──────────────────────────────────────────────

    def get_continuous_hr(self, token: str, user_id: int, day: date) -> Optional[dict]:
        try:
            return self._get(token, f"/users/continuous-heart-rate/{day.isoformat()}")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    def get_continuous_hr_range(self, token: str, from_date: date, to_date: date) -> list[dict]:
        data = self._get(
            token,
            "/users/continuous-heart-rate",
            params={"from": from_date.isoformat(), "to": to_date.isoformat()},
        )
        if data is None:
            return []
        return data if isinstance(data, list) else [data]

    # ── Cardio Load / Strain ───────────────────────────────────────────────

    def get_cardio_load_range(self, token: str, from_date: date, to_date: date) -> list[dict]:
        """GET /v3/users/cardio-load/date?from=YYYY-MM-DD&to=YYYY-MM-DD."""
        data = self._get(
            token,
            "/users/cardio-load/date",
            params={"from": from_date.isoformat(), "to": to_date.isoformat()},
        )
        if data is None:
            return []
        return data if isinstance(data, list) else [data]

    # ── User / physical info ───────────────────────────────────────────────

    def get_user_info(self, token: str, user_id: int) -> dict:
        return self._get(token, f"/users/{user_id}")

    def get_physical_info(self, token: str) -> Optional[dict]:
        try:
            return self._get(token, "/users/physical-info")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise
