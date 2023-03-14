from pathlib import Path
from unittest.mock import MagicMock, call, patch

import PIL
import pytest
from ophyd.sim import make_fake_device
from requests import HTTPError, Response

import dodal.devices.oav.utils as oav_utils
from dodal.devices.oav.zoom_controller import OAV
from dodal.utils import Point2D


@pytest.fixture
def fake_oav() -> OAV:
    FakeOAV = make_fake_device(OAV)
    fake_oav: OAV = FakeOAV(name="test")

    fake_oav.snapshot.url.sim_put("http://test.url")
    fake_oav.snapshot.filename.put("test filename")
    fake_oav.snapshot.directory.put("test directory")
    fake_oav.snapshot.top_left_x.put(100)
    fake_oav.snapshot.top_left_y.put(100)
    fake_oav.snapshot.box_width.put(50)
    fake_oav.snapshot.num_boxes_x.put(15)
    fake_oav.snapshot.num_boxes_y.put(10)
    return fake_oav


@patch("requests.get")
def test_snapshot_trigger_handles_request_with_bad_status_code_correctly(
    mock_get, fake_oav: OAV
):
    response = Response()
    response.status_code = 404
    mock_get.return_value = response

    st = fake_oav.snapshot.trigger()
    with pytest.raises(HTTPError):
        st.wait()


@patch("requests.get")
@patch("dodal.devices.oav.snapshot.Image")
def test_snapshot_trigger_loads_correct_url(mock_image, mock_get: MagicMock, fake_oav):
    st = fake_oav.snapshot.trigger()
    st.wait()
    mock_get.assert_called_once_with("http://test.url", stream=True)


@patch("requests.get")
@patch("dodal.devices.oav.snapshot.Image.open")
def test_snapshot_trigger_saves_to_correct_file(
    mock_open: MagicMock, mock_get, fake_oav
):
    image = PIL.Image.open("test")
    mock_save = MagicMock()
    image.save = mock_save
    mock_open.return_value = image
    st = fake_oav.snapshot.trigger()
    st.wait()
    expected_calls_to_save = [
        call(Path(f"test directory/test filename{addition}.png"))
        for addition in ["", "_outer_overlay", "_grid_overlay"]
    ]
    calls_to_save = mock_save.mock_calls
    assert calls_to_save == expected_calls_to_save


@patch("requests.get")
@patch("dodal.devices.oav.snapshot.Image.open")
@patch("dodal.devices.oav.grid_overlay.add_grid_overlay_to_image")
@patch("dodal.devices.oav.grid_overlay.add_grid_border_overlay_to_image")
def test_correct_grid_drawn_on_image(
    mock_border_overlay: MagicMock,
    mock_grid_overlay: MagicMock,
    mock_open: MagicMock,
    mock_get: MagicMock,
    fake_oav: OAV,
):
    st = fake_oav.snapshot.trigger()
    st.wait()
    expected_border_calls = [call(mock_open.return_value, 100, 100, 50, 15, 10)]
    expected_grid_calls = [call(mock_open.return_value, 100, 100, 50, 15, 10)]
    actual_border_calls = mock_border_overlay.mock_calls
    actual_grid_calls = mock_grid_overlay.mock_calls
    assert actual_border_calls == expected_border_calls
    assert actual_grid_calls == expected_grid_calls


def test_bottom_right_from_top_left():
    top_left = Point2D(123, 123)
    bottom_right = oav_utils.bottom_right_from_top_left(
        top_left, 20, 30, 0.1, 0.15, 0.37, 0.37
    )
    assert bottom_right.x == 863 and bottom_right.y == 1788
    bottom_right = oav_utils.bottom_right_from_top_left(
        top_left, 15, 20, 0.005, 0.007, 1, 1
    )
    assert bottom_right.x == 198 and bottom_right.y == 263
