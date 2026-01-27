"""Config flow for Shopify Store integration using OAuth Authorization Code flow."""
from __future__ import annotations

import logging
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
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
    ERROR_UNKNOWN,
    OAUTH2_REDIRECT_URI,
)
from .oauth import (
    ShopifyTokenError,
    build_authorization_url,
    exchange_code_for_token,
    generate_state,
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

    This uses external OAuth where the user is redirected to Shopify to
    authorize the app, then Shopify redirects back to Home Assistant's
    callback URL with an authorization code.

    Shopify access tokens are long-lived (no refresh token needed).
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._shop_domain: str | None = None
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._oauth_state: str | None = None
        self._reauth_entry: ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ShopifyOptionsFlow:
        """Get the options flow for this handler."""
        return ShopifyOptionsFlow(config_entry)

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

            # Generate OAuth state for CSRF protection
            self._oauth_state = generate_state()

            # Build authorization URL
            auth_url = build_authorization_url(
                shop_domain=shop_domain,
                client_id=client_id,
                state=self._oauth_state,
            )

            _LOGGER.debug(
                "Starting OAuth flow for %s, redirecting to Shopify",
                shop_domain,
            )

            # Use external step to redirect user to Shopify
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
        """Handle the external authorization step.

        This step waits for the user to complete authorization on Shopify.
        Home Assistant will receive the callback at /auth/external/callback
        and then call async_step_callback.
        """
        return self.async_external_step_done(next_step_id="callback")

    async def async_step_callback(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the OAuth callback from Shopify.

        This is called after the external OAuth callback is received.
        The callback URL parameters are passed via user_input.
        """
        errors: dict[str, str] = {}

        if user_input is None:
            # Show form to manually enter callback parameters if auto-redirect failed
            return self.async_show_form(
                step_id="callback",
                data_schema=vol.Schema(
                    {
                        vol.Required("code"): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.TEXT)
                        ),
                        vol.Required("state"): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.TEXT)
                        ),
                        vol.Optional("hmac"): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.TEXT)
                        ),
                        vol.Optional("shop"): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.TEXT)
                        ),
                    }
                ),
                errors=errors,
                description_placeholders={
                    "expected_state": self._oauth_state or "unknown",
                },
            )

        # Verify state matches (CSRF protection)
        received_state = user_input.get("state", "")
        if received_state != self._oauth_state:
            _LOGGER.error(
                "OAuth state mismatch: expected %s, got %s",
                self._oauth_state,
                received_state,
            )
            return self.async_abort(reason="oauth_state_mismatch")

        # Verify HMAC if present
        hmac_value = user_input.get("hmac")
        if hmac_value and self._client_secret:
            if not verify_hmac(user_input, self._client_secret):
                _LOGGER.error("HMAC verification failed")
                return self.async_abort(reason="invalid_hmac")

        # Get authorization code
        code = user_input.get("code")
        if not code:
            _LOGGER.error("No authorization code in callback")
            return self.async_abort(reason="no_auth_code")

        # Verify shop matches (if provided)
        callback_shop = user_input.get("shop")
        if callback_shop:
            callback_shop = normalize_shop_domain(callback_shop)
            if callback_shop != self._shop_domain:
                _LOGGER.warning(
                    "Shop mismatch: expected %s, got %s",
                    self._shop_domain,
                    callback_shop,
                )
                # Use the shop from callback as it's authoritative
                self._shop_domain = callback_shop

        # Exchange code for access token
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
        # Note: client_id and client_secret are stored for potential reauth
        # Access token is long-lived (no refresh needed)
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

            # Generate new OAuth state
            self._oauth_state = generate_state()

            # Build authorization URL
            auth_url = build_authorization_url(
                shop_domain=self._shop_domain,
                client_id=self._client_id,
                state=self._oauth_state,
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
