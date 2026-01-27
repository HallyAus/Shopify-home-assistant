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
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
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
    AUTH_MODE_OAUTH,
    AUTH_MODE_TOKEN,
    CONF_ACCESS_TOKEN,
    CONF_API_VERSION,
    CONF_AUTH_MODE,
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
    OAUTH2_SCOPES,
)
from .oauth import (
    ShopifyOAuth2TokenError,
    build_authorization_url,
    exchange_code_for_token,
    generate_state,
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
    """Handle a config flow for Shopify."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._shop_domain: str | None = None
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._oauth_state: str | None = None
        self._auth_mode: str | None = None
        self._access_token: str | None = None
        self._shop_info: dict[str, Any] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ShopifyOptionsFlow:
        """Get the options flow for this handler."""
        return ShopifyOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - choose auth method."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._auth_mode = user_input.get(CONF_AUTH_MODE, AUTH_MODE_OAUTH)

            if self._auth_mode == AUTH_MODE_OAUTH:
                return await self.async_step_oauth_init()
            else:
                return await self.async_step_manual_token()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AUTH_MODE, default=AUTH_MODE_OAUTH
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": AUTH_MODE_OAUTH, "label": "OAuth (Recommended)"},
                                {"value": AUTH_MODE_TOKEN, "label": "Manual Access Token"},
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_oauth_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Initialize OAuth flow - collect shop domain and credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._shop_domain = user_input[CONF_SHOP_DOMAIN]
            self._client_id = user_input[CONF_CLIENT_ID]
            self._client_secret = user_input[CONF_CLIENT_SECRET]

            # Normalize shop domain
            shop = self._normalize_shop_domain(self._shop_domain)
            self._shop_domain = shop

            # Check if already configured
            await self.async_set_unique_id(shop)
            self._abort_if_unique_id_configured()

            # Generate state for CSRF protection
            self._oauth_state = generate_state()

            # Build authorization URL
            redirect_uri = self._get_redirect_uri()

            _LOGGER.debug(
                "Starting OAuth flow for shop %s with redirect URI: %s",
                shop,
                redirect_uri,
            )

            auth_url = build_authorization_url(
                shop_domain=shop,
                client_id=self._client_id,
                redirect_uri=redirect_uri,
                state=self._oauth_state,
                scopes=OAUTH2_SCOPES,
            )

            return self.async_external_step(
                step_id="oauth_authorize",
                url=auth_url,
            )

        return self.async_show_form(
            step_id="oauth_init",
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

    async def async_step_oauth_authorize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the external authorization step completion."""
        return self.async_external_step_done(next_step_id="oauth_callback")

    async def async_step_oauth_callback(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle OAuth callback with authorization code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input.get("code")
            state = user_input.get("state")
            error = user_input.get("error")

            if error:
                _LOGGER.error("OAuth error from Shopify: %s", error)
                return self.async_abort(reason="oauth_error")

            # Verify state to prevent CSRF
            if state != self._oauth_state:
                _LOGGER.error(
                    "OAuth state mismatch. Expected: %s, Got: %s",
                    self._oauth_state,
                    state,
                )
                return self.async_abort(reason="oauth_state_mismatch")

            if not code:
                _LOGGER.error("No authorization code received from Shopify")
                return self.async_abort(reason="no_auth_code")

            try:
                # Exchange code for access token
                token_data = await exchange_code_for_token(
                    hass=self.hass,
                    shop_domain=self._shop_domain,
                    client_id=self._client_id,
                    client_secret=self._client_secret,
                    code=code,
                )

                self._access_token = token_data["access_token"]
                _LOGGER.info(
                    "Successfully obtained access token for %s",
                    self._shop_domain,
                )

                # Test the connection and create entry
                return await self._test_and_create_entry()

            except ShopifyOAuth2TokenError as err:
                _LOGGER.error("Token exchange failed: %s", err)
                return self.async_abort(reason="token_exchange_failed")

        # If user arrives here without automatic redirect,
        # show form to manually enter the callback parameters
        return self.async_show_form(
            step_id="oauth_callback",
            data_schema=vol.Schema(
                {
                    vol.Required("code"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required("state"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "shop_domain": self._shop_domain,
                "expected_state": self._oauth_state or "",
            },
        )

    async def async_step_manual_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual access token entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._shop_domain = user_input[CONF_SHOP_DOMAIN]
            self._access_token = user_input[CONF_ACCESS_TOKEN]

            # Normalize shop domain
            shop = self._normalize_shop_domain(self._shop_domain)
            self._shop_domain = shop

            # Check if already configured
            await self.async_set_unique_id(shop)
            self._abort_if_unique_id_configured()

            try:
                return await self._test_and_create_entry(
                    extra_data={
                        CONF_API_VERSION: user_input.get(
                            CONF_API_VERSION, DEFAULT_API_VERSION
                        ),
                        CONF_TIMEZONE_OVERRIDE: user_input.get(
                            CONF_TIMEZONE_OVERRIDE, ""
                        ),
                        CONF_INCLUDE_TEST_ORDERS: user_input.get(
                            CONF_INCLUDE_TEST_ORDERS, DEFAULT_INCLUDE_TEST_ORDERS
                        ),
                        CONF_MOCK_MODE: user_input.get(
                            CONF_MOCK_MODE, DEFAULT_MOCK_MODE
                        ),
                    }
                )
            except ShopifyAuthError:
                errors["base"] = ERROR_INVALID_AUTH
            except ShopifyConnectionError as err:
                if "not found" in str(err).lower():
                    errors["base"] = ERROR_INVALID_DOMAIN
                else:
                    errors["base"] = ERROR_CANNOT_CONNECT
            except aiohttp.ClientError:
                errors["base"] = ERROR_CANNOT_CONNECT
            except Exception:
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = ERROR_UNKNOWN

        return self.async_show_form(
            step_id="manual_token",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SHOP_DOMAIN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required(CONF_ACCESS_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(
                        CONF_API_VERSION, default=DEFAULT_API_VERSION
                    ): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Optional(CONF_TIMEZONE_OVERRIDE, default=""): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Optional(
                        CONF_INCLUDE_TEST_ORDERS, default=DEFAULT_INCLUDE_TEST_ORDERS
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_MOCK_MODE, default=DEFAULT_MOCK_MODE
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def _test_and_create_entry(
        self, extra_data: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Test connection and create config entry."""
        extra_data = extra_data or {}

        api_version = extra_data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
        timezone_override = extra_data.get(CONF_TIMEZONE_OVERRIDE, "")
        include_test = extra_data.get(CONF_INCLUDE_TEST_ORDERS, DEFAULT_INCLUDE_TEST_ORDERS)
        mock_mode = extra_data.get(CONF_MOCK_MODE, DEFAULT_MOCK_MODE)

        # Test connection
        client = ShopifyGraphQLClient(
            shop_domain=self._shop_domain,
            access_token=self._access_token,
            api_version=api_version,
            session=async_get_clientsession(self.hass),
            timezone_override=timezone_override or None,
            include_test_orders=include_test,
            mock_mode=mock_mode,
        )

        shop_info = await client.test_connection()
        self._shop_info = {
            "name": shop_info.name,
            "currency": shop_info.currency_code,
            "timezone": shop_info.timezone,
        }

        # Build entry data
        data = {
            CONF_SHOP_DOMAIN: self._shop_domain,
            CONF_ACCESS_TOKEN: self._access_token,
            CONF_AUTH_MODE: self._auth_mode or AUTH_MODE_TOKEN,
            CONF_API_VERSION: api_version,
            CONF_TIMEZONE_OVERRIDE: timezone_override,
            CONF_INCLUDE_TEST_ORDERS: include_test,
            CONF_MOCK_MODE: mock_mode,
        }

        # Add OAuth credentials if used
        if self._client_id:
            data[CONF_CLIENT_ID] = self._client_id
        if self._client_secret:
            data[CONF_CLIENT_SECRET] = self._client_secret

        return self.async_create_entry(
            title=shop_info.name or self._shop_domain,
            data=data,
            options={
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                CONF_MONTHS_LOOKBACK: DEFAULT_MONTHS_LOOKBACK,
            },
        )

    def _get_redirect_uri(self) -> str:
        """Get the OAuth redirect URI for Home Assistant."""
        # Use Home Assistant's external URL for OAuth callback
        base_url = self.hass.config.external_url or self.hass.config.internal_url
        if not base_url:
            base_url = "http://homeassistant.local:8123"
        return f"{base_url.rstrip('/')}/auth/external/callback"

    @staticmethod
    def _normalize_shop_domain(domain: str) -> str:
        """Normalize the shop domain to standard format."""
        domain = domain.strip().lower()
        if domain.startswith("https://"):
            domain = domain[8:]
        elif domain.startswith("http://"):
            domain = domain[7:]
        domain = domain.rstrip("/")
        if not domain.endswith(".myshopify.com"):
            if ".myshopify.com" not in domain:
                domain = f"{domain}.myshopify.com"
        return domain

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthorization."""
        self._shop_domain = entry_data.get(CONF_SHOP_DOMAIN)
        self._auth_mode = entry_data.get(CONF_AUTH_MODE, AUTH_MODE_TOKEN)
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
            if self._auth_mode == AUTH_MODE_OAUTH and self._client_id:
                # Re-do OAuth flow
                self._oauth_state = generate_state()
                redirect_uri = self._get_redirect_uri()
                auth_url = build_authorization_url(
                    shop_domain=self._shop_domain,
                    client_id=self._client_id,
                    redirect_uri=redirect_uri,
                    state=self._oauth_state,
                    scopes=OAUTH2_SCOPES,
                )
                return self.async_external_step(
                    step_id="reauth_oauth",
                    url=auth_url,
                )
            else:
                # Manual token entry
                access_token = user_input.get(CONF_ACCESS_TOKEN)
                try:
                    client = ShopifyGraphQLClient(
                        shop_domain=self._shop_domain,
                        access_token=access_token,
                        session=async_get_clientsession(self.hass),
                    )
                    await client.test_connection()

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

        if self._auth_mode == AUTH_MODE_OAUTH and self._client_id:
            # OAuth reauth - just need confirmation to proceed
            schema = vol.Schema({})
        else:
            # Manual token reauth - need new token
            schema = vol.Schema(
                {
                    vol.Required(CONF_ACCESS_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "shop_domain": self._shop_domain,
            },
        )

    async def async_step_reauth_oauth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle OAuth reauth external step."""
        return self.async_external_step_done(next_step_id="reauth_oauth_callback")

    async def async_step_reauth_oauth_callback(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle OAuth reauth callback."""
        reauth_entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input.get("code")
            state = user_input.get("state")

            if state != self._oauth_state:
                return self.async_abort(reason="oauth_state_mismatch")

            try:
                token_data = await exchange_code_for_token(
                    hass=self.hass,
                    shop_domain=self._shop_domain,
                    client_id=self._client_id,
                    client_secret=self._client_secret,
                    code=code,
                )

                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={
                        **reauth_entry.data,
                        CONF_ACCESS_TOKEN: token_data["access_token"],
                    },
                )
            except ShopifyOAuth2TokenError as err:
                _LOGGER.error("Reauth token exchange failed: %s", err)
                return self.async_abort(reason="token_exchange_failed")

        return self.async_show_form(
            step_id="reauth_oauth_callback",
            data_schema=vol.Schema(
                {
                    vol.Required("code"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required("state"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                }
            ),
            errors=errors,
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
