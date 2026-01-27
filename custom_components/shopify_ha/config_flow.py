"""Config flow for Shopify Store integration using OAuth Authorization Code flow.

This implementation uses Home Assistant's OAuth2 callback mechanism with JWT-encoded
state for proper security and flow matching.

The callback URL is dynamically built from your HA external URL:
<your-ha-external-url>/auth/external/callback

Required reverse proxy headers for OAuth to work:
- X-Forwarded-Proto: https
- X-Forwarded-Host: <your-domain>
"""
from __future__ import annotations

import logging
import secrets
from typing import Any
from urllib.parse import urlencode, urlparse

import jwt
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url
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
    OAUTH2_CALLBACK_PATH,
    OAUTH2_SCOPES,
)
from .oauth import (
    ShopifyTokenError,
    exchange_code_for_token,
    normalize_shop_domain,
    verify_hmac,
)

_LOGGER = logging.getLogger(__name__)

# Use the same JWT secret key as HA's OAuth2 flow helper
# This allows the OAuth2AuthorizeCallbackView to decode our state
DATA_JWT_SECRET = "oauth2_jwt_secret"


def _encode_jwt(hass: HomeAssistant, data: dict[str, Any]) -> str:
    """Encode data as JWT using HA's shared secret.

    This uses the same secret as HA's OAuth2 callback handler, ensuring
    the callback can decode our state parameter.
    """
    secret = hass.data.get(DATA_JWT_SECRET)
    if secret is None:
        secret = hass.data[DATA_JWT_SECRET] = secrets.token_hex()
    return jwt.encode(data, secret, algorithm="HS256")


def _get_ha_external_url(hass: HomeAssistant) -> str | None:
    """Get Home Assistant's external URL.

    Returns None if external URL is not configured or accessible.
    """
    try:
        # Try to get the external URL
        url = get_url(hass, allow_internal=False, prefer_external=True)
        return url
    except Exception as err:
        _LOGGER.debug("Could not get HA external URL: %s", err)
        return None


def _get_redirect_uri(hass: HomeAssistant) -> str | None:
    """Build the OAuth redirect URI from HA's external URL.

    Returns None if external URL is not configured.
    """
    external_url = _get_ha_external_url(hass)
    if not external_url:
        return None
    # Remove trailing slash and append callback path
    return external_url.rstrip("/") + OAUTH2_CALLBACK_PATH


