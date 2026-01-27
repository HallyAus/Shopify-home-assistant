"""OAuth2 Client Credentials implementation for Shopify."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import OAUTH2_TOKEN_URL_TEMPLATE

_LOGGER = logging.getLogger(__name__)

# Token refresh buffer - refresh 5 minutes before expiry
TOKEN_REFRESH_BUFFER = timedelta(minutes=5)


class ShopifyAuthError(Exception):
    """Base exception for Shopify authentication errors."""


class ShopifyTokenError(ShopifyAuthError):
    """Token request error."""


@dataclass
class TokenData:
    """Token data from Shopify."""

    access_token: str
    expires_at: datetime
    scope: str

    @property
    def is_expired(self) -> bool:
        """Check if token is expired or will expire soon."""
        return datetime.now() >= (self.expires_at - TOKEN_REFRESH_BUFFER)

    @property
    def masked_token(self) -> str:
        """Return masked token for logging (last 4 chars only)."""
        if len(self.access_token) > 4:
            return f"...{self.access_token[-4:]}"
        return "****"


def normalize_shop_domain(domain: str) -> str:
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


async def async_get_client_credentials_token(
    hass: HomeAssistant,
    shop_domain: str,
    client_id: str,
    client_secret: str,
) -> TokenData:
    """Get access token using client credentials grant.

    This is the server-to-server OAuth flow for Shopify custom apps.
    Tokens expire after ~24 hours (86399 seconds).

    Args:
        hass: Home Assistant instance
        shop_domain: The shop domain (e.g., my-store.myshopify.com)
        client_id: The app client ID
        client_secret: The app client secret

    Returns:
        TokenData with access_token, expires_at, and scope

    Raises:
        ShopifyTokenError: If token request fails
    """
    shop = normalize_shop_domain(shop_domain)
    token_url = OAUTH2_TOKEN_URL_TEMPLATE.format(shop=shop)

    # Client credentials grant uses form-urlencoded body
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }

    session = async_get_clientsession(hass)

    try:
        async with session.post(
            token_url,
            data=payload,  # form-urlencoded
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            response_text = await response.text()

            if response.status == 400:
                _LOGGER.error(
                    "Token request failed (400): %s",
                    response_text[:200],
                )
                raise ShopifyTokenError(
                    "Invalid request - check client_id and client_secret"
                )

            if response.status == 401:
                _LOGGER.error("Token request failed (401): Invalid credentials")
                raise ShopifyTokenError(
                    "Invalid client credentials - verify client_id and client_secret"
                )

            if response.status == 403:
                _LOGGER.error(
                    "Token request failed (403): %s",
                    response_text[:200],
                )
                raise ShopifyTokenError(
                    "Access forbidden - ensure app is installed on the store"
                )

            if response.status != 200:
                _LOGGER.error(
                    "Token request failed (%d): %s",
                    response.status,
                    response_text[:200],
                )
                raise ShopifyTokenError(
                    f"Token request failed with status {response.status}"
                )

            try:
                data = await response.json()
            except Exception as err:
                _LOGGER.error("Failed to parse token response: %s", err)
                raise ShopifyTokenError("Invalid JSON response from Shopify") from err

            access_token = data.get("access_token")
            if not access_token:
                _LOGGER.error("No access_token in response: %s", list(data.keys()))
                raise ShopifyTokenError("No access token in response")

            # expires_in is in seconds (typically 86399 = ~24 hours)
            expires_in = data.get("expires_in", 86399)
            expires_at = datetime.now() + timedelta(seconds=expires_in)

            scope = data.get("scope", "")

            token_data = TokenData(
                access_token=access_token,
                expires_at=expires_at,
                scope=scope,
            )

            _LOGGER.info(
                "Successfully obtained token for %s (expires in %d seconds, scope: %s)",
                shop,
                expires_in,
                scope,
            )

            return token_data

    except aiohttp.ClientError as err:
        _LOGGER.error("Network error during token request: %s", err)
        raise ShopifyTokenError(f"Network error: {err}") from err


class ShopifyTokenManager:
    """Manage Shopify OAuth tokens with automatic refresh.

    Tokens are obtained using the client credentials grant and expire
    after ~24 hours. This manager handles automatic refresh.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        shop_domain: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        """Initialize the token manager."""
        self._hass = hass
        self._shop_domain = normalize_shop_domain(shop_domain)
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_data: TokenData | None = None

    @property
    def shop_domain(self) -> str:
        """Return the shop domain."""
        return self._shop_domain

    @property
    def has_valid_token(self) -> bool:
        """Check if we have a valid (non-expired) token."""
        return self._token_data is not None and not self._token_data.is_expired

    @property
    def access_token(self) -> str | None:
        """Return the current access token if valid."""
        if self._token_data and not self._token_data.is_expired:
            return self._token_data.access_token
        return None

    @property
    def token_expires_at(self) -> datetime | None:
        """Return token expiration time."""
        return self._token_data.expires_at if self._token_data else None

    def set_token(self, access_token: str, expires_at: datetime, scope: str = "") -> None:
        """Set token from stored data (e.g., from config entry)."""
        self._token_data = TokenData(
            access_token=access_token,
            expires_at=expires_at,
            scope=scope,
        )
        _LOGGER.debug(
            "Loaded stored token for %s (expires: %s)",
            self._shop_domain,
            expires_at.isoformat(),
        )

    async def async_get_token(self) -> str:
        """Get a valid access token, refreshing if necessary.

        Returns:
            Valid access token

        Raises:
            ShopifyTokenError: If unable to get a valid token
        """
        if self._token_data is None or self._token_data.is_expired:
            _LOGGER.debug(
                "Token missing or expired for %s, refreshing...",
                self._shop_domain,
            )
            await self.async_refresh_token()

        return self._token_data.access_token

    async def async_refresh_token(self) -> TokenData:
        """Refresh the access token using client credentials grant.

        Returns:
            New TokenData

        Raises:
            ShopifyTokenError: If refresh fails
        """
        _LOGGER.debug("Refreshing token for %s", self._shop_domain)

        self._token_data = await async_get_client_credentials_token(
            hass=self._hass,
            shop_domain=self._shop_domain,
            client_id=self._client_id,
            client_secret=self._client_secret,
        )

        _LOGGER.info(
            "Token refreshed for %s (expires: %s)",
            self._shop_domain,
            self._token_data.expires_at.isoformat(),
        )

        return self._token_data

    def get_token_info(self) -> dict[str, Any]:
        """Get token info for diagnostics (with masked token)."""
        if self._token_data:
            return {
                "has_token": True,
                "token_last4": self._token_data.masked_token,
                "expires_at": self._token_data.expires_at.isoformat(),
                "is_expired": self._token_data.is_expired,
                "scope": self._token_data.scope,
            }
        return {
            "has_token": False,
        }
