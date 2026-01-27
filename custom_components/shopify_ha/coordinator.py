"""DataUpdateCoordinator for Shopify integration.

Shopify access tokens are long-lived and do not require refresh.
If a token becomes invalid (revoked), reauth is triggered.
"""
from __future__ import annotations

import logging
from datetime import timedelta

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
    CONF_MONTHS_LOOKBACK,
    CONF_SCAN_INTERVAL,
    DEFAULT_MONTHS_LOOKBACK,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class ShopifyDataUpdateCoordinator(DataUpdateCoordinator[ShopifyData]):
    """Class to manage fetching Shopify data.

    Uses long-lived Shopify access tokens (no refresh needed).
    Auth failures trigger reauth flow.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: ShopifyGraphQLClient,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        self.config_entry = config_entry

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

        except ShopifyAuthError as err:
            _LOGGER.error("Authentication error for %s: %s", self.shop_domain, err)
            # Shopify tokens are long-lived but can be revoked
            # Trigger reauth flow
            raise ConfigEntryAuthFailed(
                f"Authentication failed for {self.shop_domain}. "
                "The access token may have been revoked. Please re-authenticate."
            ) from err

        except ShopifyRateLimitError as err:
            _LOGGER.warning(
                "Rate limited by Shopify for %s: %s", self.shop_domain, err
            )
            raise UpdateFailed(
                "Rate limited by Shopify. Will retry at next interval."
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
