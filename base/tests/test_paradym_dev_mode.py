"""paradym's Development Mode switch-on, run against a scripted fake driver.

The fake driver answers each locator from the elements of the screen it is currently on, taps
move it to the screen the element was scripted to lead to, and back() moves it as scripted, so
the flow's real waits, probes and back presses run with nothing on a device. Tiny timeouts and a
no-op sleep keep every case fast.
"""
import logging
import time
from types import SimpleNamespace

import pytest
from selenium.common.exceptions import NoSuchElementException, WebDriverException

from wallets.paradym.flows import settings_flow
from wallets.paradym.pages import home_page, settings_page

PAGE_ARGS = {"timeouts": {"default": 0.1}}

OFF_THUMB = "[0,0][80,50]"
ON_THUMB = "[120,0][200,50]"
TRACK = "[0,0][200,50]"

PAGE_SOURCE = '<hierarchy><node text="Settings"/></hierarchy>'
DUMP = "paradym_settings_dev_mode.xml"


class Element:
    def __init__(self, driver, name, bounds=None, leads_to=None):
        self.driver, self.name, self.bounds, self.leads_to = driver, name, bounds, leads_to

    def get_attribute(self, attr):
        assert attr == "bounds"
        return self.bounds

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    def click(self):
        self.driver.taps.append(self.name)
        if self.leads_to:
            self.driver.screen = self.leads_to


class Driver:
    """`screens` maps a screen name to {locator: Element}; `backs` maps it to the next screen."""

    def __init__(self, screens, backs, start="home", dead=False):
        self.screens, self.backs, self.screen, self.dead = screens, backs, start, dead
        self.taps, self.back_presses, self.calls = [], 0, 0
        self.source_error = None

    @property
    def page_source(self):
        if self.source_error:
            raise self.source_error
        return PAGE_SOURCE

    def find_element(self, by, value):
        self.calls += 1
        if self.dead:
            raise WebDriverException("session is gone")
        try:
            return self.screens[self.screen][(by, value)]
        except KeyError:
            raise NoSuchElementException(f"{value} not on {self.screen}")

    def back(self):
        self.calls += 1
        if self.dead:
            raise WebDriverException("session is gone")
        self.back_presses += 1
        self.screen = self.backs.get(self.screen, self.screen)


def build(*, toggle_leads_to="settings_on", state="off", settings_item=True, thumb=True,
          menu_leads_to="drawer", backs=None, **kw):
    """A paradym with a home, a drawer and a Settings page whose toggle is `state`."""
    d = Driver({}, backs if backs is not None else
               {"settings_on": "drawer", "settings_off": "drawer", "drawer": "home"}, **kw)

    def thumb_el(bounds):
        return {settings_page._dev_mode_thumb: Element(d, "thumb", bounds)}

    toggle = {settings_page._dev_mode_toggle:
              Element(d, "toggle", TRACK, leads_to=toggle_leads_to)}
    settings = {settings_page._settings_heading: Element(d, "heading"), **toggle}
    d.screens = {
        "home": {home_page.SCREEN_ID: Element(d, "hello"),
                 settings_flow._menu_button: Element(d, "menu", leads_to=menu_leads_to)},
        "drawer": ({settings_flow._settings_nav_item:
                    Element(d, "settings", leads_to="settings_" + state)}
                   if settings_item else {}),
        "settings_off": {**settings, **(thumb_el(OFF_THUMB) if thumb else {})},
        "settings_on": {**settings, **(thumb_el(ON_THUMB) if thumb else {})},
    }
    return d


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(settings_flow, "_HOME_CHECK", 0.05)


def run(driver, caplog, xml_dir=None):
    caplog.set_level(logging.INFO)
    result = settings_flow.ensure_dev_mode(driver, xml_dir=xml_dir, **PAGE_ARGS)
    records = [r for r in caplog.records if r.name == settings_flow.logger.name]
    return result, records


def test_off_is_tapped_once_and_reads_on_afterwards(caplog):
    d = build(state="off")
    result, records = run(d, caplog)

    assert result is True and d.screen == "home"
    assert d.taps.count("toggle") == 1
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.INFO, "[settings_flow] paradym Development Mode: turned on")]


def test_a_thumb_still_sliding_after_the_tap_is_read_again(caplog, monkeypatch):
    d = build(state="off", toggle_leads_to="settings_off")
    pauses = []

    def pause(seconds):
        pauses.append(seconds)
        if len(pauses) == 2:
            d.screen = "settings_on"
    monkeypatch.setattr(time, "sleep", pause)
    result, records = run(d, caplog)

    assert result is True and d.taps.count("toggle") == 1
    assert records[0].getMessage() == "[settings_flow] paradym Development Mode: turned on"


def test_tap_that_leaves_the_toggle_off_warns(caplog):
    d = build(state="off", toggle_leads_to="settings_off")
    result, records = run(d, caplog)

    assert result is True
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert "tapped the toggle but it did not read as on" in records[0].getMessage()


def test_already_on_is_not_tapped(caplog):
    d = build(state="on")
    result, records = run(d, caplog)

    assert result is True and "toggle" not in d.taps
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.INFO, "[settings_flow] paradym Development Mode: was already on")]


