"""VPN server locations fetched from the hub API."""

from functools import cached_property

import httpx

from openoutreach.config import hub_url


class _Locations:
    """Fetch once from hub, sort and cache for the process lifetime."""

    @cached_property
    def _data(self) -> dict[str, list[str]]:
        url = f"{hub_url().rstrip('/')}/api/vpn/locations/"
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        return {
            c["name"]: sorted(c.get("cities", []))
            for c in sorted(resp.json()["countries"], key=lambda c: c["name"])
        }

    def countries(self) -> list[str]:
        return list(self._data.keys())

    def cities(self, country: str) -> list[str]:
        return self._data.get(country, [])


_locations = _Locations()
countries = _locations.countries
cities = _locations.cities
