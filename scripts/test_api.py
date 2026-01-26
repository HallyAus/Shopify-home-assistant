#!/usr/bin/env python3
"""
Test script for Shopify API client.

This script allows you to test the Shopify GraphQL API client
without running Home Assistant.

Usage:
    python scripts/test_api.py --shop your-store.myshopify.com --token YOUR_TOKEN
    python scripts/test_api.py --mock  # Test with mock data

Environment variables (alternative to CLI args):
    SHOPIFY_SHOP_DOMAIN: Your store domain
    SHOPIFY_ACCESS_TOKEN: Your admin API access token
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Add the custom_components to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.shopify_ha.api import (
    ShopifyGraphQLClient,
    ShopifyAuthError,
    ShopifyConnectionError,
)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
_LOGGER = logging.getLogger(__name__)


async def test_client(shop_domain: str, access_token: str, mock_mode: bool = False):
    """Test the Shopify API client."""
    print(f"\n{'='*60}")
    print(f"Testing Shopify API Client")
    print(f"Shop: {shop_domain}")
    print(f"Mock Mode: {mock_mode}")
    print(f"{'='*60}\n")

    client = ShopifyGraphQLClient(
        shop_domain=shop_domain,
        access_token=access_token,
        mock_mode=mock_mode,
    )

    try:
        # Test 1: Connection and shop info
        print("1. Testing connection...")
        try:
            shop_info = await client.test_connection()
            print(f"   SUCCESS! Connected to: {shop_info.name}")
            print(f"   Currency: {shop_info.currency_code}")
            print(f"   Timezone: {shop_info.timezone}")
            print(f"   Plan: {shop_info.plan_name}")
        except ShopifyAuthError as err:
            print(f"   FAILED! Authentication error: {err}")
            return
        except ShopifyConnectionError as err:
            print(f"   FAILED! Connection error: {err}")
            return

        # Test 2: Unfulfilled orders count
        print("\n2. Getting unfulfilled orders count...")
        try:
            count = await client.get_unfulfilled_orders_count()
            print(f"   Unfulfilled orders: {count}")
        except Exception as err:
            print(f"   FAILED! Error: {err}")

        # Test 3: Current month revenue
        print("\n3. Getting current month revenue...")
        try:
            revenue, order_count, month_start, month_end = await client.get_current_month_revenue()
            print(f"   Revenue: ${revenue:.2f} AUD")
            print(f"   Order count: {order_count}")
            print(f"   Period: {month_start.date()} to {month_end.date()}")
        except Exception as err:
            print(f"   FAILED! Error: {err}")

        # Test 4: Total orders count
        print("\n4. Getting total orders count...")
        try:
            total = await client.get_total_orders_count()
            print(f"   Total orders: {total}")
        except Exception as err:
            print(f"   FAILED! Error: {err}")

        # Test 5: Busiest month
        print("\n5. Getting busiest month...")
        try:
            month, revenue, order_count = await client.get_busiest_month(months_lookback=12)
            if month:
                print(f"   Busiest month: {month}")
                print(f"   Revenue: ${revenue:.2f} AUD")
                print(f"   Orders: {order_count}")
            else:
                print("   No data available")
        except Exception as err:
            print(f"   FAILED! Error: {err}")

        # Test 6: Full data fetch
        print("\n6. Testing full data fetch...")
        try:
            data = await client.fetch_all_data(months_lookback=12)
            print(f"   SUCCESS! All data fetched.")
            print(f"   API calls remaining: {data.api_calls_remaining}")
        except Exception as err:
            print(f"   FAILED! Error: {err}")

    finally:
        await client.close()

    print(f"\n{'='*60}")
    print("All tests completed!")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Test Shopify API client")
    parser.add_argument(
        "--shop",
        default=os.environ.get("SHOPIFY_SHOP_DOMAIN", ""),
        help="Shop domain (e.g., my-store.myshopify.com)"
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("SHOPIFY_ACCESS_TOKEN", ""),
        help="Admin API access token"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock mode (no real API calls)"
    )

    args = parser.parse_args()

    if args.mock:
        shop_domain = "mock-store.myshopify.com"
        access_token = "mock-token"
    elif not args.shop or not args.token:
        print("Error: Either --mock flag or both --shop and --token are required")
        print("You can also set SHOPIFY_SHOP_DOMAIN and SHOPIFY_ACCESS_TOKEN env vars")
        sys.exit(1)
    else:
        shop_domain = args.shop
        access_token = args.token

    asyncio.run(test_client(shop_domain, access_token, mock_mode=args.mock))


if __name__ == "__main__":
    main()
