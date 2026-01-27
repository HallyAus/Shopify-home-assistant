"""Config flow for Shopify Store integration using OAuth Authorization Code flow.

IMPORTANT: This integration requires Home Assistant to be accessible via HTTPS
at the configured external URL. The callback URL must exactly match:
https://homeassistant.printforge.com.au/auth/external/callback

Required reverse proxy headers:
- X-Forwarded-Proto: https
- X-Forwarded-Host: homeassistant.printforge.com.au
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

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
    CONF_GRANTED_SCOPES,
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
    OAUTH2_AUTHORIZE_URL_TEMPLATE,
    OAUTH2_REDIRECT_URI,
    OAUTH2_SCOPES,
)
from .oauth import (
    ShopifyTokenError,
    exchange_code_for_token,
    normalize_shop_domain,
    verify_hmac,
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
    """Handle a config flow for Shopify using OAuth Authorization Code flow.

    Uses Home Assistant's external step flow mechanism. The flow_id is used
    as the OAuth state parameter so HA can match the callback.

    IMPORTANT: Home Assistant must be accessible via HTTPS at the external URL.
    The reverse proxy must set X-Forwarded-Proto and X-Forwarded-Host headers.
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._shop_domain: str | None = None
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._reauth_entry: ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ShopifyOptionsFlow:
        """Get the options flow for this handler."""
        return ShopifyOptionsFlow(config_entry)

    def _build_auth_url(self) -> str:
        """Build the Shopify OAuth authorization URL.

        Uses self.flow_id as the state parameter - this is CRITICAL for
        Home Assistant's external flow handling to work correctly.
        """
        shop = normalize_shop_domain(self._shop_domain)
        base_url = OAUTH2_AUTHORIZE_URL_TEMPLATE.format(shop=shop)

        # IMPORTANT: Use flow_id as state - HA uses this to match the callback
        params = {
            "client_id": self._client_id,
            "scope": ",".join(OAUTH2_SCOPES),
            "redirect_uri": OAUTH2_REDIRECT_URI,
            "state": self.flow_id,  # HA's flow_id is used as OAuth state
        }

        auth_url = f"{base_url}?{urlencode(params)}"

        _LOGGER.debug(
            "Built auth URL for shop=%s, redirect_uri=%s, state(flow_id)=%s",
            shop,
            OAUTH2_REDIRECT_URI,
            self.flow_id,
        )

        return auth_url

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - collect shop domain and credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Normalize and validate shop domain
            shop_domain = normalize_shop_domain(user_input[CONF_SHOP_DOMAIN])
            client_id = user_input[CONF_CLIENT_ID].strip()
            client_secret = user_input[CONF_CLIENT_SECRET].strip()

            # Check if already configured
            await self.async_set_unique_id(shop_domain)
            self._abort_if_unique_id_configured()

            # Store credentials for later steps
            self._shop_domain = shop_domain
            self._client_id = client_id
            self._client_secret = client_secret

            # Build authorization URL using flow_id as state
            auth_url = self._build_auth_url()

            _LOGGER.info(
                "Starting OAuth flow for %s (flow_id=%s)",
                shop_domain,
                self.flow_id,
            )

            # Use external step - HA will track this flow by flow_id
            return self.async_external_step(
                step_id="authorize",
                url=auth_url,
            )

        # Show the form to collect credentials
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
            description_placeholders={
                "redirect_uri": OAUTH2_REDIRECT_URI,
            },
        )

    async def async_step_authorize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the external authorization callback.

        This is called by Home Assistant when the OAuth callback is received.
        HA matches the state parameter to find this flow, then passes the
        callback query parameters as user_input.
        """
        _LOGGER.debug(
            "async_step_authorize called (flow_id=%s), user_input keys: %s",
            self.flow_id,
            list(user_input.keys()) if user_input else None,
        )

        if user_input is None:
            # No callback data yet - this shouldn't happen normally
            # The external step is waiting for the callback
            _LOGGER.warning(
                "async_step_authorize called without user_input (flow_id=%s)",
                self.flow_id,
            )
            return self.async_external_step_done(next_step_id="authorize")

        # We have the callback data from Shopify
        # Verify HMAC if present (Shopify includes this for security)
        hmac_value = user_input.get("hmac")
        if hmac_value and self._client_secret:
            if not verify_hmac(user_input, self._client_secret):
                _LOGGER.error("HMAC verification failed for flow_id=%s", self.flow_id)
                return self.async_abort(reason="invalid_hmac")

        # Get authorization code
        code = user_input.get("code")
        if not code:
            _LOGGER.error(
                "No authorization code in callback (flow_id=%s). "
                "Received params: %s",
                self.flow_id,
                [k for k in user_input.keys() if k not in ("hmac",)],
            )
            return self.async_abort(reason="no_auth_code")

        # Log received shop (without sensitive data)
        callback_shop = user_input.get("shop")
        if callback_shop:
            callback_shop = normalize_shop_domain(callback_shop)
            _LOGGER.debug(
                "Callback shop=%s, expected=%s",
                callback_shop,
                self._shop_domain,
            )
            # Use the shop from callback as it's authoritative
            if callback_shop != self._shop_domain:
                _LOGGER.info(
                    "Using shop from callback: %s (was: %s)",
                    callback_shop,
                    self._shop_domain,
                )
                self._shop_domain = callback_shop

        # Exchange code for access token
        _LOGGER.debug("Exchanging authorization code for access token")
        try:
            token_data = await exchange_code_for_token(
                hass=self.hass,
                shop_domain=self._shop_domain,
                client_id=self._client_id,
                client_secret=self._client_secret,
                code=code,
            )
        except ShopifyTokenError as err:
            _LOGGER.error("Token exchange failed: %s", err)
            return self.async_abort(reason="token_exchange_failed")

        access_token = token_data["access_token"]
        granted_scopes = token_data.get("scope", "")

        _LOGGER.debug(
            "Token exchange successful, granted scopes: %s",
            granted_scopes,
        )

        # Test the connection with the new token
        try:
            session = async_get_clientsession(self.hass)
            client = ShopifyGraphQLClient(
                shop_domain=self._shop_domain,
                access_token=access_token,
                api_version=DEFAULT_API_VERSION,
                session=session,
            )
            shop_info = await client.test_connection()
            shop_name = shop_info.name
            _LOGGER.info(
                "Successfully connected to Shopify store: %s",
                shop_name,
            )
        except ShopifyAuthError as err:
            _LOGGER.error("Auth test failed: %s", err)
            return self.async_abort(reason="invalid_auth")
        except ShopifyConnectionError as err:
            _LOGGER.error("Connection test failed: %s", err)
            return self.async_abort(reason="cannot_connect")
        except Exception as err:
            _LOGGER.exception("Unexpected error testing connection: %s", err)
            return self.async_abort(reason="unknown")

        # Create config entry
        data = {
            CONF_SHOP_DOMAIN: self._shop_domain,
            CONF_CLIENT_ID: self._client_id,
            CONF_CLIENT_SECRET: self._client_secret,
            CONF_ACCESS_TOKEN: access_token,
            CONF_GRANTED_SCOPES: granted_scopes,
            CONF_API_VERSION: DEFAULT_API_VERSION,
            CONF_TIMEZONE_OVERRIDE: "",
            CONF_INCLUDE_TEST_ORDERS: DEFAULT_INCLUDE_TEST_ORDERS,
            CONF_MOCK_MODE: DEFAULT_MOCK_MODE,
        }

        # Handle reauth
        if self._reauth_entry:
            self.hass.config_entries.async_update_entry(
                self._reauth_entry,
                data=data,
            )
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        return self.async_create_entry(
            title=shop_name or self._shop_domain,
            data=data,
            options={
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                CONF_MONTHS_LOOKBACK: DEFAULT_MONTHS_LOOKBACK,
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication - token revoked or invalid."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )

        # Pre-populate with existing data
        self._shop_domain = entry_data.get(CONF_SHOP_DOMAIN)
        self._client_id = entry_data.get(CONF_CLIENT_ID)
        self._client_secret = entry_data.get(CONF_CLIENT_SECRET)

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth confirmation - start OAuth flow again."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Update credentials if changed
            self._client_id = user_input.get(CONF_CLIENT_ID, self._client_id)
            self._client_secret = user_input.get(CONF_CLIENT_SECRET, self._client_secret)

            # Build authorization URL using flow_id as state
            auth_url = self._build_auth_url()

            _LOGGER.info(
                "Starting reauth OAuth flow for %s (flow_id=%s)",
                self._shop_domain,
                self.flow_id,
            )

            return self.async_external_step(
                step_id="authorize",
                url=auth_url,
            )

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
                "redirect_uri": OAUTH2_REDIRECT_URI,
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
