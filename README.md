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
- **OAuth 2.0 Authorization Code flow** for secure authentication
- Long-lived access tokens (no refresh needed)
- Supports multiple stores (add multiple config entries)
- Configurable update interval (default: 15 minutes)
- Smart caching to minimize API calls
- Automatic rate limiting handling
- Mock mode for testing without real API calls
- Full diagnostics support with credential redaction

## Requirements

- Home Assistant 2024.12 or newer
- **Home Assistant must be publicly accessible via HTTPS** (required for OAuth callback)
- A Shopify store with Admin API access
- A Shopify custom app with Client ID and Client Secret

### Reverse Proxy Configuration

If you're using a reverse proxy (nginx, Traefik, etc.), you **MUST** configure these headers for OAuth to work correctly:

```
X-Forwarded-Proto: https
X-Forwarded-Host: homeassistant.printforge.com.au
```

**Example nginx configuration:**
```nginx
location / {
    proxy_pass http://homeassistant:8123;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
}
```

**Example Traefik configuration:**
```yaml
http:
  middlewares:
    headers:
      headers:
        customRequestHeaders:
          X-Forwarded-Proto: "https"
```

Without these headers, Home Assistant cannot determine the correct external URL for OAuth callbacks.

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

## Authentication

This integration uses **OAuth 2.0 Authorization Code flow** for authentication. You will be redirected to Shopify to authorize the app.

### Important: Redirect URL Configuration

Your Home Assistant instance **must be publicly accessible via HTTPS** for OAuth to work.

The exact redirect URL used by this integration is:

```
https://homeassistant.printforge.com.au/auth/external/callback
```

**You must add this exact URL** to your Shopify app's Redirect URLs setting.

### Step 1: Create a Custom App in Shopify Dev Portal

1. Go to [Shopify Partners](https://partners.shopify.com/) or your Shopify admin
2. Navigate to **Apps** → **All apps** → **Create app**
3. Choose **Create app manually**
4. Name your app (e.g., "Home Assistant Integration")
5. Click **Create app**

### Step 2: Configure the App

1. In your app settings, go to **Configuration**
2. Under **URLs**, add the redirect URL:
   ```
   https://homeassistant.printforge.com.au/auth/external/callback
   ```
3. Under **API access**, configure the required scopes:
   - Enable **`read_orders`** scope
4. Click **Save**

### Step 3: Get Your Credentials

1. Go to **API credentials** in your app
2. Copy the **Client ID**
3. Copy the **Client Secret**

### Step 4: Install the App on Your Store

1. In your app, go to **Overview**
2. Click **Select store** and choose your store
3. Click **Install app** to install it on your store

### Step 5: Configure Home Assistant

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "Shopify Store"
4. Enter:
   - **Shop Domain**: Your store domain (e.g., `printforge` or `printforge.myshopify.com`)
   - **Client ID**: From your Shopify app
   - **Client Secret**: From your Shopify app
5. Click **Submit**
6. You will be redirected to Shopify to authorize the app
7. Click **Install app** on Shopify to grant access
8. You will be redirected back to Home Assistant

### How Authentication Works

1. You enter your credentials in Home Assistant
2. Home Assistant redirects you to Shopify's authorization page
3. You approve the app on Shopify
4. Shopify redirects back to Home Assistant with an authorization code
5. Home Assistant exchanges the code for an access token
6. The access token is stored securely in your configuration

**Important Notes:**
- Shopify access tokens are **long-lived** and do not expire
- No automatic token refresh is needed
- If your token is revoked (e.g., app uninstalled), you'll need to re-authorize

### Required Scopes

| Scope | Purpose |
|-------|---------|
| `read_orders` | Required for all sensors (orders, revenue, etc.) |

## Configuration Options

### Initial Setup

| Option | Description |
|--------|-------------|
| Shop Domain | Your Shopify store domain |
| Client ID | OAuth client ID from your custom app |
| Client Secret | OAuth client secret from your custom app |

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

### OAuth redirect fails or shows error

**Cause**: Redirect URL mismatch or Home Assistant not publicly accessible.

**Solution**:
1. Verify the redirect URL in your Shopify app exactly matches:
   ```
   https://homeassistant.printforge.com.au/auth/external/callback
   ```
2. Ensure your Home Assistant is accessible via HTTPS at the configured URL
3. Check that no firewall is blocking the callback

### Error: Invalid credentials or insufficient permissions (401/403)

**Cause**: The access token is invalid or the app doesn't have the required scopes.

**Solution**:
1. Re-authorize the app through the integration
2. Ensure the app has `read_orders` scope enabled
3. Make sure the app is still installed on your store
4. Check if the app was uninstalled and reinstall it

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

### Access token revoked

**Cause**: The app was uninstalled from the store or the token was revoked.

**Solution**:
1. Go to the integration in Home Assistant
2. Click **Reconfigure** to re-authorize
3. Complete the OAuth flow again to get a new token

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

You can test the API with your access token:

```bash
# Test shop info
curl -X POST \
  "https://YOUR-STORE.myshopify.com/admin/api/2026-01/graphql.json" \
  -H "Content-Type: application/json" \
  -H "X-Shopify-Access-Token: YOUR_ACCESS_TOKEN" \
  -d '{"query": "{ shop { name currencyCode ianaTimezone } }"}'

# Test orders count
curl -X POST \
  "https://YOUR-STORE.myshopify.com/admin/api/2026-01/graphql.json" \
  -H "Content-Type: application/json" \
  -H "X-Shopify-Access-Token: YOUR_ACCESS_TOKEN" \
  -d '{"query": "{ ordersCount { count } }"}'

# Test unfulfilled orders
curl -X POST \
  "https://YOUR-STORE.myshopify.com/admin/api/2026-01/graphql.json" \
  -H "Content-Type: application/json" \
  -H "X-Shopify-Access-Token: YOUR_ACCESS_TOKEN" \
  -d '{"query": "{ orders(first: 10, query: \"status:open AND fulfillment_status:unfulfilled AND financial_status:paid\") { edges { node { name createdAt } } } }"}'
```

Replace `YOUR-STORE` with your shop domain and `YOUR_ACCESS_TOKEN` with your token.

### Diagnostics

1. Go to **Settings** → **Devices & Services**
2. Find the Shopify integration and click on it
3. Click the three dots menu → **Download diagnostics**

The diagnostics file includes:
- Configuration (with credentials redacted)
- Token status (masked, showing only last 4 characters)
- Granted scopes
- Current sensor data
- Shop information
- Coordinator status

## API Usage

This integration uses Shopify's GraphQL Admin API version 2026-01. Here's what happens during each update:

1. **Unfulfilled Orders**: Query for open, paid, unfulfilled orders
2. **Current Month Revenue**: Paginated query for orders in date range
3. **Total Orders**: Cached count query (refreshed daily)
4. **Busiest Month**: Paginated query for historical data

The integration implements:
- Long-lived access tokens (no refresh needed)
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
