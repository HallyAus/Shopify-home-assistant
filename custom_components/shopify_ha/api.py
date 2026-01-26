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

    unfulfilled_orders_count: int = 0
    current_month_revenue_aud: Decimal = field(default_factory=lambda: Decimal("0.00"))
    current_month_order_count: int = 0
    total_orders_count: int = 0
    busiest_month: str | None = None
    busiest_month_revenue_aud: Decimal = field(default_factory=lambda: Decimal("0.00"))
    busiest_month_order_count: int = 0
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
                                "fulfillmentStatus": "UNFULFILLED",
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
                                "cancelledAt": None,
                                "currentTotalPriceSet": {
                                    "presentmentMoney": {
                                        "amount": str(100 + i * 50),
                                        "currencyCode": "AUD",
                                    },
                                    "shopMoney": {
                                        "amount": str(100 + i * 50),
                                        "currencyCode": "AUD",
                                    },
                                },
                                "totalRefundedSet": {
                                    "presentmentMoney": {
                                        "amount": "0.00",
                                        "currencyCode": "AUD",
                                    },
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
        """Build Shopify order query string."""
        parts = []

        if not self._include_test_orders:
            parts.append("-test:true")

        if status_filter:
            parts.append(f"status:{status_filter}")

        if fulfillment_filter:
            parts.append(f"fulfillment_status:{fulfillment_filter}")

        if financial_filter:
            parts.append(f"financial_status:{financial_filter}")

        if created_after:
            parts.append(f"created_at:>={created_after.isoformat()}")

        if created_before:
            parts.append(f"created_at:<={created_before.isoformat()}")

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

                # Skip cancelled orders
                if node.get("cancelledAt"):
                    continue

                # Get total price
                price_set = node.get("currentTotalPriceSet", {})
                money = self._extract_aud_amount(price_set)

                # Subtract refunds
                refund_set = node.get("totalRefundedSet", {})
                refunded = self._extract_aud_amount(refund_set)

                net_revenue = money - refunded
                if net_revenue > 0:
                    total_revenue += net_revenue
                    order_count += 1

            page_info = orders.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return total_revenue, order_count, month_start, month_end

    def _extract_aud_amount(self, price_set: dict[str, Any]) -> Decimal:
        """Extract AUD amount from a MoneyBag, preferring presentment."""
        # Try presentment money first (customer's currency)
        presentment = price_set.get("presentmentMoney", {})
        if presentment.get("currencyCode") == TARGET_CURRENCY:
            try:
                return Decimal(presentment.get("amount", "0"))
            except (ValueError, TypeError):
                pass

        # Fall back to shop money
        shop = price_set.get("shopMoney", {})
        if shop.get("currencyCode") == TARGET_CURRENCY:
            try:
                return Decimal(shop.get("amount", "0"))
            except (ValueError, TypeError):
                pass

        # If neither is AUD, log warning and use shop money
        # (User should be aware of currency mismatch)
        shop_amount = shop.get("amount", "0")
        shop_currency = shop.get("currencyCode", "UNKNOWN")
        if shop_currency != TARGET_CURRENCY:
            _LOGGER.warning(
                "Currency mismatch: shop uses %s, not %s. "
                "Using shop amount as-is. Consider using a store with AUD currency.",
                shop_currency,
                TARGET_CURRENCY,
            )

        try:
            return Decimal(shop_amount)
        except (ValueError, TypeError):
            return Decimal("0.00")

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

                # Skip cancelled
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

                # Get revenue
                price_set = node.get("currentTotalPriceSet", {})
                amount = self._extract_aud_amount(price_set)

                refund_set = node.get("totalRefundedSet", {})
                refunded = self._extract_aud_amount(refund_set)

                net_revenue = amount - refunded
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

        # Wait for all with error handling
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

        # Track API calls remaining
        result.api_calls_remaining = int(self._available_points)

        return result
