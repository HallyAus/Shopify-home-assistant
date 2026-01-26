# Shopify Home Assistant Integration

[![HACS Custom][hacs-badge]][hacs-url]
[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)

A custom Home Assistant integration that connects to the Shopify Admin API and exposes sensors for monitoring your store's orders and revenue.

## Features

- **Unfulfilled Orders Count**: Track orders that are paid but not yet fulfilled
- **Current Month Revenue (AUD)**: Monitor your store's revenue for the current month
- **Total Orders Count**: See your all-time order count
- **Busiest Month**: Identify your highest revenue month with detailed statistics

### Key Capabilities

- Uses Shopify's GraphQL Admin API for efficient data fetching
- Supports multiple stores (add multiple config entries)
- Configurable update interval (default: 15 minutes)
- Smart caching to minimize API calls
- Automatic rate limiting handling
- Mock mode for testing without real API calls
- Full diagnostics support with token redaction

## Requirements

- Home Assistant 2024.12 or newer
- A Shopify store with Admin API access
- A custom app access token with `read_orders` scope

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add this repository URL: `https://github.com/HallyAus/Shopify-home-assistant`
4. Select category: **Integration**
5. Click **Add**
6. Search for "Shopify Store" and install it
7. Restart Home Assistant

### Manual Installation

1. Download the `custom_components/shopify_ha` folder from this repository
2. Copy it to your Home Assistant's `custom_components` directory
3. Restart Home Assistant

## Creating a Shopify Custom App

To use this integration, you need to create a custom app in your Shopify admin:

### Step 1: Create the App

1. Log in to your Shopify admin panel
2. Go to **Settings** → **Apps and sales channels**
3. Click **Develop apps** (you may need to enable this first)
4. Click **Create an app**
5. Name your app (e.g., "Home Assistant Integration")
6. Click **Create app**

### Step 2: Configure API Scopes

1. In your new app, click **Configure Admin API scopes**
2. Find and enable **`read_orders`** (under Orders section)
3. Optionally enable **`read_products`** if you want future product sensors
4. Click **Save**

### Step 3: Get Your Access Token

1. Click **Install app** to install it on your store
2. Click **Reveal token once** to see your Admin API access token
3. **IMPORTANT**: Copy and save this token securely - it's only shown once!

### Required Scopes

| Scope | Purpose |
|-------|---------|
| `read_orders` | Required for all sensors (orders, revenue, etc.) |

## Configuration

### Adding the Integration

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "Shopify Store"
4. Enter your configuration:
   - **Shop Domain**: Your store's domain (e.g., `my-store.myshopify.com` or just `my-store`)
   - **Admin API Access Token**: The token from your custom app
   - **API Version**: Leave as default unless you have specific requirements
   - **Timezone Override**: Optional - override the timezone for date calculations
   - **Include Test Orders**: Whether to include test orders in calculations
   - **Mock Mode**: Use mock data for testing (no real API calls)

### Options (Configurable After Setup)

Access options via the integration's **Configure** button:

| Option | Default | Description |
|--------|---------|-------------|
| Update Interval | 15 minutes | How often to fetch data from Shopify |
| Months Lookback | 24 months | Number of months to analyze for busiest month |
| Include Test Orders | Off | Include test orders in calculations |
| Timezone Override | Store timezone | Override timezone for date boundaries |

## Sensors

### sensor.\<store\>_shopify_unfulfilled_orders_count

**State**: Number of unfulfilled paid orders

**Attributes**:
- `store_name`: Your store's name
- `shop_domain`: Your store's domain
- `last_sync`: Timestamp of last data update
- `api_calls_remaining`: Remaining API capacity

### sensor.\<store\>_shopify_current_month_revenue_aud

**State**: Current month's revenue in AUD

**Attributes**:
- `currency`: "AUD"
- `start_date`: First day of current month
- `end_date`: Current timestamp
- `order_count`: Number of orders included
- `store_name`: Your store's name
- `last_sync`: Timestamp of last data update
- `api_calls_remaining`: Remaining API capacity

### sensor.\<store\>_shopify_total_orders_count

**State**: Total number of orders (all time)

**Attributes**:
- `store_name`: Your store's name
- `shop_domain`: Your store's domain
- `last_sync`: Timestamp of last data update
- `api_calls_remaining`: Remaining API capacity

### sensor.\<store\>_shopify_busiest_month

**State**: The busiest month in "YYYY-MM" format

