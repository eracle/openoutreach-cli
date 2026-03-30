"""Shared onboarding prompt definitions for all OpenOutreach contexts.

Two profiles:
- PREMIUM_QUESTIONS  — full wizard including VPN location (cloud-hosted)
- SELF_HOSTED_QUESTIONS — everything except VPN (local/Docker)
"""

from __future__ import annotations

from openoutreach.vpn_locations import cities, countries
from openoutreach.wizard import (
    Autocomplete,
    Confirm,
    IntText,
    MultilineText,
    Password,
    Text,
)

# ── VPN (premium only) ───────────────────────────────────────────

VPN_COUNTRY = Autocomplete("vpn_country", "VPN country", resolver=lambda _: countries())
VPN_CITY = Autocomplete("vpn_city", "VPN city", resolver=lambda a: cities(a.get("vpn_country", "")))

# ── Campaign ─────────────────────────────────────────────────────

CAMPAIGN_NAME = Text("campaign_name", "Campaign name", default="LinkedIn Outreach")
PRODUCT_DESCRIPTION = MultilineText("product_description", "Product/service description")
CAMPAIGN_OBJECTIVE = MultilineText(
    "campaign_objective",
    "Campaign objective (e.g. 'sell analytics platform to CTOs')",
)
BOOKING_LINK = Text("booking_link", "Booking link (e.g. https://cal.com/you)", required=False)
SEED_URLS = MultilineText(
    "seed_urls", "LinkedIn seed profile URLs (one per line)", required=False,
)

# ── LinkedIn account ─────────────────────────────────────────────

LINKEDIN_EMAIL = Text("linkedin_email", "LinkedIn email")
LINKEDIN_PASSWORD = Password("linkedin_password", "LinkedIn password")

# ── LLM ──────────────────────────────────────────────────────────

LLM_API_KEY = Password("llm_api_key", "LLM API key (e.g. sk-...)")
AI_MODEL = Text("ai_model", "AI model (e.g. gpt-4o, claude-sonnet-4-5-20250929)")
LLM_API_BASE = Text(
    "llm_api_base", "LLM API base URL (e.g. https://api.openai.com/v1)", required=False,
)

# ── Preferences ──────────────────────────────────────────────────

NEWSLETTER = Confirm("newsletter", "Subscribe to OpenOutreach newsletter?", default=True)
CONNECT_DAILY = IntText("connect_daily_limit", "Connection requests daily limit", default=50)
CONNECT_WEEKLY = IntText("connect_weekly_limit", "Connection requests weekly limit", default=250)
FOLLOW_UP_DAILY = IntText("follow_up_daily_limit", "Follow-up messages daily limit", default=100)

# ── Legal ────────────────────────────────────────────────────────

LEGAL = Confirm(
    "legal_acceptance",
    "Do you accept the Legal Notice? (https://github.com/eracle/linkedin/blob/master/LEGAL_NOTICE.md)",
    default=False,
)

# ── Profiles ─────────────────────────────────────────────────────

PREMIUM_QUESTIONS = [
    VPN_COUNTRY, VPN_CITY,
    CAMPAIGN_NAME, PRODUCT_DESCRIPTION, CAMPAIGN_OBJECTIVE, BOOKING_LINK,
    SEED_URLS,
    LINKEDIN_EMAIL, LINKEDIN_PASSWORD,
    LLM_API_KEY, AI_MODEL, LLM_API_BASE,
    NEWSLETTER,
    CONNECT_DAILY, CONNECT_WEEKLY, FOLLOW_UP_DAILY,
    LEGAL,
]

SELF_HOSTED_QUESTIONS = [
    CAMPAIGN_NAME, PRODUCT_DESCRIPTION, CAMPAIGN_OBJECTIVE, BOOKING_LINK,
    SEED_URLS,
    LINKEDIN_EMAIL, LINKEDIN_PASSWORD,
    LLM_API_KEY, AI_MODEL, LLM_API_BASE,
    NEWSLETTER,
    CONNECT_DAILY, CONNECT_WEEKLY, FOLLOW_UP_DAILY,
    LEGAL,
]
