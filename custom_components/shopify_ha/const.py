"""Constants for the Shopify Store Integration."""
from __future__ import annotations

from typing import Final

# Domain
DOMAIN: Final = "shopify_ha"

# Configuration keys
CONF_SHOP_DOMAIN: Final = "shop_domain"
CONF_ACCESS_TOKEN: Final = "access_token"
CONF_API_VERSION: Final = "api_version"
CONF_TIMEZONE_OVERRIDE: Final = "timezone_override"
CONF_INCLUDE_TEST_ORDERS: Final = "include_test_orders"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_MONTHS_LOOKBACK: Final = "months_lookback"
CONF_MOCK_MODE: Final = "mock_mode"
CONF_GRANTED_SCOPES: Final = "granted_scopes"

# OAuth2 Authorization Code Flow Configuration
# Note: Shopify tokens are long-lived and do not require refresh
OAUTH2_AUTHORIZE_URL_TEMPLATE: Final = "https://{shop}/admin/oauth/authorize"
OAUTH2_TOKEN_URL_TEMPLATE: Final = "https://{shop}/admin/oauth/access_token"
OAUTH2_SCOPES: Final = ["read_orders"]
# OAuth callback path - will be appended to HA's external URL
OAUTH2_CALLBACK_PATH: Final = "/auth/external/callback"

# Defaults
DEFAULT_API_VERSION: Final = "2026-01"
DEFAULT_SCAN_INTERVAL: Final = 15  # minutes
DEFAULT_MONTHS_LOOKBACK: Final = 24
DEFAULT_INCLUDE_TEST_ORDERS: Final = False
DEFAULT_MOCK_MODE: Final = False

# API Constants
SHOPIFY_GRAPHQL_ENDPOINT: Final = "admin/api/{version}/graphql.json"
SHOPIFY_API_RATE_LIMIT_HEADER: Final = "X-Shopify-Shop-Api-Call-Limit"
GRAPHQL_COST_HEADER: Final = "X-GraphQL-Cost-Include-Fields"

# GraphQL page sizes
DEFAULT_PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 250

# Sensor types - Original 4
SENSOR_UNFULFILLED_ORDERS: Final = "unfulfilled_orders_count"
SENSOR_CURRENT_MONTH_REVENUE: Final = "current_month_revenue_aud"
SENSOR_TOTAL_ORDERS: Final = "total_orders_count"
SENSOR_BUSIEST_MONTH: Final = "busiest_month"

# Sensor types - New 10 sensors
SENSOR_TODAY_ORDERS: Final = "today_orders_count"
SENSOR_TODAY_REVENUE: Final = "today_revenue_aud"
SENSOR_WEEK_ORDERS: Final = "this_week_orders_count"
SENSOR_WEEK_REVENUE: Final = "this_week_revenue_aud"
SENSOR_AVERAGE_ORDER_VALUE: Final = "average_order_value_aud"
SENSOR_PENDING_PAYMENT_ORDERS: Final = "pending_payment_orders_count"
SENSOR_PARTIALLY_FULFILLED_ORDERS: Final = "partially_fulfilled_orders_count"
SENSOR_YTD_REVENUE: Final = "year_to_date_revenue_aud"
SENSOR_LAST_30_DAYS_ORDERS: Final = "last_30_days_orders_count"
SENSOR_LAST_30_DAYS_REVENUE: Final = "last_30_days_revenue_aud"

# Attributes
ATTR_CURRENCY: Final = "currency"
ATTR_START_DATE: Final = "start_date"
ATTR_END_DATE: Final = "end_date"
ATTR_ORDER_COUNT: Final = "order_count"
ATTR_LAST_SYNC: Final = "last_sync"
ATTR_API_CALLS_REMAINING: Final = "api_calls_remaining"
ATTR_MONTH: Final = "month"
ATTR_REVENUE_AUD: Final = "revenue_aud"
ATTR_STORE_NAME: Final = "store_name"
ATTR_SHOP_DOMAIN: Final = "shop_domain"
ATTR_PERIOD: Final = "period"
ATTR_YEAR: Final = "year"
ATTR_WEEK_NUMBER: Final = "week_number"

# Error messages
ERROR_INVALID_AUTH: Final = "invalid_auth"
ERROR_CANNOT_CONNECT: Final = "cannot_connect"
ERROR_INVALID_DOMAIN: Final = "invalid_domain"
ERROR_UNKNOWN: Final = "unknown"
ERROR_RATE_LIMITED: Final = "rate_limited"

# Currency
TARGET_CURRENCY: Final = "AUD"

# Coordinator data keys
DATA_COORDINATOR: Final = "coordinator"
DATA_API_CLIENT: Final = "api_client"

# Platforms
PLATFORMS: Final = ["sensor"]

# Minimum HA version
MIN_HA_VERSION: Final = "2024.12.0"

# GraphQL Queries
GRAPHQL_ORDERS_COUNT_QUERY: Final = """
query OrdersCount($query: String) {
  ordersCount(query: $query) {
    count
  }
}
"""

# Corrected for API 2026-01: Use displayFulfillmentStatus instead of fulfillmentStatus
GRAPHQL_UNFULFILLED_ORDERS_QUERY: Final = """
query UnfulfilledOrders($first: Int!, $after: String, $query: String) {
  orders(first: $first, after: $after, query: $query) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        name
        createdAt
        displayFulfillmentStatus
        displayFinancialStatus
      }
    }
  }
}
"""

# Revenue query for API 2026-01
# Uses currentTotalPriceSet (reflects edits/returns/refunds) for accurate net revenue
# Prefer shopMoney for consistent store currency
GRAPHQL_ORDERS_REVENUE_QUERY: Final = """
query OrdersRevenue($first: Int!, $after: String, $query: String) {
  orders(first: $first, after: $after, query: $query) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        name
        createdAt
        displayFinancialStatus
        displayFulfillmentStatus
        cancelledAt
        closed
        currentTotalPriceSet {
          shopMoney {
            amount
            currencyCode
          }
        }
        netPaymentSet {
          shopMoney {
            amount
            currencyCode
          }
        }
        totalRefundedSet {
          shopMoney {
            amount
            currencyCode
          }
        }
      }
    }
  }
}
"""

GRAPHQL_SHOP_INFO_QUERY: Final = """
query ShopInfo {
  shop {
    name
    currencyCode
    ianaTimezone
    plan {
      displayName
    }
  }
}
"""

# Throttle settings
THROTTLE_RESTORE_RATE: Final = 50.0  # points per second
THROTTLE_MAX_COST: Final = 1000  # maximum available points
THROTTLE_MIN_AVAILABLE: Final = 100  # minimum points before backing off