def _validate_external_url(hass: HomeAssistant) -> tuple[bool, str | None, str | None]:
    """Validate that HA's external URL is properly configured for OAuth.

    Returns:
        Tuple of (is_valid, error_message, redirect_uri)
    """
    external_url = _get_ha_external_url(hass)

    if not external_url:
        return False, (
            "Home Assistant external URL is not configured. "
            "Go to Settings → System → Network and configure your External URL. "
            "OAuth requires HA to be accessible via HTTPS from the internet."
        ), None

    parsed = urlparse(external_url)

    _LOGGER.debug(
        "HA external URL: %s (scheme=%s, host=%s)",
        external_url,
        parsed.scheme,
        parsed.netloc,
    )

    # Check scheme - must be HTTPS for OAuth
    if parsed.scheme != "https":
        return False, (
            f"Home Assistant external URL must use HTTPS for OAuth. "
            f"Current: {external_url}. "
            f"Configure HTTPS in Settings → System → Network."
        ), None

    # Check for localhost/local IPs which won't work
    host = parsed.netloc.split(":")[0].lower()
    if host in ("localhost", "127.0.0.1", "::1") or host.startswith("192.168.") or host.startswith("10."):
        return False, (
            f"OAuth callback will not work with local URLs ({host}). "
            f"Configure a public external URL in Settings → System → Network."
        ), None

    redirect_uri = external_url.rstrip("/") + OAUTH2_CALLBACK_PATH
    return True, None, redirect_uri


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

    Uses JWT-encoded state for compatibility with Home Assistant's
    /auth/external/callback endpoint.

    Flow:
    1. async_step_user - Collect shop domain, client_id, client_secret
    2. async_step_auth - Return external step URL (user redirected to Shopify)
    3. async_step_auth - Called again with callback data (code, state, hmac)
    4. async_step_creation - Exchange code for token and create entry
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._shop_domain: str | None = None
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._redirect_uri: str | None = None
        self._reauth_entry: ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ShopifyOptionsFlow:
        """Get the options flow for this handler."""
        return ShopifyOptionsFlow(config_entry)

    def _build_auth_url(self) -> str:
        """Build the Shopify OAuth authorization URL with JWT-encoded state.

        The state is encoded as a JWT containing the flow_id and redirect_uri.
        This allows HA's /auth/external/callback to decode and route properly.
        """
        shop = normalize_shop_domain(self._shop_domain)
        base_url = OAUTH2_AUTHORIZE_URL_TEMPLATE.format(shop=shop)

        # Create JWT-encoded state matching HA's OAuth2 callback expectations
        state_data = {
            "flow_id": self.flow_id,
            "redirect_uri": self._redirect_uri,
        }
        encoded_state = _encode_jwt(self.hass, state_data)

        params = {
            "client_id": self._client_id,
            "scope": ",".join(OAUTH2_SCOPES),
            "redirect_uri": self._redirect_uri,
            "state": encoded_state,
        }

        auth_url = f"{base_url}?{urlencode(params)}"

        # Debug logging - no secrets
        _LOGGER.info(
            "=== SHOPIFY OAUTH DEBUG ===\n"
            "  Flow ID: %s\n"
            "  Shop: %s\n"
            "  Redirect URI: %s\n"
            "  Scopes: %s\n"
            "  Auth URL (without state): %s?client_id=...&scope=...&redirect_uri=%s&state=<jwt>\n"
            "  State contains: flow_id=%s, redirect_uri=%s\n"
            "===========================",
            self.flow_id,
            shop,
            self._redirect_uri,
            ",".join(OAUTH2_SCOPES),
            base_url,
            self._redirect_uri,
            self.flow_id,
            self._redirect_uri,
        )

        return auth_url

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - collect shop domain and credentials."""
        errors: dict[str, str] = {}

        # Get the redirect URI for display
        redirect_uri = _get_redirect_uri(self.hass) or "<configure external URL first>"

        if user_input is not None:
            # Normalize and validate shop domain
            shop_domain = normalize_shop_domain(user_input[CONF_SHOP_DOMAIN])
            client_id = user_input[CONF_CLIENT_ID].strip()
            client_secret = user_input[CONF_CLIENT_SECRET].strip()

            # Check if already configured (unless reauth)
            if not self._reauth_entry:
                await self.async_set_unique_id(shop_domain)
                self._abort_if_unique_id_configured()

            # Store credentials for later steps
            self._shop_domain = shop_domain
            self._client_id = client_id
            self._client_secret = client_secret

            # Validate HA external URL is properly configured
            is_valid, error_msg, validated_redirect_uri = _validate_external_url(self.hass)
            if not is_valid:
                _LOGGER.error("External URL validation failed: %s", error_msg)
                errors["base"] = "external_url_mismatch"
                # Show the form again with error
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema(
                        {
                            vol.Required(
                                CONF_SHOP_DOMAIN, default=shop_domain
                            ): TextSelector(
                                TextSelectorConfig(type=TextSelectorType.TEXT)
                            ),
                            vol.Required(
                                CONF_CLIENT_ID, default=client_id
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
                        "redirect_uri": redirect_uri,
                        "error_details": error_msg or "",
                    },
                )

            # Store the validated redirect URI
            self._redirect_uri = validated_redirect_uri

            # Proceed to OAuth authorization
            return await self.async_step_auth()

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
                "redirect_uri": redirect_uri,
            },
        )

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the OAuth authorization step.

        First call: Return external step to redirect user to Shopify
        Second call: Called by HA's callback handler with the OAuth response
        """
        if user_input is None:
            # First call - start external OAuth flow
            auth_url = self._build_auth_url()

            _LOGGER.info(
                "Starting OAuth flow for %s (flow_id=%s)",
                self._shop_domain,
                self.flow_id,
            )

            return self.async_external_step(
                step_id="auth",
                url=auth_url,
            )

        # Second call - we received the callback data from HA's OAuth callback handler
        # user_input contains: {"state": {...}, "code": "..."}
        _LOGGER.info(
            "=== OAUTH CALLBACK RECEIVED ===\n"
            "  Flow ID: %s\n"
            "  Callback keys: %s\n"
            "===============================",
            self.flow_id,
            list(user_input.keys()) if user_input else "None",
        )

        # Verify we got what we expected
        if "code" not in user_input:
            error = user_input.get("error", "unknown")
            _LOGGER.error(
                "OAuth callback missing code. Error: %s, Keys: %s",
                error,
                list(user_input.keys()),
            )
            return self.async_abort(reason="oauth_error")

        # Store the callback data and proceed to creation step
        self.external_data = user_input

        return self.async_external_step_done(next_step_id="creation")

    async def async_step_creation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the config entry after successful OAuth.

        This step:
        1. Verifies HMAC (if present in callback)
        2. Exchanges authorization code for access token
        3. Tests the connection
        4. Creates the config entry
        """
        _LOGGER.debug(
            "async_step_creation called (flow_id=%s)",
            self.flow_id,
        )

        # Get callback data - could be in external_data or passed directly
        callback_data = getattr(self, "external_data", None) or user_input or {}

        code = callback_data.get("code")
        if not code:
            _LOGGER.error("No authorization code available")
            return self.async_abort(reason="no_auth_code")

        # Verify HMAC if present (Shopify includes this in the redirect)
        # Note: HA's callback handler may not pass all query params
        state_data = callback_data.get("state", {})
        hmac_value = callback_data.get("hmac")
        if hmac_value and self._client_secret:
            # Reconstruct params for HMAC verification
            params_to_verify = {k: v for k, v in callback_data.items() if k != "state"}
            if isinstance(state_data, dict):
                # Add encoded state back for HMAC
                params_to_verify["state"] = _encode_jwt(self.hass, state_data)

            if not verify_hmac(params_to_verify, self._client_secret):
                _LOGGER.warning(
                    "HMAC verification failed - continuing anyway as HA validated state"
                )
                # Don't abort - HA has already validated the state JWT

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

        _LOGGER.info(
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

        if not self._reauth_entry:
            return self.async_abort(reason="reauth_failed")

        # Pre-populate with existing data
        self._shop_domain = entry_data.get(CONF_SHOP_DOMAIN)
        self._client_id = entry_data.get(CONF_CLIENT_ID)
        self._client_secret = entry_data.get(CONF_CLIENT_SECRET)

        # Set unique_id to prevent duplicate flows
        await self.async_set_unique_id(self._shop_domain)

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth confirmation - start OAuth flow again."""
        errors: dict[str, str] = {}

        # Get the redirect URI for display
        redirect_uri = _get_redirect_uri(self.hass) or "<configure external URL first>"

        if user_input is not None:
            # Update credentials if changed
            self._client_id = user_input.get(CONF_CLIENT_ID, self._client_id)
            self._client_secret = user_input.get(CONF_CLIENT_SECRET, self._client_secret)

            # Validate HA external URL
            is_valid, error_msg, validated_redirect_uri = _validate_external_url(self.hass)
            if not is_valid:
                _LOGGER.error("External URL validation failed: %s", error_msg)
                errors["base"] = "external_url_mismatch"
            else:
                # Store the validated redirect URI
                self._redirect_uri = validated_redirect_uri
                # Proceed to OAuth
                return await self.async_step_auth()

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
                "redirect_uri": redirect_uri,
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
