"""Shopify Store Integration for Home Assistant."""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, Platform
from homeassistant.core import HomeAssistant
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
    CONF_TOKEN_EXPIRES_AT,
    DEFAULT_API_VERSION,
    DEFAULT_INCLUDE_TEST_ORDERS,
    DEFAULT_MOCK_MODE,
    DOMAIN,
)
from .coordinator import ShopifyDataUpdateCoordinator
from .oauth import ShopifyTokenError, ShopifyTokenManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Shopify from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Get configuration
    shop_domain = entry.data[CONF_SHOP_DOMAIN]
    client_id = entry.data[CONF_CLIENT_ID]
    client_secret = entry.data[CONF_CLIENT_SECRET]
    api_version = entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    timezone_override = entry.data.get(CONF_TIMEZONE_OVERRIDE)
    include_test = entry.data.get(CONF_INCLUDE_TEST_ORDERS, DEFAULT_INCLUDE_TEST_ORDERS)
    mock_mode = entry.data.get(CONF_MOCK_MODE, DEFAULT_MOCK_MODE)

    # Create token manager
    token_manager = ShopifyTokenManager(
        hass=hass,
        shop_domain=shop_domain,
        client_id=client_id,
        client_secret=client_secret,
    )

    # Load stored token if available
    stored_token = entry.data.get(CONF_ACCESS_TOKEN)
    stored_expires = entry.data.get(CONF_TOKEN_EXPIRES_AT)
    if stored_token and stored_expires:
        try:
            expires_at = datetime.fromisoformat(stored_expires)
            token_manager.set_token(stored_token, expires_at)
            _LOGGER.debug(
                "Loaded stored token for %s (expires: %s)",
                shop_domain,
                stored_expires,
            )
        except (ValueError, TypeError) as err:
            _LOGGER.warning("Failed to parse stored token expiry: %s", err)

    # Get valid token (will refresh if needed)
    try:
        access_token = await token_manager.async_get_token()
    except ShopifyTokenError as err:
        _LOGGER.error("Failed to get token for %s: %s", shop_domain, err)
        raise ConfigEntryAuthFailed(
            f"Failed to authenticate with Shopify for {shop_domain}. "
            "Please check your client credentials."
        ) from err

    # Update stored token if it was refreshed
    if token_manager.token_expires_at:
        new_data = dict(entry.data)
        new_data[CONF_ACCESS_TOKEN] = access_token
        new_data[CONF_TOKEN_EXPIRES_AT] = token_manager.token_expires_at.isoformat()
        hass.config_entries.async_update_entry(entry, data=new_data)

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
        raise ConfigEntryAuthFailed(
            f"Authentication failed for {shop_domain}. "
            "Please check your credentials and ensure read_orders scope is granted."
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

    # Create coordinator with token manager
    coordinator = ShopifyDataUpdateCoordinator(
        hass=hass,
        client=client,
        config_entry=entry,
        token_manager=token_manager,
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator and token manager
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
        "token_manager": token_manager,
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
