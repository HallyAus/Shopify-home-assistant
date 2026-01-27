"""DataUpdateCoordinator for Shopify integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    ShopifyAPIError,
    ShopifyAuthError,
    ShopifyConnectionError,
    ShopifyData,
    ShopifyGraphQLClient,
    ShopifyRateLimitError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_MONTHS_LOOKBACK,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN_EXPIRES_AT,
    DEFAULT_MONTHS_LOOKBACK,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .oauth import ShopifyTokenError, ShopifyTokenManager

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


class ShopifyDataUpdateCoordinator(DataUpdateCoordinator[ShopifyData]):
    """Class to manage fetching Shopify data."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: ShopifyGraphQLClient,
        config_entry: ConfigEntry,
        token_manager: ShopifyTokenManager,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        self.config_entry = config_entry
        self._token_manager = token_manager

        # Get scan interval from options or config
        scan_interval_minutes = config_entry.options.get(
            CONF_SCAN_INTERVAL,
            config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{client.shop_domain}",
            update_interval=timedelta(minutes=scan_interval_minutes),
        )

        # Store months lookback setting
        self._months_lookback = config_entry.options.get(
            CONF_MONTHS_LOOKBACK,
            config_entry.data.get(CONF_MONTHS_LOOKBACK, DEFAULT_MONTHS_LOOKBACK),
        )

        _LOGGER.debug(
            "Coordinator initialized for %s with %d minute interval",
            client.shop_domain,
            scan_interval_minutes,
        )

    @property
    def shop_domain(self) -> str:
        """Return the shop domain."""
        return self.client.shop_domain

    @property
    def shop_name(self) -> str:
        """Return the shop name."""
        if self.data and self.data.shop_info:
            return self.data.shop_info.name
        return self.client.shop_domain.split(".")[0]

    async def _async_update_data(self) -> ShopifyData:
        """Fetch data from Shopify API."""
        _LOGGER.debug("Fetching Shopify data for %s", self.shop_domain)

        try:
            # Ensure we have a valid token before fetching
            await self._ensure_valid_token()

            data = await self.client.fetch_all_data(
                months_lookback=self._months_lookback
            )
            _LOGGER.debug(
                "Fetched data: unfulfilled=%d, revenue=%s, total=%d, busiest=%s",
                data.unfulfilled_orders_count,
                data.current_month_revenue_aud,
                data.total_orders_count,
                data.busiest_month,
            )
            return data

        except ShopifyTokenError as err:
            _LOGGER.error("Token error for %s: %s", self.shop_domain, err)
            raise ConfigEntryAuthFailed(
                f"Authentication failed for {self.shop_domain}. "
                "Please check your client credentials."
            ) from err

        except ShopifyAuthError as err:
            _LOGGER.error("Authentication error for %s: %s", self.shop_domain, err)
            raise ConfigEntryAuthFailed(
                f"Authentication failed for {self.shop_domain}. "
                "Please check your access token and API permissions."
            ) from err

        except ShopifyRateLimitError as err:
            _LOGGER.warning(
                "Rate limited by Shopify for %s: %s", self.shop_domain, err
            )
            raise UpdateFailed(
                f"Rate limited by Shopify. Will retry at next interval."
            ) from err

        except ShopifyConnectionError as err:
            _LOGGER.error("Connection error for %s: %s", self.shop_domain, err)
            raise UpdateFailed(
                f"Unable to connect to Shopify: {err}"
            ) from err

        except ShopifyAPIError as err:
            _LOGGER.error("API error for %s: %s", self.shop_domain, err)
            raise UpdateFailed(f"Shopify API error: {err}") from err

        except Exception as err:
            _LOGGER.exception("Unexpected error fetching Shopify data: %s", err)
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def _ensure_valid_token(self) -> None:
        """Ensure we have a valid token, refreshing if necessary.

        This method checks if the token is expired or about to expire,
        refreshes it if needed, updates the client's access token, and
        persists the new token to the config entry.
        """
        # Get current token state before refresh
        was_expired = not self._token_manager.has_valid_token
        old_expires_at = self._token_manager.token_expires_at

        # Get a valid token (will auto-refresh if expired)
        access_token = await self._token_manager.async_get_token()

        # Update client with the (possibly new) token
        self.client.update_access_token(access_token)

        # Check if token was refreshed (expiry changed)
        new_expires_at = self._token_manager.token_expires_at

        if was_expired or (old_expires_at != new_expires_at):
            # Token was refreshed - persist to config entry
            _LOGGER.debug(
                "Token refreshed for %s, persisting to config entry",
                self.shop_domain,
            )
            await self._persist_token(access_token, new_expires_at)

    async def _persist_token(self, access_token: str, expires_at) -> None:
        """Persist updated token to config entry."""
        new_data = dict(self.config_entry.data)
        new_data[CONF_ACCESS_TOKEN] = access_token
        new_data[CONF_TOKEN_EXPIRES_AT] = expires_at.isoformat()

        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data=new_data,
        )
        _LOGGER.debug(
            "Token persisted for %s (expires: %s)",
            self.shop_domain,
            expires_at.isoformat(),
        )

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator and close the client."""
        await super().async_shutdown()
        await self.client.close()

    def update_options(self, options: dict) -> None:
        """Update coordinator options from config entry."""
        # Update scan interval
        new_interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        if self.update_interval != timedelta(minutes=new_interval):
            self.update_interval = timedelta(minutes=new_interval)
            _LOGGER.debug(
                "Updated scan interval to %d minutes for %s",
                new_interval,
                self.shop_domain,
            )

        # Update months lookback
        self._months_lookback = options.get(
            CONF_MONTHS_LOOKBACK, DEFAULT_MONTHS_LOOKBACK
        )
        _LOGGER.debug(
            "Updated months lookback to %d for %s",
            self._months_lookback,
            self.shop_domain,
        )
