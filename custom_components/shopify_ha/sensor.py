"""Sensor platform for Shopify integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_DOLLAR
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ShopifyData
from .const import (
    ATTR_API_CALLS_REMAINING,
    ATTR_CURRENCY,
    ATTR_END_DATE,
    ATTR_LAST_SYNC,
    ATTR_MONTH,
    ATTR_ORDER_COUNT,
    ATTR_REVENUE_AUD,
    ATTR_SHOP_DOMAIN,
    ATTR_START_DATE,
    ATTR_STORE_NAME,
    DOMAIN,
    SENSOR_BUSIEST_MONTH,
    SENSOR_CURRENT_MONTH_REVENUE,
    SENSOR_TOTAL_ORDERS,
    SENSOR_UNFULFILLED_ORDERS,
    TARGET_CURRENCY,
)
from .coordinator import ShopifyDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class ShopifySensorEntityDescription(SensorEntityDescription):
    """Describes a Shopify sensor entity."""

    value_fn: Callable[[ShopifyData], Any]
    attributes_fn: Callable[[ShopifyData, ShopifyDataUpdateCoordinator], dict[str, Any]]


def _common_attributes(
    data: ShopifyData, coordinator: ShopifyDataUpdateCoordinator
) -> dict[str, Any]:
    """Return common attributes for all sensors."""
    attrs: dict[str, Any] = {
        ATTR_STORE_NAME: coordinator.shop_name,
        ATTR_SHOP_DOMAIN: coordinator.shop_domain,
    }
    if data.last_sync:
        attrs[ATTR_LAST_SYNC] = data.last_sync.isoformat()
    if data.api_calls_remaining is not None:
        attrs[ATTR_API_CALLS_REMAINING] = data.api_calls_remaining
    return attrs


def _unfulfilled_attributes(
    data: ShopifyData, coordinator: ShopifyDataUpdateCoordinator
) -> dict[str, Any]:
    """Return attributes for unfulfilled orders sensor."""
    return _common_attributes(data, coordinator)


def _revenue_attributes(
    data: ShopifyData, coordinator: ShopifyDataUpdateCoordinator
) -> dict[str, Any]:
    """Return attributes for revenue sensor."""
    attrs = _common_attributes(data, coordinator)
    attrs[ATTR_CURRENCY] = data.currency
    attrs[ATTR_ORDER_COUNT] = data.current_month_order_count
    if data.month_start:
        attrs[ATTR_START_DATE] = data.month_start.isoformat()
    if data.month_end:
        attrs[ATTR_END_DATE] = data.month_end.isoformat()
    return attrs


def _total_orders_attributes(
    data: ShopifyData, coordinator: ShopifyDataUpdateCoordinator
) -> dict[str, Any]:
    """Return attributes for total orders sensor."""
    return _common_attributes(data, coordinator)


def _busiest_month_attributes(
    data: ShopifyData, coordinator: ShopifyDataUpdateCoordinator
) -> dict[str, Any]:
    """Return attributes for busiest month sensor."""
    attrs = _common_attributes(data, coordinator)
    attrs[ATTR_MONTH] = data.busiest_month
    attrs[ATTR_REVENUE_AUD] = float(data.busiest_month_revenue_aud)
    attrs[ATTR_ORDER_COUNT] = data.busiest_month_order_count
    attrs[ATTR_CURRENCY] = data.currency
    return attrs


SENSOR_DESCRIPTIONS: tuple[ShopifySensorEntityDescription, ...] = (
    ShopifySensorEntityDescription(
        key=SENSOR_UNFULFILLED_ORDERS,
        translation_key="unfulfilled_orders_count",
        icon="mdi:package-variant",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="orders",
        value_fn=lambda data: data.unfulfilled_orders_count,
        attributes_fn=_unfulfilled_attributes,
    ),
    ShopifySensorEntityDescription(
        key=SENSOR_CURRENT_MONTH_REVENUE,
        translation_key="current_month_revenue",
        icon="mdi:cash-multiple",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=TARGET_CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda data: float(data.current_month_revenue_aud),
        attributes_fn=_revenue_attributes,
    ),
    ShopifySensorEntityDescription(
        key=SENSOR_TOTAL_ORDERS,
        translation_key="total_orders_count",
        icon="mdi:cart-check",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="orders",
        value_fn=lambda data: data.total_orders_count,
        attributes_fn=_total_orders_attributes,
    ),
    ShopifySensorEntityDescription(
        key=SENSOR_BUSIEST_MONTH,
        translation_key="busiest_month",
        icon="mdi:chart-line",
        value_fn=lambda data: data.busiest_month,
        attributes_fn=_busiest_month_attributes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shopify sensors from a config entry."""
    coordinator: ShopifyDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]["coordinator"]

    entities: list[ShopifySensor] = [
        ShopifySensor(coordinator, description, config_entry)
        for description in SENSOR_DESCRIPTIONS
    ]

    async_add_entities(entities)


class ShopifySensor(CoordinatorEntity[ShopifyDataUpdateCoordinator], SensorEntity):
    """Representation of a Shopify sensor."""

    entity_description: ShopifySensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ShopifyDataUpdateCoordinator,
        description: ShopifySensorEntityDescription,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._config_entry = config_entry

        # Create unique ID using store identifier
        store_slug = self._get_store_slug()
        self._attr_unique_id = f"{config_entry.entry_id}_{description.key}"

        # Set entity ID friendly name
        self.entity_id = f"sensor.{store_slug}_shopify_{description.key}"

    def _get_store_slug(self) -> str:
        """Get a URL-safe store identifier."""
        # Use shop domain without .myshopify.com
        domain = self.coordinator.shop_domain
        if domain.endswith(".myshopify.com"):
            domain = domain[:-14]
        # Replace non-alphanumeric with underscore
        return "".join(c if c.isalnum() else "_" for c in domain).lower()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._config_entry.entry_id)},
            name=f"Shopify: {self.coordinator.shop_name}",
            manufacturer="Shopify",
            model="Store",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"https://{self.coordinator.shop_domain}/admin",
        )

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        if self.coordinator.data is None:
            return {}
        return self.entity_description.attributes_fn(
            self.coordinator.data, self.coordinator
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success and self.coordinator.data is not None
