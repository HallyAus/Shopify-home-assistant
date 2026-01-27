"""Shopify Store Integration for Home Assistant.

Uses OAuth 2.0 Authorization Code flow for authentication.
Shopify access tokens are long-lived and do not require refresh.

The OAuth callback is handled by HA's built-in /auth/external/callback endpoint.
This integration registers the OAuth2AuthorizeCallbackView to ensure it's available.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any

from aiohttp import web
import jwt
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ShopifyAuthError,
    ShopifyConnectionError,
    ShopifyGraphQLClient,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_API_VERSION,
    CONF_INCLUDE_TEST_ORDERS,
    CONF_MOCK_MODE,
    CONF_SHOP_DOMAIN,
    CONF_TIMEZONE_OVERRIDE,
    DEFAULT_API_VERSION,
    DEFAULT_INCLUDE_TEST_ORDERS,
    DEFAULT_MOCK_MODE,
    DOMAIN,
)
from .coordinator import ShopifyDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# OAuth2 callback view registration
DATA_JWT_SECRET = "oauth2_jwt_secret"
DATA_SHOPIFY_VIEW_REGISTERED = f"{DOMAIN}_oauth_view_registered"
AUTH_CALLBACK_PATH = "/auth/external/callback"


def _decode_jwt(hass: HomeAssistant, encoded: str) -> dict[str, Any] | None:
    """Decode a JWT token using HA's shared secret."""
    secret = hass.data.get(DATA_JWT_SECRET)
    if secret is None:
        return None
    try:
        return jwt.decode(encoded, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None


class ShopifyOAuth2CallbackView(HomeAssistantView):
    """Handle OAuth2 authorization callbacks for Shopify.

    This view handles the callback at /auth/external/callback and routes
    the authorization code back to the config flow.

    It's compatible with HA's built-in OAuth2 callback system.
    """

    url = AUTH_CALLBACK_PATH
    name = "shopify_ha:oauth2:callback"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Handle the OAuth callback."""
        hass: HomeAssistant = request.app["hass"]

        # Log incoming callback for debugging
        _LOGGER.info(
            "=== SHOPIFY OAUTH CALLBACK ===\n"
            "  Host header: %s\n"
            "  Scheme: %s\n"
            "  Path: %s\n"
            "  Query keys: %s\n"
            "==============================",
            request.headers.get("Host", "not set"),
            request.headers.get("X-Forwarded-Proto", request.scheme),
            request.path,
            list(request.query.keys()),
        )

        if "state" not in request.query:
            _LOGGER.error("OAuth callback missing state parameter")
            return web.Response(status=400, text="Missing state parameter")

        # Decode the JWT state
        state = _decode_jwt(hass, request.query["state"])

        if state is None:
            _LOGGER.error(
                "OAuth callback: Failed to decode JWT state. "
                "This usually means the callback went to a different HA instance "
                "or the JWT secret changed."
            )
            return web.Response(
                status=400,
                text="Invalid state. Is My Home Assistant configured to go to the right instance?",
            )

        flow_id = state.get("flow_id")
        if not flow_id:
            _LOGGER.error("OAuth callback: No flow_id in decoded state")
            return web.Response(status=400, text="Invalid state: missing flow_id")

        _LOGGER.info(
            "OAuth callback: Decoded state, flow_id=%s, redirect_uri=%s",
            flow_id,
            state.get("redirect_uri", "not set"),
        )

        # Build the user_input to pass to the flow
        user_input: dict[str, Any] = {"state": state}

        if "code" in request.query:
            user_input["code"] = request.query["code"]
            _LOGGER.debug("OAuth callback: Got authorization code")
        elif "error" in request.query:
            user_input["error"] = request.query["error"]
            _LOGGER.error(
                "OAuth callback: Got error from Shopify: %s",
                request.query.get("error_description", request.query["error"]),
            )

        # Include HMAC and other Shopify params for verification
        for key in ["hmac", "shop", "timestamp", "host"]:
            if key in request.query:
                user_input[key] = request.query[key]

        # Continue the config flow
        try:
            result = await hass.config_entries.flow.async_configure(
                flow_id, user_input
            )
            _LOGGER.info(
                "OAuth callback: Flow continued, result type=%s",
                result.get("type"),
            )
        except UnknownFlow:
            _LOGGER.error(
                "OAuth callback: Unknown flow %s. Flow may have expired or "
                "this callback went to a different HA instance.",
                flow_id,
            )
            return web.Response(
                status=400,
                text="Unknown flow. The setup may have timed out. Please try again.",
            )

        # Return a page that closes the window
        return web.Response(
            text="""
            <html>
            <head><title>Authorization Complete</title></head>
            <body>
                <h1>Authorization Complete</h1>
                <p>You can close this window and return to Home Assistant.</p>
                <script>window.close();</script>
            </body>
            </html>
            """,
            content_type="text/html",
        )


def _ensure_oauth_view_registered(hass: HomeAssistant) -> None:
    """Ensure the OAuth2 callback view is registered.

    This registers our callback view which handles /auth/external/callback.
    We check if it's already registered to avoid duplicates.
    """
    if hass.data.get(DATA_SHOPIFY_VIEW_REGISTERED):
        return

    # Also ensure the JWT secret exists
    if DATA_JWT_SECRET not in hass.data:
        hass.data[DATA_JWT_SECRET] = secrets.token_hex()

    # Register our callback view
    hass.http.register_view(ShopifyOAuth2CallbackView())
    hass.data[DATA_SHOPIFY_VIEW_REGISTERED] = True
    _LOGGER.info("Registered Shopify OAuth callback view at %s", AUTH_CALLBACK_PATH)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Shopify component.

    This registers the OAuth callback view early so it's available
    during config flow.
    """
    _ensure_oauth_view_registered(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Shopify from a config entry."""
    # Ensure OAuth view is registered (in case async_setup wasn't called)
    _ensure_oauth_view_registered(hass)

    hass.data.setdefault(DOMAIN, {})

    # Get configuration
    shop_domain = entry.data[CONF_SHOP_DOMAIN]
    access_token = entry.data[CONF_ACCESS_TOKEN]
    api_version = entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    timezone_override = entry.data.get(CONF_TIMEZONE_OVERRIDE)
    include_test = entry.data.get(CONF_INCLUDE_TEST_ORDERS, DEFAULT_INCLUDE_TEST_ORDERS)
    mock_mode = entry.data.get(CONF_MOCK_MODE, DEFAULT_MOCK_MODE)

    # Validate we have an access token
    if not access_token:
        _LOGGER.error("No access token found for %s", shop_domain)
        raise ConfigEntryAuthFailed(
            f"No access token for {shop_domain}. Please re-authenticate."
        )

    # Create API client
    session = async_get_clientsession(hass)
    client = ShopifyGraphQLClient(
        shop_domain=shop_domain,
        access_token=access_token,
        api_version=api_version,
        session=session,
        timezone_override=timezone_override or None,
        include_test_orders=include_test,
        mock_mode=mock_mode,
    )

    # Test connection
    try:
        await client.test_connection()
    except ShopifyAuthError as err:
        _LOGGER.error("Authentication failed for %s: %s", shop_domain, err)
        # Trigger reauth flow
        raise ConfigEntryAuthFailed(
            f"Authentication failed for {shop_domain}. "
            "The access token may have been revoked. Please re-authenticate."
        ) from err
    except ShopifyConnectionError as err:
        _LOGGER.error("Connection failed for %s: %s", shop_domain, err)
        raise ConfigEntryNotReady(
            f"Unable to connect to Shopify store {shop_domain}. "
            "Please verify the shop domain and try again."
        ) from err
    except Exception as err:
        _LOGGER.exception("Unexpected error connecting to %s", shop_domain)
        raise ConfigEntryNotReady(
            f"Unexpected error connecting to {shop_domain}: {err}"
        ) from err

    # Create coordinator (no token manager needed - tokens are long-lived)
    coordinator = ShopifyDataUpdateCoordinator(
        hass=hass,
        client=client,
        config_entry=entry,
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator and client
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register options update listener
    entry.async_on_unload(entry.add_update_listener(async_options_updated))

    _LOGGER.info("Shopify integration set up for %s", shop_domain)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator: ShopifyDataUpdateCoordinator = data["coordinator"]
        await coordinator.async_shutdown()
        _LOGGER.info(
            "Shopify integration unloaded for %s",
            entry.data.get(CONF_SHOP_DOMAIN),
        )

    return unload_ok


async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    coordinator: ShopifyDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    coordinator.update_options(entry.options)

    # Trigger refresh with new options
    await coordinator.async_request_refresh()
    _LOGGER.debug(
        "Options updated for %s, triggering refresh",
        entry.data.get(CONF_SHOP_DOMAIN),
    )


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug(
        "Migrating configuration from version %s.%s",
        config_entry.version,
        config_entry.minor_version,
    )

    if config_entry.version > 1:
        # This means the user has downgraded from a future version
        return False

    # Currently at version 1, no migration needed
    return True
