"""Verify Shopify shop money retains its native currency in HA sensors."""

import asyncio
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _stub_module(name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    module.__path__ = []
    sys.modules[name] = module
    return module


def _load_integration():
    integration = _stub_module("custom_components.shopify_ha")
    integration.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components" / "shopify_ha")]

    class GenericBase:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, *args, **kwargs):
            if args:
                self.hass = args[0]
            self.data = None

    class CoordinatorEntity(GenericBase):
        def __init__(self, coordinator):
            self.coordinator = coordinator

    @dataclass(frozen=True, kw_only=True)
    class SensorEntityDescription:
        key: str
        translation_key: str | None = None
        icon: str | None = None
        device_class: str | None = None
        state_class: str | None = None
        native_unit_of_measurement: str | None = None
        suggested_display_precision: int | None = None

    _stub_module("homeassistant")
    _stub_module("homeassistant.components")
    _stub_module("homeassistant.components.recorder")
    calls = []
    _stub_module(
        "homeassistant.components.recorder.statistics",
        async_update_statistics_metadata=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    _stub_module(
        "homeassistant.components.sensor",
        SensorDeviceClass=SimpleNamespace(MONETARY="monetary"),
        SensorStateClass=SimpleNamespace(MEASUREMENT="measurement", TOTAL="total", TOTAL_INCREASING="total_increasing"),
        SensorEntity=type("SensorEntity", (), {}),
        SensorEntityDescription=SensorEntityDescription,
    )
    _stub_module("homeassistant.config_entries", ConfigEntry=type("ConfigEntry", (), {}))
    _stub_module("homeassistant.const", CURRENCY_DOLLAR="$")
    _stub_module("homeassistant.core", HomeAssistant=type("HomeAssistant", (), {}))
    _stub_module("homeassistant.exceptions", ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (Exception,), {}))
    helpers = _stub_module("homeassistant.helpers")
    registry = SimpleNamespace(async_get_entity_id=lambda domain, platform, unique_id: f"sensor.{unique_id}")
    helpers.entity_registry = _stub_module("homeassistant.helpers.entity_registry", async_get=lambda hass: registry)
    _stub_module("homeassistant.helpers.device_registry", DeviceEntryType=SimpleNamespace(SERVICE="service"), DeviceInfo=dict)
    _stub_module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    _stub_module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=GenericBase, CoordinatorEntity=CoordinatorEntity, UpdateFailed=type("UpdateFailed", (Exception,), {}))

    from custom_components.shopify_ha.api import ShopInfo, ShopifyData
    from custom_components.shopify_ha.coordinator import ShopifyDataUpdateCoordinator
    from custom_components.shopify_ha.sensor import SENSOR_DESCRIPTIONS, ShopifySensor

    return ShopInfo, ShopifyData, ShopifyDataUpdateCoordinator, SENSOR_DESCRIPTIONS, ShopifySensor, calls


def test_gbp_and_eur_revenue_keep_shop_amount_and_currency():
    ShopInfo, ShopifyData, Coordinator, descriptions, Sensor, metadata_calls = _load_integration()

    for currency in ("GBP", "EUR"):
        class Client:
            shop_domain = "example.myshopify.com"

            async def test_connection(self):
                return ShopInfo(name="Example", currency_code=currency, timezone="UTC")

            async def fetch_all_data(self, months_lookback):
                return ShopifyData(
                    current_month_revenue_aud=Decimal("12.50"),
                    shop_info=ShopInfo(name="Example", currency_code=currency, timezone="UTC"),
                )

        hass = SimpleNamespace(config=SimpleNamespace(components={"recorder"}))
        entry = SimpleNamespace(entry_id="entry", options={}, data={})
        coordinator = Coordinator(hass, Client(), entry)
        data = asyncio.run(coordinator._async_update_data())
        coordinator.data = data

        monetary = next(d for d in descriptions if d.device_class == "monetary")
        sensor = Sensor(coordinator, monetary, entry)
        assert data.currency == currency
        assert sensor.native_unit_of_measurement == currency
        assert sensor.native_value == 12.5

    assert metadata_calls
    assert all(call[1]["new_unit_of_measurement"] in {"GBP", "EUR"} for call in metadata_calls)
