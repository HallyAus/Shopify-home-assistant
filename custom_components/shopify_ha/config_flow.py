"""Config flow for Shopify integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

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
    CONF_MONTHS_LOOKBACK,
    CONF_SCAN_INTERVAL,
    CONF_SHOP_DOMAIN,
    CONF_TIMEZONE_OVERRIDE,
    DEFAULT_API_VERSION,
    DEFAULT_INCLUDE_TEST_ORDERS,
    DEFAULT_MOCK_MODE,
    DEFAULT_MONTHS_LOOKBACK,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
    ERROR_INVALID_DOMAIN,
    ERROR_UNKNOWN,
)

_LOGGER = logging.getLogger(__name__)


def get_config_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Get the config schema with optional defaults."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_SHOP_DOMAIN,
                default=defaults.get(CONF_SHOP_DOMAIN, ""),
            ): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
            vol.Required(
                CONF_ACCESS_TOKEN,
                default=defaults.get(CONF_ACCESS_TOKEN, ""),
            ): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_API_VERSION,
                default=defaults.get(CONF_API_VERSION, DEFAULT_API_VERSION),
            ): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
            vol.Optional(
                CONF_TIMEZONE_OVERRIDE,
                default=defaults.get(CONF_TIMEZONE_OVERRIDE, ""),
            ): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
            vol.Optional(
                CONF_INCLUDE_TEST_ORDERS,
                default=defaults.get(
                    CONF_INCLUDE_TEST_ORDERS, DEFAULT_INCLUDE_TEST_ORDERS
                ),
            ): BooleanSelector(),
            vol.Optional(
                CONF_MOCK_MODE,
                default=defaults.get(CONF_MOCK_MODE, DEFAULT_MOCK_MODE),
            ): BooleanSelector(),
        }
    )


def get_options_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Get the options schema with defaults."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=5,
                    max=1440,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="minutes",
                )
            ),
            vol.Optional(
                CONF_MONTHS_LOOKBACK,
                default=defaults.get(CONF_MONTHS_LOOKBACK, DEFAULT_MONTHS_LOOKBACK),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=120,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="months",
                )
            ),
            vol.Optional(
                CONF_INCLUDE_TEST_ORDERS,
                default=defaults.get(
                    CONF_INCLUDE_TEST_ORDERS, DEFAULT_INCLUDE_TEST_ORDERS
                ),
            ): BooleanSelector(),
            vol.Optional(
                CONF_TIMEZONE_OVERRIDE,
                default=defaults.get(CONF_TIMEZONE_OVERRIDE, ""),
            ): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
        }
    )


class ShopifyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Shopify."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._shop_info: dict[str, Any] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ShopifyOptionsFlow:
        """Get the options flow for this handler."""
        return ShopifyOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                # Validate the input
                shop_domain = user_input[CONF_SHOP_DOMAIN]
                access_token = user_input[CONF_ACCESS_TOKEN]
                api_version = user_input.get(CONF_API_VERSION, DEFAULT_API_VERSION)
                timezone_override = user_input.get(CONF_TIMEZONE_OVERRIDE, "")
                include_test = user_input.get(
                    CONF_INCLUDE_TEST_ORDERS, DEFAULT_INCLUDE_TEST_ORDERS
                )
                mock_mode = user_input.get(CONF_MOCK_MODE, DEFAULT_MOCK_MODE)

                # Normalize domain
                client = ShopifyGraphQLClient(
                    shop_domain=shop_domain,
                    access_token=access_token,
                    api_version=api_version,
                    session=async_get_clientsession(self.hass),
                    timezone_override=timezone_override or None,
                    include_test_orders=include_test,
                    mock_mode=mock_mode,
                )

                # Test connection
                shop_info = await client.test_connection()
                normalized_domain = client.shop_domain

                # Check if already configured
                await self.async_set_unique_id(normalized_domain)
                self._abort_if_unique_id_configured()

                # Store shop info for title
                self._shop_info = {
                    "name": shop_info.name,
                    "currency": shop_info.currency_code,
                    "timezone": shop_info.timezone,
                }

                # Create entry
                return self.async_create_entry(
                    title=shop_info.name or normalized_domain,
                    data={
                        CONF_SHOP_DOMAIN: normalized_domain,
                        CONF_ACCESS_TOKEN: access_token,
                        CONF_API_VERSION: api_version,
                        CONF_TIMEZONE_OVERRIDE: timezone_override,
                        CONF_INCLUDE_TEST_ORDERS: include_test,
                        CONF_MOCK_MODE: mock_mode,
                    },
                    options={
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                        CONF_MONTHS_LOOKBACK: DEFAULT_MONTHS_LOOKBACK,
                    },
                )

            except ShopifyAuthError as err:
                _LOGGER.warning("Authentication failed: %s", err)
                errors["base"] = ERROR_INVALID_AUTH
            except ShopifyConnectionError as err:
                _LOGGER.warning("Connection failed: %s", err)
                if "not found" in str(err).lower():
                    errors["base"] = ERROR_INVALID_DOMAIN
                else:
                    errors["base"] = ERROR_CANNOT_CONNECT
            except aiohttp.ClientError as err:
                _LOGGER.warning("HTTP error: %s", err)
                errors["base"] = ERROR_CANNOT_CONNECT
            except Exception:
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = ERROR_UNKNOWN

        return self.async_show_form(
            step_id="user",
            data_schema=get_config_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthorization."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauthorization confirmation."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            try:
                access_token = user_input[CONF_ACCESS_TOKEN]
                shop_domain = reauth_entry.data[CONF_SHOP_DOMAIN]
                api_version = reauth_entry.data.get(
                    CONF_API_VERSION, DEFAULT_API_VERSION
                )
                mock_mode = reauth_entry.data.get(CONF_MOCK_MODE, DEFAULT_MOCK_MODE)

                client = ShopifyGraphQLClient(
                    shop_domain=shop_domain,
                    access_token=access_token,
                    api_version=api_version,
                    session=async_get_clientsession(self.hass),
                    mock_mode=mock_mode,
                )

                # Test connection
                await client.test_connection()

                # Update entry
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={
                        **reauth_entry.data,
                        CONF_ACCESS_TOKEN: access_token,
                    },
                )

            except ShopifyAuthError:
                errors["base"] = ERROR_INVALID_AUTH
            except ShopifyConnectionError:
                errors["base"] = ERROR_CANNOT_CONNECT
            except Exception:
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = ERROR_UNKNOWN

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCESS_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "shop_domain": reauth_entry.data[CONF_SHOP_DOMAIN]
            },
        )


class ShopifyOptionsFlow(OptionsFlow):
    """Handle options flow for Shopify."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            # Merge with existing options
            new_options = {**self._config_entry.options, **user_input}

            # Also update data if timezone changed
            new_data = dict(self._config_entry.data)
            if CONF_TIMEZONE_OVERRIDE in user_input:
                new_data[CONF_TIMEZONE_OVERRIDE] = user_input[CONF_TIMEZONE_OVERRIDE]
            if CONF_INCLUDE_TEST_ORDERS in user_input:
                new_data[CONF_INCLUDE_TEST_ORDERS] = user_input[
                    CONF_INCLUDE_TEST_ORDERS
                ]

            self.hass.config_entries.async_update_entry(
                self._config_entry,
                data=new_data,
                options=new_options,
            )

            return self.async_create_entry(title="", data=new_options)

        # Get current values from both options and data
        current = {
            **self._config_entry.data,
            **self._config_entry.options,
        }

        return self.async_show_form(
            step_id="init",
            data_schema=get_options_schema(current),
        )
