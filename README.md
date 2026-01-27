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
- **Two authentication methods**: OAuth (recommended) or manual access token
- Supports multiple stores (add multiple config entries)
- Configurable update interval (default: 15 minutes)
- Smart caching to minimize API calls
- Automatic rate limiting handling
- Mock mode for testing without real API calls
- Full diagnostics support with credential redaction

## Requirements

- Home Assistant 2024.12 or newer
- A Shopify store with Admin API access
- Either:
  - A Shopify Partners app with Client ID and Client Secret (OAuth method), OR
  - A custom app access token with `read_orders` scope (manual method)

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

## Authentication Methods

This integration supports two authentication methods:

### Method 1: OAuth (Recommended)

OAuth is the recommended method as it's more secure and follows Shopify's best practices.

#### Step 1: Create a Shopify Partners App

1. Go to [Shopify Partners](https://partners.shopify.com/) and log in
2. Click **Apps** → **Create app**
3. Choose **Create app manually**
4. Enter an app name (e.g., "Home Assistant Integration")
5. Click **Create**

#### Step 2: Configure the App

1. In your app settings, go to **Configuration**
2. Set the **App URL** to your Home Assistant external URL (e.g., `https://homeassistant.yourdomain.com`)
3. Add **Allowed redirection URL(s)**:
   - `https://homeassistant.yourdomain.com/auth/external/callback`
4. Under **Access scopes**, add:
   - `read_orders`
5. Click **Save**

#### Step 3: Get Client Credentials

1. Go to **Client credentials** in your app
2. Copy the **Client ID**
3. Copy the **Client secret**

#### Step 4: Install the App on Your Store

1. In the app settings, go to **Test your app**
2. Select your development store
3. Click **Install app**

#### Step 5: Configure Home Assistant

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "Shopify Store"
4. Select **OAuth (Recommended)**
5. Enter:
   - **Shop Domain**: Your store domain (e.g., `my-store` or `my-store.myshopify.com`)
   - **Client ID**: From Shopify Partners
   - **Client Secret**: From Shopify Partners
6. You'll be redirected to Shopify to authorize
7. Approve the permissions
8. You'll be redirected back to Home Assistant

### Method 2: Manual Access Token

Use this method if you prefer a simpler setup or don't have access to Shopify Partners.

#### Step 1: Create a Custom App in Shopify Admin

1. Log in to your Shopify admin panel
2. Go to **Settings** → **Apps and sales channels**
3. Click **Develop apps** (you may need to enable this first)
4. Click **Create an app**
5. Name your app (e.g., "Home Assistant Integration")
6. Click **Create app**

#### Step 2: Configure API Scopes

1. In your new app, click **Configure Admin API scopes**
2. Find and enable **`read_orders`** (under Orders section)
3. Click **Save**

#### Step 3: Get Your Access Token

1. Click **Install app** to install it on your store
2. Click **Reveal token once** to see your Admin API access token
3. **IMPORTANT**: Copy and save this token securely - it's only shown once!

#### Step 4: Configure Home Assistant

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "Shopify Store"
4. Select **Manual Access Token**
5. Enter:
   - **Shop Domain**: Your store domain (e.g., `my-store` or `my-store.myshopify.com`)
   - **Admin API Access Token**: The token you copied
6. Click **Submit**

### Required Scopes

| Scope | Purpose |
|-------|---------|
| `read_orders` | Required for all sensors (orders, revenue, etc.) |

## Configuration Options

### Initial Setup

| Option | Description |
|--------|-------------|
| Shop Domain | Your Shopify store domain |
| Client ID | OAuth client ID (OAuth method only) |
| Client Secret | OAuth client secret (OAuth method only) |
| Access Token | Admin API token (manual method only) |
| API Version | Shopify API version (default: 2024-10) |
| Timezone Override | Override store timezone for calculations |
| Include Test Orders | Include test orders in counts |
| Mock Mode | Use mock data for testing |

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

### OAuth: Redirect URL Mismatch

**Cause**: The redirect URL in Home Assistant doesn't match what's configured in Shopify.

**Solution**:
1. Ensure your Home Assistant external URL is correctly set in **Settings** → **System** → **Network**
2. Add the exact redirect URL to your Shopify app's allowed redirection URLs
3. The format should be: `https://your-ha-domain/auth/external/callback`

### Error: Invalid access token or insufficient permissions (401/403)

**Cause**: The access token is invalid or doesn't have the required scopes.

**Solution**:
1. Verify your access token/credentials are correct
2. Ensure the app has `read_orders` scope enabled
3. For OAuth: Re-authorize the app
4. For manual: Generate a new access token

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
2. Verify your credentials have the correct permissions
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
- Configuration (with credentials redacted)
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
