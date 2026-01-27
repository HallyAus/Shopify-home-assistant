"""Shopify Store Integration for Home Assistant.

Uses OAuth 2.0 Authorization Code flow for authentication.
Shopify access tokens are long-lived and do not require refresh.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
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
    DEFAULT_API_VERSION,
    DEFAULT_INCLUDE_TEST_ORDERS,
    DEFAULT_MOCK_MODE,
    DOMAIN,
)
from .coordinator import ShopifyDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Shopify from a config entry."""
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
