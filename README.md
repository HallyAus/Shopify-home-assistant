# Shopify Home Assistant Integration

[![HACS Custom][hacs-badge]][hacs-url]
[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)
[![Buy Me A Coffee][coffee-badge]][coffee-url]

A custom Home Assistant integration that connects to the Shopify Admin API and exposes **14 sensors** for monitoring your store's orders and revenue.

## Support This Project

If you find this integration useful, consider supporting me!

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support-yellow?style=for-the-badge&logo=buy-me-a-coffee)](https://buymeacoffee.com/printforge)

**Or use my referral codes:**
- **Starlink** - Get one free month! [Sign up here](https://starlink.com/residential?referral=RC-2455784-77014-69&app_source=share)
- **OVO Energy** (Australia) - [Get a discount](https://www.ovoenergy.com.au/refer/daniel16485)

## Features

### 14 Sensors with Icons

| Icon | Sensor | Description |
|------|--------|-------------|
| `mdi:package-variant` | **Unfulfilled Orders** | Orders paid but not yet fulfilled |
| `mdi:cash-multiple` | **Current Month Revenue** | Revenue for the current month (store currency) |
| `mdi:cart-check` | **Total Orders** | All-time order count |
| `mdi:chart-line` | **Busiest Month** | Highest revenue month historically |
| `mdi:cart-arrow-down` | **Today's Orders** | Orders placed today |
| `mdi:cash-register` | **Today's Revenue** | Revenue from today's orders |
| `mdi:calendar-week` | **This Week's Orders** | Orders this week (Mon-Sun) |
| `mdi:cash-sync` | **This Week's Revenue** | Revenue for the current week |
| `mdi:cash-marker` | **Average Order Value** | Average order value this month |
| `mdi:clock-alert-outline` | **Pending Payment Orders** | Orders awaiting payment |
| `mdi:package-variant-closed-check` | **Partially Fulfilled Orders** | Orders partially shipped |
| `mdi:calendar-star` | **Year-to-Date Revenue** | Revenue for the current year |
| `mdi:history` | **Last 30 Days Orders** | Order count in last 30 days |
| `mdi:chart-areaspline` | **Last 30 Days Revenue** | Revenue in last 30 days |

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
X-Forwarded-Host: <your-domain>
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

The redirect URL is dynamically built from your Home Assistant external URL:

```
https://<your-ha-external-url>/auth/external/callback
```

**Example:** If your HA is at `https://myha.duckdns.org`, the redirect URL would be:
```
https://myha.duckdns.org/auth/external/callback
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
2. Under **URLs**, add your redirect URL:
   ```
   https://<your-ha-external-url>/auth/external/callback
   ```
   Replace `<your-ha-external-url>` with your actual Home Assistant external URL (e.g., `myha.duckdns.org`)
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
   - **Shop Domain**: Your store domain (e.g., `my-store` or `my-store.myshopify.com`)
   - **Client ID**: From your Shopify app
   - **Client Secret**: From your Shopify app
5. Click **Submit**
6. You will be redirected to Shopify to authorize the app
7. Click **Install app** on Shopify to grant access
8. You will be redirected back to Home Assistant

### How Authentication Works

1. You enter your credentials in Home Assistant
2. Home Assistant builds an authorization URL with a JWT-encoded state parameter
3. You are redirected to Shopify's authorization page
4. You approve the app on Shopify
5. Shopify redirects back to Home Assistant at `/auth/external/callback`
6. Home Assistant decodes the JWT state and routes to the correct config flow
7. Home Assistant exchanges the authorization code for an access token
8. The access token is stored securely in your configuration

**Technical Details:**
- The OAuth `state` parameter is JWT-encoded containing the flow ID
- This allows HA to securely match callbacks to the originating config flow
- The JWT uses a per-instance secret key for security

**Important Notes:**
- Shopify access tokens are **long-lived** and do not expire
- No automatic token refresh is needed
- If your token is revoked (e.g., app uninstalled), you'll need to re-authorize
- You MUST access HA via the external URL during setup (not localhost)

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

All sensors include these common attributes:
- `store_name`: Your store's name
- `shop_domain`: Your store's domain
- `last_sync`: Timestamp of last data update
- `api_calls_remaining`: Remaining API capacity

### Original Sensors

#### `mdi:package-variant` sensor.\<store\>_shopify_unfulfilled_orders_count

**State**: Number of unfulfilled paid orders

#### `mdi:cash-multiple` sensor.\<store\>_shopify_current_month_revenue_aud

**State**: Current month's revenue in the store currency

**Additional Attributes**: `currency`, `start_date`, `end_date`, `order_count`

#### `mdi:cart-check` sensor.\<store\>_shopify_total_orders_count

**State**: Total number of orders (all time)

#### `mdi:chart-line` sensor.\<store\>_shopify_busiest_month

**State**: The busiest month in "YYYY-MM" format

**Additional Attributes**: `month`, `revenue_aud`, `order_count`, `currency`

### New Sensors - Daily Tracking

#### `mdi:cart-arrow-down` sensor.\<store\>_shopify_today_orders_count

**State**: Number of orders placed today

**Additional Attributes**: `start_date`, `end_date`, `period`

#### `mdi:cash-register` sensor.\<store\>_shopify_today_revenue_aud

**State**: Revenue from today's orders in the store currency

**Additional Attributes**: `currency`, `order_count`, `start_date`, `end_date`, `period`

### New Sensors - Weekly Tracking

#### `mdi:calendar-week` sensor.\<store\>_shopify_this_week_orders_count

**State**: Number of orders this week (Monday to now)

**Additional Attributes**: `start_date`, `end_date`, `week_number`, `period`

#### `mdi:cash-sync` sensor.\<store\>_shopify_this_week_revenue_aud

**State**: Revenue for the current week in the store currency

**Additional Attributes**: `currency`, `order_count`, `start_date`, `end_date`, `week_number`, `period`

### New Sensors - Order Analysis

#### `mdi:cash-marker` sensor.\<store\>_shopify_average_order_value_aud

**State**: Average order value for the current month in the store currency

**Additional Attributes**: `currency`, `order_count`, `period`

#### `mdi:clock-alert-outline` sensor.\<store\>_shopify_pending_payment_orders_count

**State**: Number of orders awaiting payment

#### `mdi:package-variant-closed-check` sensor.\<store\>_shopify_partially_fulfilled_orders_count

**State**: Number of orders that are partially fulfilled/shipped

### New Sensors - Long-term Tracking

#### `mdi:calendar-star` sensor.\<store\>_shopify_year_to_date_revenue_aud

**State**: Year-to-date revenue in the store currency

**Additional Attributes**: `currency`, `order_count`, `start_date`, `year`, `period`

#### `mdi:history` sensor.\<store\>_shopify_last_30_days_orders_count

**State**: Number of orders in the last 30 days

**Additional Attributes**: `start_date`, `period`

#### `mdi:chart-areaspline` sensor.\<store\>_shopify_last_30_days_revenue_aud

**State**: Revenue from the last 30 days in the store currency

**Additional Attributes**: `currency`, `order_count`, `start_date`, `period`

## Troubleshooting

### "Invalid state. Is My Home Assistant configured to go to the right instance?"

**Cause**: JWT state validation failed. This happens when:
1. The callback went to a different HA instance than the one that started the flow
2. HA's external URL is misconfigured
3. The config flow timed out (10 minute limit)
4. Reverse proxy headers are not set correctly

**Solution**:
1. **Check HA External URL**: Ensure Home Assistant's external URL is configured:
   - Go to Settings → System → Network → External URL
   - Must be an HTTPS URL accessible from the internet

2. **Check Reverse Proxy Headers**: Your proxy MUST set these headers:
   ```
   X-Forwarded-Proto: https
   X-Forwarded-Host: <your-domain>
   ```

3. **Access HA via External URL**: When setting up the integration, you MUST access HA via your external URL (not localhost or local IP).

4. **Complete Setup Quickly**: The OAuth flow has a 10-minute timeout. Complete authorization promptly.

5. **Check Debug Logs**: Enable debug logging and look for these messages:
   ```
   === SHOPIFY OAUTH DEBUG ===
   Flow ID: <uuid>
   Redirect URI: https://<your-domain>/auth/external/callback
   ```

   When the callback arrives:
   ```
   === SHOPIFY OAUTH CALLBACK ===
   Host header: <your-domain>
   Scheme: https
   ```

### OAuth redirect fails or shows error

**Cause**: Redirect URL mismatch or Home Assistant not publicly accessible.

**Solution**:
1. Verify the redirect URL in your Shopify app exactly matches your HA external URL:
   ```
   https://<your-ha-external-url>/auth/external/callback
   ```
2. Ensure your Home Assistant is accessible via HTTPS at the configured URL
3. Check that no firewall is blocking the callback
4. Make sure you're accessing HA through the external URL, not a local address

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

### Currency and legacy entity names

Monetary sensors report the currency configured in your Shopify store. The integration does not convert amounts. Existing entity names ending in `_aud` are retained so automations and dashboards keep working; check the sensor unit or `currency` attribute for the actual currency.

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

**Original Sensors:**
1. **Unfulfilled Orders**: Query for open, paid, unfulfilled orders
2. **Current Month Revenue**: Paginated query for orders in date range
3. **Total Orders**: Cached count query (refreshed daily)
4. **Busiest Month**: Paginated query for historical data

**New Sensors (fetched concurrently):**
5. **Today's Orders/Revenue**: Orders from start of today
6. **This Week's Orders/Revenue**: Orders since Monday
7. **Average Order Value**: Calculated from current month data
8. **Pending Payment Orders**: Open orders with pending payment status
9. **Partially Fulfilled Orders**: Orders partially shipped
10. **Year-to-Date Revenue**: Orders from January 1st
11. **Last 30 Days Orders/Revenue**: Rolling 30-day window

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
[coffee-badge]: https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support-yellow?logo=buy-me-a-coffee
[coffee-url]: https://buymeacoffee.com/printforge
