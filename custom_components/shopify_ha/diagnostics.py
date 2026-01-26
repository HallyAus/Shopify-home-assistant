"""Diagnostics support for Shopify integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import ShopifyDataUpdateCoordinator

# Keys to redact from diagnostics
TO_REDACT = {
    CONF_ACCESS_TOKEN,
    "access_token",
    "token",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: ShopifyDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    # Build diagnostics data
    data = coordinator.data

    diagnostics_data: dict[str, Any] = {
        "config_entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "domain": entry.domain,
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "shop_domain": coordinator.shop_domain,
            "shop_name": coordinator.shop_name,
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
    }

    if data:
        diagnostics_data["data"] = {
            "unfulfilled_orders_count": data.unfulfilled_orders_count,
            "current_month_revenue_aud": str(data.current_month_revenue_aud),
            "current_month_order_count": data.current_month_order_count,
            "total_orders_count": data.total_orders_count,
            "busiest_month": data.busiest_month,
            "busiest_month_revenue_aud": str(data.busiest_month_revenue_aud),
            "busiest_month_order_count": data.busiest_month_order_count,
            "currency": data.currency,
            "api_calls_remaining": data.api_calls_remaining,
            "last_sync": data.last_sync.isoformat() if data.last_sync else None,
            "month_start": data.month_start.isoformat() if data.month_start else None,
            "month_end": data.month_end.isoformat() if data.month_end else None,
        }

        if data.shop_info:
            diagnostics_data["shop_info"] = {
                "name": data.shop_info.name,
                "currency_code": data.shop_info.currency_code,
                "timezone": data.shop_info.timezone,
                "plan_name": data.shop_info.plan_name,
            }
    else:
        diagnostics_data["data"] = None

    return diagnostics_data
