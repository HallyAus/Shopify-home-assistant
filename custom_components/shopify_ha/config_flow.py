"""Config flow for Shopify integration."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
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
    CONF_TOKEN_EXPIRES_AT,
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
from .oauth import (
    ShopifyTokenError,
    async_get_client_credentials_token,
    normalize_shop_domain,
)

_LOGGER = logging.getLogger(__name__)


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
    """Handle a config flow for Shopify.

    Uses OAuth 2.0 client credentials grant (server-to-server).
    No browser redirects required.
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._shop_domain: str | None = None
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._shop_info: dict[str, Any] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ShopifyOptionsFlow:
        """Get the options flow for this handler."""
        return ShopifyOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - collect credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._shop_domain = user_input[CONF_SHOP_DOMAIN]
            self._client_id = user_input[CONF_CLIENT_ID]
            self._client_secret = user_input[CONF_CLIENT_SECRET]

            # Normalize shop domain
            shop = normalize_shop_domain(self._shop_domain)
            self._shop_domain = shop

            # Check if already configured
            await self.async_set_unique_id(shop)
            self._abort_if_unique_id_configured()

            try:
                # Get token using client credentials grant
                token_data = await async_get_client_credentials_token(
                    hass=self.hass,
                    shop_domain=shop,
                    client_id=self._client_id,
                    client_secret=self._client_secret,
                )

                self._access_token = token_data.access_token
                self._token_expires_at = token_data.expires_at

                _LOGGER.info(
                    "Successfully obtained token for %s (expires: %s)",
                    shop,
                    token_data.expires_at.isoformat(),
                )

                # Test the connection with a GraphQL query
                return await self._test_and_create_entry()

            except ShopifyTokenError as err:
                _LOGGER.error("Token request failed: %s", err)
                errors["base"] = ERROR_INVALID_AUTH
            except ShopifyAuthError as err:
                _LOGGER.error("Auth error: %s", err)
                errors["base"] = ERROR_INVALID_AUTH
            except ShopifyConnectionError as err:
                _LOGGER.error("Connection error: %s", err)
                if "not found" in str(err).lower():
                    errors["base"] = ERROR_INVALID_DOMAIN
                else:
                    errors["base"] = ERROR_CANNOT_CONNECT
            except Exception:
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = ERROR_UNKNOWN

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SHOP_DOMAIN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required(CONF_CLIENT_ID): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required(CONF_CLIENT_SECRET): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def _test_and_create_entry(self) -> ConfigFlowResult:
        """Test connection with GraphQL and create config entry."""
        # Test connection with the obtained token
        client = ShopifyGraphQLClient(
            shop_domain=self._shop_domain,
            access_token=self._access_token,
            api_version=DEFAULT_API_VERSION,
            session=async_get_clientsession(self.hass),
        )

        shop_info = await client.test_connection()
        self._shop_info = {
            "name": shop_info.name,
            "currency": shop_info.currency_code,
            "timezone": shop_info.timezone,
        }

        # Create entry with credentials and token
        data = {
            CONF_SHOP_DOMAIN: self._shop_domain,
            CONF_CLIENT_ID: self._client_id,
            CONF_CLIENT_SECRET: self._client_secret,
            CONF_ACCESS_TOKEN: self._access_token,
            CONF_TOKEN_EXPIRES_AT: self._token_expires_at.isoformat(),
            CONF_API_VERSION: DEFAULT_API_VERSION,
            CONF_TIMEZONE_OVERRIDE: "",
            CONF_INCLUDE_TEST_ORDERS: DEFAULT_INCLUDE_TEST_ORDERS,
            CONF_MOCK_MODE: DEFAULT_MOCK_MODE,
        }

        return self.async_create_entry(
            title=shop_info.name or self._shop_domain,
            data=data,
            options={
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                CONF_MONTHS_LOOKBACK: DEFAULT_MONTHS_LOOKBACK,
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthorization (e.g., if credentials changed)."""
        self._shop_domain = entry_data.get(CONF_SHOP_DOMAIN)
        self._client_id = entry_data.get(CONF_CLIENT_ID)
        self._client_secret = entry_data.get(CONF_CLIENT_SECRET)

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauthorization confirmation."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            client_id = user_input.get(CONF_CLIENT_ID, self._client_id)
            client_secret = user_input.get(CONF_CLIENT_SECRET, self._client_secret)

            try:
                # Get new token
                token_data = await async_get_client_credentials_token(
                    hass=self.hass,
                    shop_domain=self._shop_domain,
                    client_id=client_id,
                    client_secret=client_secret,
                )

                # Test connection
                client = ShopifyGraphQLClient(
                    shop_domain=self._shop_domain,
                    access_token=token_data.access_token,
                    session=async_get_clientsession(self.hass),
                )
                await client.test_connection()

                # Update entry with new credentials and token
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={
                        **reauth_entry.data,
                        CONF_CLIENT_ID: client_id,
                        CONF_CLIENT_SECRET: client_secret,
                        CONF_ACCESS_TOKEN: token_data.access_token,
                        CONF_TOKEN_EXPIRES_AT: token_data.expires_at.isoformat(),
                    },
                )

            except ShopifyTokenError:
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
                    vol.Required(
                        CONF_CLIENT_ID, default=self._client_id or ""
                    ): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required(CONF_CLIENT_SECRET): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "shop_domain": self._shop_domain,
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
            new_options = {**self._config_entry.options, **user_input}

            new_data = dict(self._config_entry.data)
            if CONF_TIMEZONE_OVERRIDE in user_input:
                new_data[CONF_TIMEZONE_OVERRIDE] = user_input[CONF_TIMEZONE_OVERRIDE]
            if CONF_INCLUDE_TEST_ORDERS in user_input:
                new_data[CONF_INCLUDE_TEST_ORDERS] = user_input[CONF_INCLUDE_TEST_ORDERS]

            self.hass.config_entries.async_update_entry(
                self._config_entry,
                data=new_data,
                options=new_options,
            )

            return self.async_create_entry(title="", data=new_options)

        current = {
            **self._config_entry.data,
            **self._config_entry.options,
        }

        return self.async_show_form(
            step_id="init",
            data_schema=get_options_schema(current),
        )