def test_unreadable_state_is_not_tapped_and_is_named(caplog):
    d = build(state="on", thumb=False)
    result, records = run(d, caplog)

    assert result is True and "toggle" not in d.taps
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert "toggle's state" in message and "continuing without it" in message
    assert "init_flow" not in message


@pytest.mark.parametrize("missing", ["settings_item", "heading"])
def test_unreachable_settings_warns_and_still_goes_home(caplog, missing):
    d = build(state="on", settings_item=missing != "settings_item")
    if missing == "heading":
        del d.screens["settings_on"][settings_page._settings_heading]
    result, records = run(d, caplog)

    assert result is True and d.screen == "home" and d.back_presses >= 1
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert "Could not turn on paradym's Development Mode" in records[0].getMessage()
    assert "RuntimeError" in records[0].getMessage()


def test_dead_session_warns_without_raising(caplog):
    d = build(dead=True)
    result, records = run(d, caplog)

    assert result is False
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert "WebDriverException: Message: session is gone" in message
    assert message.endswith("so init_flow takes over")
    assert "\n" not in message


def test_gives_up_after_three_back_presses(caplog):
    d = build(state="on", backs={})
    result, records = run(d, caplog)

    assert result is False and d.back_presses == 3
    assert len(records) == 1
    assert records[0].getMessage().endswith(
        "paradym Development Mode: was already on; paradym was not back on its home screen "
        "afterwards, so init_flow takes over")


def test_never_presses_back_on_home(caplog):
    # The menu tap leaves paradym on home, so the drawer's Settings item is missing and the flow
    # fails with home already showing.
    d = build(menu_leads_to="home")
    result, records = run(d, caplog)

    assert result is True and d.back_presses == 0
    assert len(records) == 1 and records[0].levelno == logging.WARNING


def test_once_per_session_second_call_does_nothing(caplog):
    caplog.set_level(logging.INFO)
    config = SimpleNamespace()
    first = build(state="off")
    assert settings_flow.ensure_dev_mode_once(first, config, **PAGE_ARGS) is True
    assert first.taps.count("toggle") == 1

    second = build(state="off")
    assert settings_flow.ensure_dev_mode_once(second, config, **PAGE_ARGS) is True
    assert second.calls == 0 and second.taps == []


def test_unreadable_toggle_saves_the_settings_screen(caplog, tmp_path):
    xml_dir = tmp_path / "xml_dumps"
    d = build(state="on", thumb=False)
    result, records = run(d, caplog, xml_dir)

    assert result is True and "toggle" not in d.taps
    assert (xml_dir / DUMP).read_text(encoding="utf-8") == PAGE_SOURCE
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert f"xml_dumps/{DUMP}" in records[0].getMessage()


@pytest.mark.parametrize("state", ["on", "off"])
def test_readable_toggle_saves_nothing(caplog, tmp_path, state):
    xml_dir = tmp_path / "xml_dumps"
    result, _ = run(build(state=state), caplog, xml_dir)

    assert result is True
    assert not xml_dir.exists()


def test_unreachable_settings_saves_nothing(caplog, tmp_path):
    xml_dir = tmp_path / "xml_dumps"
    result, records = run(build(settings_item=False), caplog, xml_dir)

    assert result is True and len(records) == 1
    assert not xml_dir.exists()


def test_a_failing_save_is_named_and_does_not_raise(caplog, tmp_path):
    d = build(state="on", thumb=False)
    d.source_error = WebDriverException("page source gone")
    result, records = run(d, caplog, tmp_path / "xml_dumps")

    assert result is True
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert "could not save the Settings screen" in records[0].getMessage()
    assert "saved to" not in records[0].getMessage()


def test_once_saves_into_the_run_directorys_xml_dumps(caplog, tmp_path):
    caplog.set_level(logging.INFO)
    config = SimpleNamespace(_run_dir=tmp_path)
    d = build(state="on", thumb=False)

    assert settings_flow.ensure_dev_mode_once(d, config, **PAGE_ARGS) is True
    assert (tmp_path / "xml_dumps" / DUMP).read_text(encoding="utf-8") == PAGE_SOURCE


def test_once_without_a_run_directory_saves_nothing(caplog, tmp_path, monkeypatch):
    caplog.set_level(logging.INFO)
    monkeypatch.chdir(tmp_path)
    d = build(state="on", thumb=False)

    assert settings_flow.ensure_dev_mode_once(d, SimpleNamespace(), **PAGE_ARGS) is True
    assert list(tmp_path.iterdir()) == []
    messages = [r.getMessage() for r in caplog.records if r.name == settings_flow.logger.name]
    assert len(messages) == 1 and "saved to" not in messages[0]


def test_a_failed_attempt_is_not_retried_by_the_next_call():
    config = SimpleNamespace()
    dead = build(dead=True)
    assert settings_flow.ensure_dev_mode_once(dead, config, **PAGE_ARGS) is False

    later = build(state="off")
    assert settings_flow.ensure_dev_mode_once(later, config, **PAGE_ARGS) is True
    assert later.calls == 0