**Attributes**:
- `month`: Month in "YYYY-MM" format
- `revenue_aud`: Revenue for that month
- `order_count`: Number of orders in that month
- `currency`: "AUD"
- `store_name`: Your store's name
- `last_sync`: Timestamp of last data update

## Troubleshooting

### Error: Invalid access token or insufficient permissions (401/403)

**Cause**: The access token is invalid or doesn't have the required scopes.

**Solution**:
1. Verify your access token is correct
2. Ensure the custom app has `read_orders` scope enabled
3. Reinstall the app in Shopify if you recently changed scopes
4. Generate a new access token if needed

### Error: Unable to connect to Shopify

**Cause**: Network issues or incorrect shop domain.

**Solution**:
1. Verify your shop domain is correct
2. Check your internet connection
3. Ensure Shopify's services are operational
4. Try accessing your Shopify admin directly to verify

### Error: Rate limited by Shopify

**Cause**: Too many API requests in a short time.

**Solution**:
1. Increase the update interval in options (e.g., 30 minutes)
2. The integration will automatically back off and retry
3. Wait a few minutes for rate limits to reset

### Currency shows non-AUD values

**Cause**: Your Shopify store uses a different currency.

**Limitation**: This integration targets AUD currency. If your store uses a different currency:
- The raw amounts will still be shown (not converted)
- A warning will be logged
- Consider this a limitation for non-AUD stores

**Workaround**: If you need multi-currency support, please open an issue with your use case.

### Timezone issues

**Cause**: Revenue calculations use incorrect timezone.

**Solution**:
1. Set the "Timezone Override" option to your desired timezone
2. Use IANA timezone names (e.g., `Australia/Sydney`, `America/New_York`)
3. If not set, the integration uses your store's timezone from Shopify

### Sensors show "Unknown" or no data

**Cause**: Initial data hasn't loaded or there was an error.

**Solution**:
1. Check the Home Assistant logs for errors
2. Verify your access token has the correct permissions
3. Try removing and re-adding the integration
4. Enable debug logging for more details

## Debugging

### Enable Debug Logging

Add to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.shopify_ha: debug
```

### GraphQL API Testing

You can test your token with curl:

```bash
# Test shop info
curl -X POST \
  "https://YOUR-STORE.myshopify.com/admin/api/2024-10/graphql.json" \
  -H "Content-Type: application/json" \
  -H "X-Shopify-Access-Token: YOUR_ACCESS_TOKEN" \
  -d '{"query": "{ shop { name currencyCode ianaTimezone } }"}'

# Test orders count
curl -X POST \
  "https://YOUR-STORE.myshopify.com/admin/api/2024-10/graphql.json" \
  -H "Content-Type: application/json" \
  -H "X-Shopify-Access-Token: YOUR_ACCESS_TOKEN" \
  -d '{"query": "{ ordersCount { count } }"}'

# Test unfulfilled orders
curl -X POST \
  "https://YOUR-STORE.myshopify.com/admin/api/2024-10/graphql.json" \
  -H "Content-Type: application/json" \
  -H "X-Shopify-Access-Token: YOUR_ACCESS_TOKEN" \
  -d '{"query": "{ orders(first: 10, query: \"fulfillment_status:unfulfilled AND financial_status:paid\") { edges { node { name createdAt } } } }"}'
```

Replace `YOUR-STORE` with your shop domain and `YOUR_ACCESS_TOKEN` with your token.

### Diagnostics

1. Go to **Settings** → **Devices & Services**
2. Find the Shopify integration and click on it
3. Click the three dots menu → **Download diagnostics**

The diagnostics file includes:
- Configuration (with token redacted)
- Current sensor data
- Shop information
- Coordinator status

## API Usage

This integration uses Shopify's GraphQL Admin API. Here's what happens during each update:

1. **Unfulfilled Orders**: Single count query or paginated fetch
2. **Current Month Revenue**: Paginated query for orders in date range
3. **Total Orders**: Cached count query (refreshed daily)
4. **Busiest Month**: Paginated query for historical data

The integration implements:
- Automatic rate limiting with backoff
- Request caching to minimize API calls
- Concurrent requests where safe
- Efficient pagination

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Disclaimer

This is an unofficial integration and is not affiliated with or endorsed by Shopify. Use at your own risk.

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-orange.svg
[hacs-url]: https://github.com/hacs/integration
[releases-shield]: https://img.shields.io/github/release/HallyAus/Shopify-home-assistant.svg
[releases]: https://github.com/HallyAus/Shopify-home-assistant/releases
[license-shield]: https://img.shields.io/github/license/HallyAus/Shopify-home-assistant.svg
