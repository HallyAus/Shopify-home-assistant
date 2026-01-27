"""OAuth2 Authorization Code flow implementation for Shopify.

This module implements standard OAuth 2.0 Authorization Code flow for Shopify.
Shopify access tokens are long-lived and do not require refresh tokens.

Flow:
1. User is redirected to Shopify's authorization endpoint
2. User approves the app
3. Shopify redirects back with authorization code and HMAC
4. We verify HMAC and exchange code for access token
5. Access token is stored in config entry

IMPORTANT: The config_flow uses HA's flow_id as the OAuth state parameter.
This ensures Home Assistant can match the callback to the correct flow.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any
from urllib.parse import urlencode

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    OAUTH2_TOKEN_URL_TEMPLATE,
)

_LOGGER = logging.getLogger(__name__)


class ShopifyOAuthError(Exception):
    """Base exception for Shopify OAuth errors."""


class ShopifyHMACError(ShopifyOAuthError):
    """HMAC verification failed."""


class ShopifyTokenError(ShopifyOAuthError):
    """Token exchange error."""


def normalize_shop_domain(domain: str) -> str:
    """Normalize the shop domain to my-store.myshopify.com format.

    Args:
        domain: Shop domain in various formats (my-store, my-store.myshopify.com, etc.)

    Returns:
        Normalized domain (my-store.myshopify.com)
    """
    domain = domain.strip().lower()

    # Remove protocol if present
    if domain.startswith("https://"):
        domain = domain[8:]
    elif domain.startswith("http://"):
        domain = domain[7:]

    # Remove trailing slash and path
    domain = domain.rstrip("/").split("/")[0]

    # Add .myshopify.com if not present
    if not domain.endswith(".myshopify.com"):
        if ".myshopify.com" not in domain:
            domain = f"{domain}.myshopify.com"

    return domain


def verify_hmac(
    query_params: dict[str, str],
    client_secret: str,
) -> bool:
    """Verify the HMAC signature from Shopify callback.

    Shopify signs the callback parameters with the client secret.
    We must verify this to ensure the callback is authentic.

    Args:
        query_params: The query parameters from the callback URL
        client_secret: The app's client secret

    Returns:
        True if HMAC is valid, False otherwise
    """
    received_hmac = query_params.get("hmac")
    if not received_hmac:
        _LOGGER.error("No HMAC in callback parameters")
        return False

    # Build the message to verify (all params except hmac, sorted alphabetically)
    params_to_sign = {
        k: v for k, v in query_params.items()
        if k != "hmac"
    }

    # Sort and encode
    message = urlencode(sorted(params_to_sign.items()))

    # Calculate expected HMAC
    expected_hmac = hmac.new(
        client_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Constant-time comparison to prevent timing attacks
    is_valid = hmac.compare_digest(expected_hmac, received_hmac)

    if not is_valid:
        _LOGGER.error("HMAC verification failed")

    return is_valid


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
        client_id: The app's client ID
        client_secret: The app's client secret
        code: The authorization code from callback

    Returns:
        Dict containing access_token and scope

    Raises:
        ShopifyTokenError: If token exchange fails
    """
    shop = normalize_shop_domain(shop_domain)
    token_url = OAUTH2_TOKEN_URL_TEMPLATE.format(shop=shop)

    # Token exchange uses JSON body (not form-encoded)
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
            response_text = await response.text()

            if response.status == 400:
                _LOGGER.error(
                    "Token exchange failed (400): %s",
                    response_text[:200],
                )
                raise ShopifyTokenError(
                    "Invalid request - authorization code may be expired or invalid"
                )

            if response.status == 401:
                _LOGGER.error("Token exchange failed (401): Invalid credentials")
                raise ShopifyTokenError(
                    "Invalid client credentials"
                )

            if response.status == 403:
                _LOGGER.error(
                    "Token exchange failed (403): %s",
                    response_text[:200],
                )
                raise ShopifyTokenError(
                    "Access forbidden - check app permissions"
                )

            if response.status != 200:
                _LOGGER.error(
                    "Token exchange failed (%d): %s",
                    response.status,
                    response_text[:200],
                )
                raise ShopifyTokenError(
                    f"Token exchange failed with status {response.status}"
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

            scope = data.get("scope", "")

            _LOGGER.info(
                "Successfully obtained access token for %s (scope: %s)",
                shop,
                scope,
            )

            return {
                "access_token": access_token,
                "scope": scope,
            }

    except aiohttp.ClientError as err:
        _LOGGER.error("Network error during token exchange: %s", err)
        raise ShopifyTokenError(f"Network error: {err}") from err


def mask_token(token: str) -> str:
    """Mask an access token for safe logging/display.

    Shows only the last 4 characters.

    Args:
        token: The token to mask

    Returns:
        Masked token string (e.g., "...abc1")
    """
    if not token:
        return "****"
    if len(token) > 4:
        return f"...{token[-4:]}"
    return "****"


def mask_secret(secret: str) -> str:
    """Mask a client secret for safe logging/display.

    Shows only the last 4 characters.

    Args:
        secret: The secret to mask

    Returns:
        Masked secret string
    """
    return mask_token(secret)
