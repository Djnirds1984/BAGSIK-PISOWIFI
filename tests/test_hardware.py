"""Coin hardware page: board selector, physical pin map, NodeMCU transport."""

from __future__ import annotations

from app.hardware import pins
from app.services import catalog

CREDENTIALS = {"username": "admin", "password": "testpass123"}


def _login(client, csrf):
    return client.post("/admin/login", data={**CREDENTIALS, "csrf_token": csrf})


def test_pin_map_names_the_same_hole_per_board():
    rpi = pins.resolve("rpi", 3)
    assert (rpi["name"], rpi["backend_pin"], rpi["driver"]) == ("GPIO2", 2, "gpio")

    orangepi = pins.resolve("orangepi", 3)
    assert (orangepi["name"], orangepi["backend_pin"]) == ("PA12", 12)

    assert pins.resolve("rpi", 11)["name"] == "GPIO17"
    assert pins.resolve("orangepi", 11)["name"] == "PA1"


def test_power_and_reserved_pins_are_not_offered():
    assert pins.resolve("rpi", 1)["ok"] is False       # 3.3V
    assert pins.resolve("rpi", 6)["ok"] is False       # ground
    assert pins.resolve("rpi", 27)["ok"] is False      # HAT EEPROM
    offered = [option["value"] for option in pins.pin_options("rpi")]
    assert 3 in offered and 1 not in offered


def test_mini_pc_has_no_pin_only_a_bridge():
    target = pins.resolve("x64", 3)
    assert target["gpio"] is False
    assert target["driver"] == "nodemcu"
    assert target["backend_pin"] is None


def test_board_detection_reads_the_device_tree_name():
    assert pins.detect_board("Raspberry Pi 3 Model B Plus Rev 1.3") == "rpi"
    assert pins.detect_board("OrangePi PC") == "orangepi"
    assert pins.detect_board("Orange Pi One") == "orangepi"
    assert pins.detect_board("") == "x64"


def test_hardware_page_defaults_to_pin_three(client, csrf):
    _login(client, csrf)

    html = client.get("/admin/settings/hardware?board=rpi").text

    assert "Raspberry Pi 3B+" in html
    assert "Orange Pi One / PC" in html
    assert "NodeMCU 8266" in html
    assert 'value="3" selected' in html
    assert "GPIO2" in html


def test_pin_list_follows_the_selected_board(client, csrf):
    _login(client, csrf)

    orangepi = client.get("/admin/settings/hardware?board=orangepi").text
    assert "Pin 3 \u00b7 PA12" in orangepi
    assert "Pin 3 \u00b7 GPIO2" not in orangepi

    rpi = client.get("/admin/settings/hardware?board=rpi").text
    assert "Pin 3 \u00b7 GPIO2" in rpi
    assert "Pin 3 \u00b7 PA12" not in rpi


def test_saving_a_board_and_pin_persists_the_choice(client, csrf):
    _login(client, csrf)

    response = client.post(
        "/admin/settings/hardware",
        data={"board": "orangepi", "pin": "11", "csrf_token": csrf},
    )

    assert "physical pin 11" in response.text
    assert catalog.setting("coin_board") == "orangepi"
    assert catalog.setting("coin_pin") == "11"


def test_x64_stores_the_bridge_and_hides_the_pin(client, csrf):
    _login(client, csrf)

    html = client.get("/admin/settings/hardware?board=x64").text
    assert 'name="pin"' not in html
    assert 'name="nodemcu_host"' in html

    response = client.post(
        "/admin/settings/hardware",
        data={
            "board": "x64",
            "nodemcu_host": "192.168.4.20",
            "nodemcu_port": "9000",
            "csrf_token": csrf,
        },
    )

    assert "NodeMCU bridge firmware" in response.text
    assert catalog.setting("coin_board") == "x64"
    assert catalog.setting("coin_nodemcu_host") == "192.168.4.20"
    assert catalog.setting("coin_nodemcu_port") == "9000"


def test_a_power_pin_is_refused(client, csrf):
    _login(client, csrf)

    response = client.post(
        "/admin/settings/hardware",
        data={"board": "rpi", "pin": "1", "csrf_token": csrf},
    )

    assert "not a GPIO pin" in response.text
    assert catalog.setting("coin_board") == ""
    assert catalog.setting("coin_pin") == ""


def test_the_slot_keeps_working_after_a_save(client, csrf):
    """A saved pin must not disturb the mock acceptor the kiosk relies on."""
    _login(client, csrf)
    client.post(
        "/admin/settings/hardware",
        data={"board": "rpi", "pin": "7", "csrf_token": csrf},
    )

    state = client.get("/coin/state").json()

    assert state["coin_ready"] is True
    assert state["phase"] == "idle"
