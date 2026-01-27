"""OAuth2 implementation for Shopify."""
from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any
from urllib.parse import urlencode

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    OAUTH2_AUTHORIZE_URL_TEMPLATE,
    OAUTH2_SCOPES,
    OAUTH2_TOKEN_URL_TEMPLATE,
)

_LOGGER = logging.getLogger(__name__)


class ShopifyOAuth2Error(Exception):
    """Base exception for Shopify OAuth2 errors."""


class ShopifyOAuth2AuthorizationError(ShopifyOAuth2Error):
    """Authorization error."""


class ShopifyOAuth2TokenError(ShopifyOAuth2Error):
    """Token exchange error."""


def generate_state() -> str:
    """Generate a secure random state parameter."""
    return secrets.token_urlsafe(32)


def generate_nonce() -> str:
    """Generate a secure random nonce."""
    return secrets.token_urlsafe(16)


def build_authorization_url(
    shop_domain: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: list[str] | None = None,
) -> str:
    """Build the Shopify OAuth2 authorization URL.

    Args:
        shop_domain: The shop domain (e.g., my-store.myshopify.com)
        client_id: The Shopify app client ID
        redirect_uri: The callback URL for authorization
        state: A random state string for CSRF protection
        scopes: List of OAuth scopes (defaults to read_orders)

    Returns:
        The full authorization URL
    """
    if scopes is None:
        scopes = OAUTH2_SCOPES

    # Normalize shop domain
    shop = _normalize_shop_domain(shop_domain)

    base_url = OAUTH2_AUTHORIZE_URL_TEMPLATE.format(shop=shop)

    params = {
        "client_id": client_id,
        "scope": ",".join(scopes),
        "redirect_uri": redirect_uri,
        "state": state,
        "grant_options[]": "per-user",  # Request online access token
    }

    return f"{base_url}?{urlencode(params)}"


def _normalize_shop_domain(domain: str) -> str:
    """Normalize the shop domain to my-store.myshopify.com format."""
    domain = domain.strip().lower()

    # Remove protocol if present
    if domain.startswith("https://"):
        domain = domain[8:]
    elif domain.startswith("http://"):
        domain = domain[7:]

    # Remove trailing slash
    domain = domain.rstrip("/")

    # Add .myshopify.com if not present
    if not domain.endswith(".myshopify.com"):
        if ".myshopify.com" not in domain:
            domain = f"{domain}.myshopify.com"

    return domain


def verify_hmac(query_params: dict[str, str], client_secret: str) -> bool:
    """Verify the HMAC signature from Shopify callback.

    Args:
        query_params: The query parameters from the callback
        client_secret: The app's client secret

    Returns:
        True if the HMAC is valid, False otherwise
    """
    hmac_value = query_params.get("hmac")
    if not hmac_value:
        return False

    # Create a copy without hmac for verification
    params_to_verify = {k: v for k, v in query_params.items() if k != "hmac"}

    # Sort and create message string
    sorted_params = sorted(params_to_verify.items())
    message = "&".join(f"{k}={v}" for k, v in sorted_params)

    # Calculate HMAC
    calculated_hmac = hashlib.sha256(
        client_secret.encode("utf-8")
    )
    calculated_hmac.update(message.encode("utf-8"))

    # Use hmac.compare_digest for timing-safe comparison
    import hmac as hmac_module
    return hmac_module.compare_digest(
        hmac_value,
        calculated_hmac.hexdigest()
    )


async def exchange_code_for_token(
    hass: HomeAssistant,
    shop_domain: str,
    client_id: str,
    client_secret: str,
    code: str,
) -> dict[str, Any]:
    """Exchange authorization code for access token.

    Args:
        hass: Home Assistant instance
        shop_domain: The shop domain
        client_id: The app client ID
        client_secret: The app client secret
        code: The authorization code from Shopify

    Returns:
        Dict containing access_token and scope

    Raises:
        ShopifyOAuth2TokenError: If token exchange fails
    """
    shop = _normalize_shop_domain(shop_domain)
    token_url = OAUTH2_TOKEN_URL_TEMPLATE.format(shop=shop)

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
    }

    session = async_get_clientsession(hass)

    try:
        async with session.post(
            token_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status == 400:
                error_data = await response.json()
                error_msg = error_data.get("error_description", error_data.get("error", "Unknown error"))
                raise ShopifyOAuth2TokenError(f"Token exchange failed: {error_msg}")

            if response.status == 401:
                raise ShopifyOAuth2TokenError("Invalid client credentials")

            if response.status != 200:
                text = await response.text()
                raise ShopifyOAuth2TokenError(
                    f"Token exchange failed with status {response.status}: {text}"
                )

            data = await response.json()

            if "access_token" not in data:
                raise ShopifyOAuth2TokenError("No access token in response")

            _LOGGER.debug(
                "Successfully obtained access token for shop %s with scopes: %s",
                shop,
                data.get("scope", "unknown"),
            )

            return {
                "access_token": data["access_token"],
                "scope": data.get("scope", ""),
                "shop_domain": shop,
            }

    except aiohttp.ClientError as err:
        raise ShopifyOAuth2TokenError(f"Network error during token exchange: {err}") from err


class ShopifyOAuth2Session:
    """Manage OAuth2 session for a Shopify store."""

    def __init__(
        self,
        hass: HomeAssistant,
        shop_domain: str,
        access_token: str,
    ) -> None:
        """Initialize the OAuth2 session."""
        self._hass = hass
        self._shop_domain = _normalize_shop_domain(shop_domain)
        self._access_token = access_token

    @property
    def shop_domain(self) -> str:
        """Return the shop domain."""
        return self._shop_domain

    @property
    def access_token(self) -> str:
        """Return the access token."""
        return self._access_token

    async def async_ensure_token_valid(self) -> None:
        """Ensure the token is valid.

        Note: Shopify offline access tokens don't expire, so this is a no-op.
        For online access tokens, you would need to handle refresh here.
        """
        # Shopify offline access tokens don't expire
        # If we're using online tokens in the future, implement refresh here
        pass
