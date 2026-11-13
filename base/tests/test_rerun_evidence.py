"""A rerun must not overwrite the evidence of the attempt before it.

pytest-rerunfailures re-runs the same item and every attempt writes the same filenames, so after
three attempts the app.log blocks of attempts 1 and 2 named attempt 3's files. These tests drive
`archive_previous_attempt` with a fabricated item and a real app.log written by the real capture
code; the end-to-end check under real pytest is in test_app_fixture_fallback.py.
"""
from types import SimpleNamespace

from base.conftest_helpers import (
    archive_previous_attempt,
    note_attempt_file,
    save_failure_artifacts,
)

NAME = "test_x_fakewallet"


class Driver:
    """page_source and screenshot carry the attempt number; `secure` refuses the screenshot."""

    def __init__(self, node, secure=False):
        self.node = node
        self.secure = secure

    @property
    def page_source(self):
        return f"<xml attempt={self.node._attempt}/>"

    def save_screenshot(self, path):
        if self.secure:
            raise Exception("Does the current view have 'secure' flag set?")
        with open(path, "w") as f:
            f.write(f"png attempt={self.node._attempt}")
        return True


def _item(tmp_path, name=NAME):
    node = SimpleNamespace(name=name, nodeid=f"x::{name}", _attempt=1,
                           config=SimpleNamespace(_run_dir=tmp_path))
    node.rep_call = SimpleNamespace(failed=True)
    return node


def _fail(tmp_path, node, secure=False):
    """One attempt's capture, the way the shared capture point does it."""
    node._error_screen_logged = False
    request = SimpleNamespace(config=node.config, node=node)
    save_failure_artifacts(Driver(node, secure), request, {"application": {}}, wallet="")


def _evidence(tmp_path, name=NAME):
    """The evidence line of every block of one test, in order."""
    out, inside = [], False
    for line in (tmp_path / "app.log").read_text().splitlines():
        if line.startswith("--- TEST: "):
            inside = line == f"--- TEST: {name} ---"
        elif inside and line.startswith("evidence:"):
            out.append([e.strip() for e in line.split(":", 1)[1].split(",")])
    return out


def _next_attempt(node):
    archive_previous_attempt(node)
    node._attempt += 1


def test_the_attempt_that_is_rerun_keeps_its_files_under_attempt_names(tmp_path):
    node = _item(tmp_path)
    _fail(tmp_path, node)
    _next_attempt(node)
    _fail(tmp_path, node)

    assert _evidence(tmp_path) == [
        [f"xml_dumps/{NAME}.attempt1.xml", f"screenshots/{NAME}.attempt1.png"],
        [f"xml_dumps/{NAME}.xml", f"screenshots/{NAME}.png"],
    ]
    for rel in sum(_evidence(tmp_path), []):
        assert (tmp_path / rel).exists(), rel


def test_three_attempts_each_block_names_files_holding_its_own_content(tmp_path):
    node = _item(tmp_path)
    for _ in range(3):
        _fail(tmp_path, node)
        if node._attempt < 3:
            _next_attempt(node)

    blocks = _evidence(tmp_path)
    assert len(blocks) == 3
    for n, entries in enumerate(blocks, start=1):
        assert len(entries) == 2
        assert (tmp_path / entries[0]).read_text() == f"<xml attempt={n}/>"
        assert (tmp_path / entries[1]).read_text() == f"png attempt={n}"
    assert blocks[2] == [f"xml_dumps/{NAME}.xml", f"screenshots/{NAME}.png"]


def test_a_secure_screen_attempt_renames_only_its_xml(tmp_path):
    node = _item(tmp_path)
    _fail(tmp_path, node, secure=True)
    _next_attempt(node)
    _fail(tmp_path, node)

    assert (tmp_path / "xml_dumps" / f"{NAME}.attempt1.xml").exists()
    # Attempt 2's screenshot is the canonical one; it must not have been taken for attempt 1's.
    assert not (tmp_path / "screenshots" / f"{NAME}.attempt1.png").exists()
    assert (tmp_path / "screenshots" / f"{NAME}.png").read_text() == "png attempt=2"
    assert _evidence(tmp_path)[0] == [f"xml_dumps/{NAME}.attempt1.xml"]


def test_a_recording_is_renamed_without_touching_app_log(tmp_path):
    node = _item(tmp_path)
    (tmp_path / "recordings").mkdir()
    (tmp_path / "recordings" / f"{NAME}.mp4").write_text("video 1")
    note_attempt_file(node, f"recordings/{NAME}.mp4")
    (tmp_path / "app.log").write_text("untouched\n")

    archive_previous_attempt(node)

    assert (tmp_path / "recordings" / f"{NAME}.attempt1.mp4").read_text() == "video 1"
    assert not (tmp_path / "recordings" / f"{NAME}.mp4").exists()
    assert (tmp_path / "app.log").read_text() == "untouched\n"


def test_another_tests_block_with_a_longer_name_is_left_byte_identical(tmp_path):
    other = _item(tmp_path, name="test_x_fakewallet_y")
    _fail(tmp_path, other)
    node = _item(tmp_path)
    _fail(tmp_path, node)
    log = (tmp_path / "app.log").read_text()
    other_block = log[log.index("--- TEST: test_x_fakewallet_y ---"):log.index(
        f"--- TEST: {NAME} ---")]

    archive_previous_attempt(node)

    after = (tmp_path / "app.log").read_text()
    assert other_block in after
    assert _evidence(tmp_path, "test_x_fakewallet_y") == [
        ["xml_dumps/test_x_fakewallet_y.xml", "screenshots/test_x_fakewallet_y.png"]]


def test_a_missing_app_log_still_renames_the_files(tmp_path):
    node = _item(tmp_path)
    _fail(tmp_path, node)
    (tmp_path / "app.log").unlink()

    archive_previous_attempt(node)

    assert (tmp_path / "xml_dumps" / f"{NAME}.attempt1.xml").exists()
    assert not (tmp_path / "app.log").exists()
    assert node._attempt_files == []


def test_a_block_of_another_attempt_is_not_rewritten(tmp_path):
    node = _item(tmp_path)
    node._attempt = 2
    _fail(tmp_path, node)
    node._attempt = 1  # the item claims attempt 1 ended, but the last block says attempt 2
    before = (tmp_path / "app.log").read_text()

    archive_previous_attempt(node)

    assert (tmp_path / "app.log").read_text() == before
    assert (tmp_path / "xml_dumps" / f"{NAME}.attempt1.xml").exists()


def test_a_recorded_file_that_no_longer_exists_is_skipped(tmp_path):
    node = _item(tmp_path)
    _fail(tmp_path, node)
    (tmp_path / "screenshots" / f"{NAME}.png").unlink()

    archive_previous_attempt(node)

    assert _evidence(tmp_path)[0] == [f"xml_dumps/{NAME}.attempt1.xml",
                                      f"screenshots/{NAME}.png"]


def test_a_run_dir_of_none_does_not_raise_and_clears_the_list(tmp_path):
    node = _item(tmp_path)
    node.config._run_dir = None
    note_attempt_file(node, f"xml_dumps/{NAME}.xml")

    archive_previous_attempt(node)

    assert node._attempt_files == []


def test_an_item_with_no_previous_files_is_not_touched():
    class Tripwire:
        @property
        def _run_dir(self):
            raise AssertionError("the no-op path read the run dir")

    node = SimpleNamespace(name=NAME, config=Tripwire(), _attempt=1)
    archive_previous_attempt(node)
    node._attempt_files = []
    archive_previous_attempt(node)
