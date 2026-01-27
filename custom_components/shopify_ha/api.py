"""Shopify Admin API client for Home Assistant integration."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import ClientError, ClientResponseError

from .const import (
    DEFAULT_API_VERSION,
    DEFAULT_PAGE_SIZE,
    GRAPHQL_ORDERS_COUNT_QUERY,
    GRAPHQL_ORDERS_REVENUE_QUERY,
    GRAPHQL_SHOP_INFO_QUERY,
    GRAPHQL_UNFULFILLED_ORDERS_QUERY,
    MAX_PAGE_SIZE,
    SHOPIFY_GRAPHQL_ENDPOINT,
    TARGET_CURRENCY,
    THROTTLE_MAX_COST,
    THROTTLE_MIN_AVAILABLE,
    THROTTLE_RESTORE_RATE,
)

_LOGGER = logging.getLogger(__name__)


class ShopifyAuthError(Exception):
    """Exception for authentication errors."""


class ShopifyConnectionError(Exception):
    """Exception for connection errors."""


class ShopifyRateLimitError(Exception):
    """Exception for rate limit errors."""


class ShopifyAPIError(Exception):
    """General API error."""


@dataclass
class ShopInfo:
    """Shop information from Shopify."""

    name: str
    currency_code: str
    timezone: str
    plan_name: str | None = None


@dataclass
class OrderData:
    """Order data structure."""

    order_id: str
    name: str
    created_at: datetime
    financial_status: str
    fulfillment_status: str | None
    total_price_aud: Decimal
    refunded_aud: Decimal
    currency_code: str
    is_cancelled: bool = False


@dataclass
class MonthlyRevenue:
    """Monthly revenue aggregation."""

    month: str  # YYYY-MM format
    revenue_aud: Decimal
    order_count: int


@dataclass
class ShopifyData:
    """Container for all Shopify data."""

    # Original 4 sensors
    unfulfilled_orders_count: int = 0
    current_month_revenue_aud: Decimal = field(default_factory=lambda: Decimal("0.00"))
    current_month_order_count: int = 0
    total_orders_count: int = 0
    busiest_month: str | None = None
    busiest_month_revenue_aud: Decimal = field(default_factory=lambda: Decimal("0.00"))
    busiest_month_order_count: int = 0

    # New sensors - Today
    today_orders_count: int = 0
    today_revenue_aud: Decimal = field(default_factory=lambda: Decimal("0.00"))
    today_start: datetime | None = None
    today_end: datetime | None = None

    # New sensors - This Week
    week_orders_count: int = 0
    week_revenue_aud: Decimal = field(default_factory=lambda: Decimal("0.00"))
    week_start: datetime | None = None
    week_end: datetime | None = None
    week_number: int = 0

    # New sensors - Average Order Value
    average_order_value_aud: Decimal = field(default_factory=lambda: Decimal("0.00"))

    # New sensors - Order Status
    pending_payment_orders_count: int = 0
    partially_fulfilled_orders_count: int = 0

    # New sensors - Year to Date
    ytd_revenue_aud: Decimal = field(default_factory=lambda: Decimal("0.00"))
    ytd_order_count: int = 0
    year_start: datetime | None = None

    # New sensors - Last 30 Days
    last_30_days_orders_count: int = 0
    last_30_days_revenue_aud: Decimal = field(default_factory=lambda: Decimal("0.00"))
    last_30_days_start: datetime | None = None

    # Common fields
    currency: str = TARGET_CURRENCY
    api_calls_remaining: int | None = None
    last_sync: datetime | None = None
    month_start: datetime | None = None
    month_end: datetime | None = None
    shop_info: ShopInfo | None = None


class ShopifyGraphQLClient:
    """Shopify Admin GraphQL API client."""

    def __init__(
        self,
        shop_domain: str,
        access_token: str,
        api_version: str = DEFAULT_API_VERSION,
        session: aiohttp.ClientSession | None = None,
        timezone_override: str | None = None,
        include_test_orders: bool = False,
        mock_mode: bool = False,
    ) -> None:
        """Initialize the Shopify GraphQL client."""
        self._shop_domain = self._normalize_domain(shop_domain)
        self._access_token = access_token
        self._api_version = api_version
        self._session = session
        self._owns_session = session is None
        self._timezone_override = timezone_override
        self._include_test_orders = include_test_orders
        self._mock_mode = mock_mode

        # Rate limiting state
        self._available_points = THROTTLE_MAX_COST
        self._last_request_time: datetime | None = None
        self._request_lock = asyncio.Lock()

        # Caching
        self._shop_info: ShopInfo | None = None
        self._total_orders_cache: int | None = None
        self._total_orders_last_update: datetime | None = None

    @staticmethod
    def _normalize_domain(domain: str) -> str:
        """Normalize the shop domain."""
        domain = domain.strip().lower()
        # Remove protocol if present
        if domain.startswith("https://"):
            domain = domain[8:]
        elif domain.startswith("http://"):
            domain = domain[7:]
        # Remove trailing slash
        domain = domain.rstrip("/")
        # Ensure .myshopify.com suffix
        if not domain.endswith(".myshopify.com"):
            if ".myshopify.com" not in domain:
                domain = f"{domain}.myshopify.com"
        return domain

    @property
    def shop_domain(self) -> str:
        """Return the shop domain."""
        return self._shop_domain

    @property
    def api_url(self) -> str:
        """Return the GraphQL API URL."""
        endpoint = SHOPIFY_GRAPHQL_ENDPOINT.format(version=self._api_version)
        return f"https://{self._shop_domain}/{endpoint}"

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure we have an active session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close the session if we own it."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def update_access_token(self, access_token: str) -> None:
        """Update the access token.

        This is used when tokens are refreshed by the token manager.
        """
        self._access_token = access_token

    def _get_headers(self) -> dict[str, str]:
        """Get request headers."""
        return {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": self._access_token,
        }

    async def _wait_for_rate_limit(self) -> None:
        """Wait if we're close to rate limit."""
        async with self._request_lock:
            if self._last_request_time:
                elapsed = (datetime.now() - self._last_request_time).total_seconds()
                restored = elapsed * THROTTLE_RESTORE_RATE
                self._available_points = min(
                    THROTTLE_MAX_COST, self._available_points + restored
                )

            if self._available_points < THROTTLE_MIN_AVAILABLE:
                wait_time = (
                    THROTTLE_MIN_AVAILABLE - self._available_points
                ) / THROTTLE_RESTORE_RATE
                _LOGGER.debug("Rate limit: waiting %.2f seconds", wait_time)
                await asyncio.sleep(wait_time)
                self._available_points = THROTTLE_MIN_AVAILABLE

            self._last_request_time = datetime.now()

    async def _execute_graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        retries: int = 3,
    ) -> dict[str, Any]:
        """Execute a GraphQL query."""
        if self._mock_mode:
            return await self._get_mock_response(query, variables)

        await self._wait_for_rate_limit()
        session = await self._ensure_session()

        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                async with session.post(
                    self.api_url,
                    json=payload,
                    headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    # Update rate limit from response headers
                    self._update_rate_limit_from_response(response)

                    if response.status == 401:
                        raise ShopifyAuthError(
                            "Invalid access token or insufficient permissions"
                        )
                    if response.status == 403:
                        raise ShopifyAuthError(
                            "Access forbidden - check API permissions"
                        )
                    if response.status == 404:
                        raise ShopifyConnectionError(
                            f"Shop not found: {self._shop_domain}"
                        )
                    if response.status == 429:
                        # Rate limited - wait and retry
                        retry_after = float(
                            response.headers.get("Retry-After", "2.0")
                        )
                        _LOGGER.warning(
                            "Rate limited, waiting %.1f seconds", retry_after
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    if response.status >= 500:
                        raise ShopifyAPIError(
                            f"Shopify server error: {response.status}"
                        )

                    response.raise_for_status()
                    result = await response.json()

                    # Check for GraphQL errors
                    if "errors" in result:
                        errors = result["errors"]
                        error_messages = [e.get("message", str(e)) for e in errors]

                        # Check for throttled error
                        if any("throttled" in msg.lower() for msg in error_messages):
                            _LOGGER.warning("GraphQL throttled, backing off")
                            self._available_points = 0
                            await asyncio.sleep(2 ** attempt)
                            continue

                        raise ShopifyAPIError(
                            f"GraphQL errors: {'; '.join(error_messages)}"
                        )

                    return result.get("data", {})

            except ClientResponseError as err:
                last_error = err
                _LOGGER.warning(
                    "HTTP error %s on attempt %d: %s",
                    err.status,
                    attempt + 1,
                    err.message,
                )
            except ClientError as err:
                last_error = err
                _LOGGER.warning(
                    "Connection error on attempt %d: %s", attempt + 1, str(err)
                )
            except asyncio.TimeoutError:
                last_error = asyncio.TimeoutError("Request timed out")
                _LOGGER.warning("Timeout on attempt %d", attempt + 1)

            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)

        raise ShopifyConnectionError(
            f"Failed after {retries} attempts: {last_error}"
        ) from last_error

    def _update_rate_limit_from_response(
        self, response: aiohttp.ClientResponse
    ) -> None:
        """Update rate limit tracking from response headers."""
        # Shopify returns cost info in extensions for GraphQL
        # Also check X-Shopify-Shop-Api-Call-Limit for REST-style info
        call_limit = response.headers.get("X-Shopify-Shop-Api-Call-Limit")
        if call_limit:
            try:
                used, available = call_limit.split("/")
                self._available_points = int(available) - int(used)
            except (ValueError, AttributeError):
                pass

    async def _get_mock_response(
        self, query: str, variables: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Return mock data for testing."""
        _LOGGER.debug("Mock mode: returning mock data")

        if "ordersCount" in query:
            return {"ordersCount": {"count": 1234}}

        if "ShopInfo" in query:
            return {
                "shop": {
                    "name": "Mock Test Store",
                    "currencyCode": "AUD",
                    "ianaTimezone": "Australia/Sydney",
                    "plan": {"displayName": "Development"},
                }
            }

        if "UnfulfilledOrders" in query or "unfulfilled" in str(variables).lower():
            return {
                "orders": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "edges": [
                        {
                            "node": {
                                "id": f"gid://shopify/Order/{i}",
                                "name": f"#100{i}",
                                "createdAt": datetime.now().isoformat(),
                                "displayFulfillmentStatus": "UNFULFILLED",
                                "displayFinancialStatus": "PAID",
                            }
                        }
                        for i in range(5)
                    ],
                }
            }

        if "OrdersRevenue" in query:
            now = datetime.now()
            return {
                "orders": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "edges": [
                        {
                            "node": {
                                "id": f"gid://shopify/Order/{i}",
                                "name": f"#200{i}",
                                "createdAt": (
                                    now - timedelta(days=i * 10)
                                ).isoformat(),
                                "displayFinancialStatus": "PAID",
                                "displayFulfillmentStatus": "FULFILLED",
                                "cancelledAt": None,
                                "closed": False,
                                "currentTotalPriceSet": {
                                    "shopMoney": {
                                        "amount": str(100 + i * 50),
                                        "currencyCode": "AUD",
                                    },
                                },
                                "netPaymentSet": {
                                    "shopMoney": {
                                        "amount": str(100 + i * 50),
                                        "currencyCode": "AUD",
                                    },
                                },
                                "totalRefundedSet": {
                                    "shopMoney": {
                                        "amount": "0.00",
                                        "currencyCode": "AUD",
                                    },
                                },
                            }
                        }
                        for i in range(15)
                    ],
                }
            }

        return {}

    async def test_connection(self) -> ShopInfo:
        """Test connection and return shop info."""
        data = await self._execute_graphql(GRAPHQL_SHOP_INFO_QUERY)
        shop = data.get("shop", {})

        self._shop_info = ShopInfo(
            name=shop.get("name", "Unknown"),
            currency_code=shop.get("currencyCode", "USD"),
            timezone=shop.get("ianaTimezone", "UTC"),
            plan_name=shop.get("plan", {}).get("displayName"),
        )
        return self._shop_info

    def _get_timezone(self) -> ZoneInfo:
        """Get the timezone to use for date calculations."""
        if self._timezone_override:
            try:
                return ZoneInfo(self._timezone_override)
            except Exception:
                _LOGGER.warning(
                    "Invalid timezone override %s, using UTC",
                    self._timezone_override,
                )
        if self._shop_info and self._shop_info.timezone:
            try:
                return ZoneInfo(self._shop_info.timezone)
            except Exception:
                _LOGGER.warning(
                    "Invalid shop timezone %s, using UTC",
                    self._shop_info.timezone,
                )
        return ZoneInfo("UTC")

    def _get_month_boundaries(self) -> tuple[datetime, datetime]:
        """Get the start and end of the current month."""
        tz = self._get_timezone()
        now = datetime.now(tz)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return month_start, now

    def _build_order_query(
        self,
        *,
        status_filter: str | None = None,
        fulfillment_filter: str | None = None,
        financial_filter: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> str:
        """Build Shopify order query string.

        Note for API 2026-01:
        - fulfillment_status filter values: unfulfilled, unshipped, partial, shipped, fulfilled
        - Must combine fulfillment_status with status:open or status:any for reliable results
        - Date format: ISO8601 (e.g., 2024-01-01T00:00:00Z)
        """
        parts = []

        if not self._include_test_orders:
            parts.append("-test:true")

        # Status filter - required when using fulfillment_status for reliable results
        if status_filter:
            parts.append(f"status:{status_filter}")

        # Fulfillment filter - valid values: unfulfilled, unshipped, partial, shipped, fulfilled
        if fulfillment_filter:
            parts.append(f"fulfillment_status:{fulfillment_filter}")

        # Financial filter - valid values: paid, pending, refunded, etc.
        if financial_filter:
            parts.append(f"financial_status:{financial_filter}")

        # Date filters - use ISO8601 format
        if created_after:
            # Format with explicit UTC timezone
            date_str = created_after.strftime("%Y-%m-%dT%H:%M:%SZ") if created_after.tzinfo else created_after.isoformat()
            parts.append(f"created_at:>={date_str}")

        if created_before:
            date_str = created_before.strftime("%Y-%m-%dT%H:%M:%SZ") if created_before.tzinfo else created_before.isoformat()
            parts.append(f"created_at:<={date_str}")

        return " AND ".join(parts) if parts else ""

    async def get_unfulfilled_orders_count(self) -> int:
        """Get count of unfulfilled paid orders."""
        query_filter = self._build_order_query(
            status_filter="open",
            fulfillment_filter="unfulfilled",
            financial_filter="paid",
        )

        # Try using ordersCount first (more efficient)
        try:
            data = await self._execute_graphql(
                GRAPHQL_ORDERS_COUNT_QUERY,
                {"query": query_filter},
            )
            if data.get("ordersCount"):
                return data["ordersCount"]["count"]
        except ShopifyAPIError as err:
            _LOGGER.debug(
                "ordersCount not available, falling back to pagination: %s", err
            )

        # Fallback: paginate through orders
        count = 0
        cursor: str | None = None

        while True:
            variables = {
                "first": DEFAULT_PAGE_SIZE,
                "query": query_filter,
            }
            if cursor:
                variables["after"] = cursor

            data = await self._execute_graphql(
                GRAPHQL_UNFULFILLED_ORDERS_QUERY,
                variables,
            )

            orders = data.get("orders", {})
            edges = orders.get("edges", [])
            count += len(edges)

            page_info = orders.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return count

    async def get_current_month_revenue(
        self,
    ) -> tuple[Decimal, int, datetime, datetime]:
        """Get current month revenue in AUD.

        Returns:
            Tuple of (revenue, order_count, month_start, month_end)
        """
        month_start, month_end = self._get_month_boundaries()

        query_filter = self._build_order_query(
            financial_filter="paid",
            created_after=month_start,
            created_before=month_end,
        )

        total_revenue = Decimal("0.00")
        order_count = 0
        cursor: str | None = None

        while True:
            variables = {
                "first": MAX_PAGE_SIZE,
                "query": query_filter,
            }
            if cursor:
                variables["after"] = cursor

            data = await self._execute_graphql(
                GRAPHQL_ORDERS_REVENUE_QUERY,
                variables,
            )

            orders = data.get("orders", {})
            edges = orders.get("edges", [])

            for edge in edges:
                node = edge.get("node", {})

                # Skip cancelled or closed orders without payment
                if node.get("cancelledAt"):
                    continue

                # Extract net revenue using API 2026-01 compatible method
                net_revenue = self._extract_order_revenue(node)

                if net_revenue > 0:
                    total_revenue += net_revenue
                    order_count += 1

            page_info = orders.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return total_revenue, order_count, month_start, month_end

    def _extract_amount(self, price_set: dict[str, Any]) -> Decimal:
        """Extract amount from a MoneyBag, using shopMoney for consistency.

        API 2026-01: Prefer shopMoney for store currency consistency.
        """
        if not price_set:
            return Decimal("0.00")

        # Use shop money for consistent store currency
        shop = price_set.get("shopMoney", {})
        shop_amount = shop.get("amount", "0")
        shop_currency = shop.get("currencyCode", "UNKNOWN")

        if shop_currency and shop_currency != TARGET_CURRENCY:
            _LOGGER.debug(
                "Currency note: shop uses %s, target is %s. Using shop amount.",
                shop_currency,
                TARGET_CURRENCY,
            )

        try:
            return Decimal(shop_amount)
        except (ValueError, TypeError):
            return Decimal("0.00")

    def _extract_order_revenue(self, node: dict[str, Any]) -> Decimal:
        """Extract net revenue from an order node.

        API 2026-01: Uses netPaymentSet when available (actual received payment),
        falls back to currentTotalPriceSet - totalRefundedSet.
        """
        # Prefer netPaymentSet - represents actual money received
        net_payment = node.get("netPaymentSet")
        if net_payment:
            return self._extract_amount(net_payment)

        # Fallback: currentTotalPriceSet minus refunds
        current_total = self._extract_amount(node.get("currentTotalPriceSet", {}))
        refunded = self._extract_amount(node.get("totalRefundedSet", {}))

        return current_total - refunded

    async def get_total_orders_count(self, force_refresh: bool = False) -> int:
        """Get total orders count (all time).

        Uses caching to avoid excessive API calls. Only refreshes daily.
        """
        now = datetime.now()

        # Use cache if available and not force refresh
        if (
            not force_refresh
            and self._total_orders_cache is not None
            and self._total_orders_last_update
            and (now - self._total_orders_last_update) < timedelta(hours=24)
        ):
            return self._total_orders_cache

        query_filter = self._build_order_query()

        # Try ordersCount endpoint
        try:
            data = await self._execute_graphql(
                GRAPHQL_ORDERS_COUNT_QUERY,
                {"query": query_filter},
            )
            if data.get("ordersCount"):
                count = data["ordersCount"]["count"]
                self._total_orders_cache = count
                self._total_orders_last_update = now
                return count
        except ShopifyAPIError as err:
            _LOGGER.debug("ordersCount query failed: %s", err)

        # Fallback: count via pagination (expensive, but works)
        _LOGGER.warning(
            "ordersCount not available, counting via pagination (slow)"
        )
        count = 0
        cursor: str | None = None

        while True:
            variables: dict[str, Any] = {"first": MAX_PAGE_SIZE}
            if query_filter:
                variables["query"] = query_filter
            if cursor:
                variables["after"] = cursor

            data = await self._execute_graphql(
                GRAPHQL_UNFULFILLED_ORDERS_QUERY,
                variables,
            )

            orders = data.get("orders", {})
            edges = orders.get("edges", [])
            count += len(edges)

            page_info = orders.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        self._total_orders_cache = count
        self._total_orders_last_update = now
        return count

    def _get_today_boundaries(self) -> tuple[datetime, datetime]:
        """Get the start and end of today."""
        tz = self._get_timezone()
        now = datetime.now(tz)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return today_start, now

    def _get_week_boundaries(self) -> tuple[datetime, datetime, int]:
        """Get the start and end of the current week (Monday-based)."""
        tz = self._get_timezone()
        now = datetime.now(tz)
        # Monday is 0, Sunday is 6
        days_since_monday = now.weekday()
        week_start = (now - timedelta(days=days_since_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week_number = now.isocalendar()[1]
        return week_start, now, week_number

    def _get_year_start(self) -> datetime:
        """Get the start of the current year."""
        tz = self._get_timezone()
        now = datetime.now(tz)
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    def _get_30_days_ago(self) -> datetime:
        """Get the datetime 30 days ago."""
        tz = self._get_timezone()
        now = datetime.now(tz)
        return (now - timedelta(days=30)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    async def get_today_data(self) -> tuple[int, Decimal, datetime, datetime]:
        """Get today's orders count and revenue.

        Returns:
            Tuple of (order_count, revenue, today_start, today_end)
        """
        today_start, today_end = self._get_today_boundaries()

        query_filter = self._build_order_query(
            financial_filter="paid",
            created_after=today_start,
            created_before=today_end,
        )

        total_revenue = Decimal("0.00")
        order_count = 0
        cursor: str | None = None

        while True:
            variables = {
                "first": MAX_PAGE_SIZE,
                "query": query_filter,
            }
            if cursor:
                variables["after"] = cursor

            data = await self._execute_graphql(
                GRAPHQL_ORDERS_REVENUE_QUERY,
                variables,
            )

            orders = data.get("orders", {})
            edges = orders.get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                if node.get("cancelledAt"):
                    continue
                net_revenue = self._extract_order_revenue(node)
                if net_revenue > 0:
                    total_revenue += net_revenue
                    order_count += 1

            page_info = orders.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return order_count, total_revenue, today_start, today_end

    async def get_week_data(self) -> tuple[int, Decimal, datetime, datetime, int]:
        """Get this week's orders count and revenue (Monday-based).

        Returns:
            Tuple of (order_count, revenue, week_start, week_end, week_number)
        """
        week_start, week_end, week_number = self._get_week_boundaries()

        query_filter = self._build_order_query(
            financial_filter="paid",
            created_after=week_start,
            created_before=week_end,
        )

        total_revenue = Decimal("0.00")
        order_count = 0
        cursor: str | None = None

        while True:
            variables = {
                "first": MAX_PAGE_SIZE,
                "query": query_filter,
            }
            if cursor:
                variables["after"] = cursor

            data = await self._execute_graphql(
                GRAPHQL_ORDERS_REVENUE_QUERY,
                variables,
            )

            orders = data.get("orders", {})
            edges = orders.get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                if node.get("cancelledAt"):
                    continue
                net_revenue = self._extract_order_revenue(node)
                if net_revenue > 0:
                    total_revenue += net_revenue
                    order_count += 1

            page_info = orders.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return order_count, total_revenue, week_start, week_end, week_number

    async def get_pending_payment_orders_count(self) -> int:
        """Get count of orders with pending payment."""
        query_filter = self._build_order_query(
            status_filter="open",
            financial_filter="pending",
        )

        try:
            data = await self._execute_graphql(
                GRAPHQL_ORDERS_COUNT_QUERY,
                {"query": query_filter},
            )
            if data.get("ordersCount"):
                return data["ordersCount"]["count"]
        except ShopifyAPIError:
            pass

        # Fallback: count via pagination
        count = 0
        cursor: str | None = None

        while True:
            variables = {
                "first": DEFAULT_PAGE_SIZE,
                "query": query_filter,
            }
            if cursor:
                variables["after"] = cursor

            data = await self._execute_graphql(
                GRAPHQL_UNFULFILLED_ORDERS_QUERY,
                variables,
            )

            orders = data.get("orders", {})
            edges = orders.get("edges", [])
            count += len(edges)

            page_info = orders.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return count

    async def get_partially_fulfilled_orders_count(self) -> int:
        """Get count of partially fulfilled orders."""
        query_filter = self._build_order_query(
            status_filter="open",
            fulfillment_filter="partial",
            financial_filter="paid",
        )

        try:
            data = await self._execute_graphql(
                GRAPHQL_ORDERS_COUNT_QUERY,
                {"query": query_filter},
            )
            if data.get("ordersCount"):
                return data["ordersCount"]["count"]
        except ShopifyAPIError:
            pass

        # Fallback: count via pagination
        count = 0
        cursor: str | None = None

        while True:
            variables = {
                "first": DEFAULT_PAGE_SIZE,
                "query": query_filter,
            }
            if cursor:
                variables["after"] = cursor

            data = await self._execute_graphql(
                GRAPHQL_UNFULFILLED_ORDERS_QUERY,
                variables,
            )

            orders = data.get("orders", {})
            edges = orders.get("edges", [])
            count += len(edges)

            page_info = orders.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return count

    async def get_ytd_revenue(self) -> tuple[Decimal, int, datetime]:
        """Get year-to-date revenue.

        Returns:
            Tuple of (revenue, order_count, year_start)
        """
        year_start = self._get_year_start()
        tz = self._get_timezone()
        now = datetime.now(tz)

        query_filter = self._build_order_query(
            financial_filter="paid",
            created_after=year_start,
            created_before=now,
        )

        total_revenue = Decimal("0.00")
        order_count = 0
        cursor: str | None = None

        while True:
            variables = {
                "first": MAX_PAGE_SIZE,
                "query": query_filter,
            }
            if cursor:
                variables["after"] = cursor

            data = await self._execute_graphql(
                GRAPHQL_ORDERS_REVENUE_QUERY,
                variables,
            )

            orders = data.get("orders", {})
            edges = orders.get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                if node.get("cancelledAt"):
                    continue
                net_revenue = self._extract_order_revenue(node)
                if net_revenue > 0:
                    total_revenue += net_revenue
                    order_count += 1

            page_info = orders.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return total_revenue, order_count, year_start

    async def get_last_30_days_data(self) -> tuple[int, Decimal, datetime]:
        """Get last 30 days orders count and revenue.

        Returns:
            Tuple of (order_count, revenue, start_date)
        """
        start_date = self._get_30_days_ago()
        tz = self._get_timezone()
        now = datetime.now(tz)

        query_filter = self._build_order_query(
            financial_filter="paid",
            created_after=start_date,
            created_before=now,
        )

        total_revenue = Decimal("0.00")
        order_count = 0
        cursor: str | None = None

        while True:
            variables = {
                "first": MAX_PAGE_SIZE,
                "query": query_filter,
            }
            if cursor:
                variables["after"] = cursor

            data = await self._execute_graphql(
                GRAPHQL_ORDERS_REVENUE_QUERY,
                variables,
            )

            orders = data.get("orders", {})
            edges = orders.get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                if node.get("cancelledAt"):
                    continue
                net_revenue = self._extract_order_revenue(node)
                if net_revenue > 0:
                    total_revenue += net_revenue
                    order_count += 1

            page_info = orders.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return order_count, total_revenue, start_date

    async def get_busiest_month(
        self, months_lookback: int = 24
    ) -> tuple[str | None, Decimal, int]:
        """Get the busiest month by revenue.

        Args:
            months_lookback: Number of months to look back

        Returns:
            Tuple of (month_str "YYYY-MM", revenue, order_count)
        """
        tz = self._get_timezone()
        now = datetime.now(tz)

        # Calculate lookback date
        lookback_date = now - timedelta(days=months_lookback * 30)
        lookback_date = lookback_date.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        query_filter = self._build_order_query(
            financial_filter="paid",
            created_after=lookback_date,
        )

        # Aggregate by month
        monthly_data: dict[str, MonthlyRevenue] = {}
        cursor: str | None = None

        while True:
            variables = {
                "first": MAX_PAGE_SIZE,
                "query": query_filter,
            }
            if cursor:
                variables["after"] = cursor

            data = await self._execute_graphql(
                GRAPHQL_ORDERS_REVENUE_QUERY,
                variables,
            )

            orders = data.get("orders", {})
            edges = orders.get("edges", [])

            for edge in edges:
                node = edge.get("node", {})

                # Skip cancelled orders
                if node.get("cancelledAt"):
                    continue

                # Parse created date
                created_str = node.get("createdAt", "")
                try:
                    created_at = datetime.fromisoformat(
                        created_str.replace("Z", "+00:00")
                    )
                    created_local = created_at.astimezone(tz)
                    month_key = created_local.strftime("%Y-%m")
                except (ValueError, TypeError):
                    continue

                # Get net revenue using API 2026-01 compatible method
                net_revenue = self._extract_order_revenue(node)

                if net_revenue <= 0:
                    continue

                # Aggregate
                if month_key not in monthly_data:
                    monthly_data[month_key] = MonthlyRevenue(
                        month=month_key,
                        revenue_aud=Decimal("0.00"),
                        order_count=0,
                    )

                monthly_data[month_key].revenue_aud += net_revenue
                monthly_data[month_key].order_count += 1

            page_info = orders.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        # Find busiest month
        if not monthly_data:
            return None, Decimal("0.00"), 0

        busiest = max(monthly_data.values(), key=lambda m: m.revenue_aud)
        return busiest.month, busiest.revenue_aud, busiest.order_count

    async def fetch_all_data(
        self, months_lookback: int = 24
    ) -> ShopifyData:
        """Fetch all data for sensors.

        This is the main method called by the coordinator.
        """
        result = ShopifyData()
        result.last_sync = datetime.now()

        # Ensure we have shop info
        if not self._shop_info:
            await self.test_connection()
        result.shop_info = self._shop_info
        result.currency = TARGET_CURRENCY

        # Fetch all data concurrently where possible
        # Original 4 sensors
        unfulfilled_task = asyncio.create_task(
            self.get_unfulfilled_orders_count()
        )
        revenue_task = asyncio.create_task(
            self.get_current_month_revenue()
        )
        total_task = asyncio.create_task(
            self.get_total_orders_count()
        )
        busiest_task = asyncio.create_task(
            self.get_busiest_month(months_lookback)
        )

        # New 10 sensors
        today_task = asyncio.create_task(
            self.get_today_data()
        )
        week_task = asyncio.create_task(
            self.get_week_data()
        )
        pending_task = asyncio.create_task(
            self.get_pending_payment_orders_count()
        )
        partial_task = asyncio.create_task(
            self.get_partially_fulfilled_orders_count()
        )
        ytd_task = asyncio.create_task(
            self.get_ytd_revenue()
        )
        last_30_task = asyncio.create_task(
            self.get_last_30_days_data()
        )

        # Wait for all with error handling - Original sensors
        try:
            result.unfulfilled_orders_count = await unfulfilled_task
        except Exception as err:
            _LOGGER.error("Failed to get unfulfilled orders: %s", err)
            result.unfulfilled_orders_count = 0

        try:
            revenue, count, start, end = await revenue_task
            result.current_month_revenue_aud = revenue
            result.current_month_order_count = count
            result.month_start = start
            result.month_end = end
            # Calculate average order value
            if count > 0:
                result.average_order_value_aud = revenue / count
        except Exception as err:
            _LOGGER.error("Failed to get current month revenue: %s", err)

        try:
            result.total_orders_count = await total_task
        except Exception as err:
            _LOGGER.error("Failed to get total orders count: %s", err)
            result.total_orders_count = 0

        try:
            month, revenue, count = await busiest_task
            result.busiest_month = month
            result.busiest_month_revenue_aud = revenue
            result.busiest_month_order_count = count
        except Exception as err:
            _LOGGER.error("Failed to get busiest month: %s", err)

        # Wait for new sensors
        try:
            count, revenue, start, end = await today_task
            result.today_orders_count = count
            result.today_revenue_aud = revenue
            result.today_start = start
            result.today_end = end
        except Exception as err:
            _LOGGER.error("Failed to get today's data: %s", err)

        try:
            count, revenue, start, end, week_num = await week_task
            result.week_orders_count = count
            result.week_revenue_aud = revenue
            result.week_start = start
            result.week_end = end
            result.week_number = week_num
        except Exception as err:
            _LOGGER.error("Failed to get week data: %s", err)

        try:
            result.pending_payment_orders_count = await pending_task
        except Exception as err:
            _LOGGER.error("Failed to get pending payment orders: %s", err)

        try:
            result.partially_fulfilled_orders_count = await partial_task
        except Exception as err:
            _LOGGER.error("Failed to get partially fulfilled orders: %s", err)

        try:
            revenue, count, year_start = await ytd_task
            result.ytd_revenue_aud = revenue
            result.ytd_order_count = count
            result.year_start = year_start
        except Exception as err:
            _LOGGER.error("Failed to get YTD revenue: %s", err)

        try:
            count, revenue, start = await last_30_task
            result.last_30_days_orders_count = count
            result.last_30_days_revenue_aud = revenue
            result.last_30_days_start = start
        except Exception as err:
            _LOGGER.error("Failed to get last 30 days data: %s", err)

        # Track API calls remaining
        result.api_calls_remaining = int(self._available_points)

        return result
