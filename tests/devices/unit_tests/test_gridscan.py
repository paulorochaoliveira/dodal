from asyncio import TimeoutError, wait_for
from dataclasses import dataclass

import numpy as np
import pytest
from bluesky import plan_stubs as bps
from bluesky import preprocessors as bpp
from bluesky.run_engine import RunEngine
from ophyd.status import DeviceStatus, Status
from ophyd_async.core import DeviceCollector, get_mock_put, set_mock_value

from dodal.devices.fast_grid_scan import (
    FastGridScanCommon,
    PandAFastGridScan,
    PandAGridScanParams,
    ZebraFastGridScan,
    ZebraGridScanParams,
    set_fast_grid_scan_params,
)
from dodal.devices.smargon import Smargon


def discard_status(st: Status | DeviceStatus):
    try:
        st.wait(0.01)
    except BaseException:
        pass


@pytest.fixture
async def zebra_fast_grid_scan():
    async with DeviceCollector(mock=True):
        zebra_fast_grid_scan = ZebraFastGridScan(name="fake_FGS", prefix="FGS")

    return zebra_fast_grid_scan


@pytest.fixture
async def panda_fast_grid_scan():
    async with DeviceCollector(mock=True):
        panda_fast_grid_scan = PandAFastGridScan(name="fake_PGS", prefix="PGS")

    return panda_fast_grid_scan


@pytest.mark.parametrize(
    "use_pgs",
    [(False), (True)],
)
async def test_given_settings_valid_when_kickoff_then_run_started(
    use_pgs,
    zebra_fast_grid_scan: ZebraFastGridScan,
    panda_fast_grid_scan: PandAFastGridScan,
):
    grid_scan: ZebraFastGridScan | PandAFastGridScan = (
        panda_fast_grid_scan if use_pgs else zebra_fast_grid_scan
    )
    set_mock_value(grid_scan.scan_invalid, False)
    set_mock_value(grid_scan.position_counter, 0)
    set_mock_value(grid_scan.status, 1)

    await grid_scan.kickoff()

    get_mock_put(grid_scan.run_cmd).assert_called_once()


@pytest.mark.parametrize(
    "use_pgs",
    [(False), (True)],
)
async def test_waits_for_running_motion(
    use_pgs,
    zebra_fast_grid_scan: ZebraFastGridScan,
    panda_fast_grid_scan: PandAFastGridScan,
):
    grid_scan: ZebraFastGridScan | PandAFastGridScan = (
        panda_fast_grid_scan if use_pgs else zebra_fast_grid_scan
    )
    set_mock_value(grid_scan.motion_program.running, 1)

    grid_scan.KICKOFF_TIMEOUT = 0.01

    with pytest.raises(TimeoutError):
        await grid_scan.kickoff()

    grid_scan.KICKOFF_TIMEOUT = 1

    set_mock_value(grid_scan.motion_program.running, 0)
    set_mock_value(grid_scan.status, 1)
    await grid_scan.kickoff()
    get_mock_put(grid_scan.run_cmd).assert_called_once()


@pytest.mark.parametrize(
    "steps, expected_images",
    [
        ((10, 10, 0), 100),
        ((30, 5, 10), 450),
        ((7, 0, 5), 35),
    ],
)
async def test_given_different_step_numbers_then_expected_images_correct(
    zebra_fast_grid_scan: ZebraFastGridScan, steps, expected_images
):
    set_mock_value(zebra_fast_grid_scan.x_steps, steps[0])
    set_mock_value(zebra_fast_grid_scan.y_steps, steps[1])
    set_mock_value(zebra_fast_grid_scan.z_steps, steps[2])

    assert await zebra_fast_grid_scan.expected_images.get_value() == expected_images


@pytest.mark.parametrize(
    "use_pgs",
    [(False), (True)],
)
async def test_running_finished_with_all_images_done_then_complete_status_finishes_not_in_error(
    use_pgs,
    zebra_fast_grid_scan: ZebraFastGridScan,
    panda_fast_grid_scan: PandAFastGridScan,
    RE: RunEngine,
):
    num_pos_1d = 2
    if use_pgs:
        grid_scan = panda_fast_grid_scan
        RE(
            set_fast_grid_scan_params(
                grid_scan,
                PandAGridScanParams(
                    transmission_fraction=0.01, x_steps=num_pos_1d, y_steps=num_pos_1d
                ),
            )
        )
    else:
        grid_scan = zebra_fast_grid_scan
        RE(
            set_fast_grid_scan_params(
                grid_scan,
                ZebraGridScanParams(
                    transmission_fraction=0.01, x_steps=num_pos_1d, y_steps=num_pos_1d
                ),
            )
        )
    set_mock_value(grid_scan.status, 1)

    complete_status = grid_scan.complete()
    assert not complete_status.done
    set_mock_value(grid_scan.position_counter, num_pos_1d**2)
    set_mock_value(grid_scan.status, 0)

    await wait_for(complete_status, 0.1)

    assert complete_status.done
    assert complete_status.exception() is None


@pytest.fixture
def motor_bundle_with_limits(smargon) -> Smargon:
    for axis in [
        smargon.x,
        smargon.y,
        smargon.z,
    ]:
        set_mock_value(axis.low_limit_travel, 0)
        set_mock_value(axis.high_limit_travel, 10)

    return smargon


@dataclass
class CompositeWithSmargon:
    smargon: Smargon


@pytest.fixture
def composite_with_smargon(motor_bundle_with_limits):
    return CompositeWithSmargon(motor_bundle_with_limits)


@pytest.mark.parametrize(
    "position, expected_in_limit",
    [
        (-1, False),
        (20, False),
        (5, True),
    ],
)
async def test_within_limits_check(
    RE: RunEngine, motor_bundle_with_limits, position, expected_in_limit
):
    def check_limits_plan():
        limits = yield from motor_bundle_with_limits.get_xyz_limits()
        assert limits.x.contains(position) == expected_in_limit

    RE(check_limits_plan())


PASSING_LINE_1 = (1, 5, 1)
PASSING_LINE_2 = (0, 10, 0.5)
FAILING_LINE_1 = (-1, 20, 0.5)
PASSING_CONST = 6
FAILING_CONST = 15


def check_parameter_validation(params, composite, expected_in_limits):
    if expected_in_limits:
        yield from params.validate_against_hardware(composite)
    else:
        with pytest.raises(ValueError):
            yield from params.validate_against_hardware(composite)


@pytest.fixture
def zebra_grid_scan_params():
    yield ZebraGridScanParams(
        transmission_fraction=0.01,
        x_steps=10,
        y_steps=15,
        z_steps=20,
        x_step_size_mm=0.3,
        y_step_size_mm=0.2,
        z_step_size_mm=0.1,
        x_start_mm=0,
        y1_start_mm=1,
        y2_start_mm=2,
        z1_start_mm=3,
        z2_start_mm=4,
    )


@pytest.fixture
def panda_grid_scan_params():
    yield PandAGridScanParams(
        transmission_fraction=0.01,
        x_steps=10,
        y_steps=15,
        z_steps=20,
        x_step_size_mm=0.3,
        y_step_size_mm=0.2,
        z_step_size_mm=0.1,
        x_start_mm=0,
        y1_start_mm=1,
        y2_start_mm=2,
        z1_start_mm=3,
        z2_start_mm=4,
    )


@pytest.mark.parametrize(
    "grid_position",
    [
        (np.array([-1, 2, 4])),
        (np.array([11, 2, 4])),
        (np.array([1, 17, 4])),
        (np.array([1, 5, 22])),
    ],
)
def test_given_x_y_z_out_of_range_then_converting_to_motor_coords_raises(
    zebra_grid_scan_params: ZebraGridScanParams,
    panda_grid_scan_params: PandAGridScanParams,
    grid_position,
):
    with pytest.raises(IndexError):
        zebra_grid_scan_params.grid_position_to_motor_position(grid_position)
    with pytest.raises(IndexError):
        panda_grid_scan_params.grid_position_to_motor_position(grid_position)


def test_given_x_y_z_of_origin_when_get_motor_positions_then_initial_positions_returned(
    zebra_grid_scan_params: ZebraGridScanParams,
    panda_grid_scan_params: PandAGridScanParams,
):
    motor_positions = [
        zebra_grid_scan_params.grid_position_to_motor_position(np.array([0, 0, 0])),
        panda_grid_scan_params.grid_position_to_motor_position(np.array([0, 0, 0])),
    ]
    assert [np.allclose(position, np.array([0, 1, 4])) for position in motor_positions]


@pytest.mark.parametrize(
    "grid_position, expected_x, expected_y, expected_z",
    [
        (np.array([1, 1, 1]), 0.3, 1.2, 4.1),
        (np.array([2, 11, 16]), 0.6, 3.2, 5.6),
        (np.array([6, 5, 5]), 1.8, 2.0, 4.5),
    ],
)
def test_given_various_x_y_z_when_get_motor_positions_then_expected_positions_returned(
    zebra_grid_scan_params: ZebraGridScanParams,
    panda_grid_scan_params: PandAGridScanParams,
    grid_position,
    expected_x,
    expected_y,
    expected_z,
):
    motor_positions = [
        zebra_grid_scan_params.grid_position_to_motor_position(grid_position),
        panda_grid_scan_params.grid_position_to_motor_position(grid_position),
    ]
    [
        np.testing.assert_allclose(
            position, np.array([expected_x, expected_y, expected_z])
        )
        for position in motor_positions
    ]


@pytest.mark.parametrize(
    "pgs",
    [(False), (True)],
)
def test_can_run_fast_grid_scan_in_run_engine(
    pgs,
    zebra_fast_grid_scan: ZebraFastGridScan,
    panda_fast_grid_scan: PandAFastGridScan,
    RE: RunEngine,
):
    @bpp.run_decorator()
    def kickoff_and_complete(device: FastGridScanCommon):
        yield from bps.kickoff(device, group="kickoff")
        set_mock_value(device.status, 1)
        yield from bps.wait("kickoff")
        yield from bps.complete(device, group="complete")
        set_mock_value(device.status, 0)
        yield from bps.wait("complete")

    (
        RE(kickoff_and_complete(panda_fast_grid_scan))
        if pgs
        else RE(kickoff_and_complete(zebra_fast_grid_scan))
    )
    assert RE.state == "idle"


def test_given_x_y_z_steps_when_full_number_calculated_then_answer_is_as_expected(
    zebra_grid_scan_params: ZebraGridScanParams,
    panda_grid_scan_params: PandAGridScanParams,
):
    assert zebra_grid_scan_params.get_num_images() == 350
    assert panda_grid_scan_params.get_num_images() == 350


@pytest.mark.parametrize(
    "test_dwell_times, expected_dwell_time_is_integer",
    [
        (9000, True),
        (1000, True),
        (100.1, True),
        (100.09, True),
        (150.7, False),
        (10, True),
        (99, True),
        (59, True),
        (0.4, False),
        (0.9, False),
        (0.44, False),
        (0.99, False),
        (0.01, False),
        (0.09, False),
        (0.001, False),
        (0.009, False),
    ],
)
def test_non_test_integer_dwell_time(test_dwell_times, expected_dwell_time_is_integer):
    if expected_dwell_time_is_integer:
        params = ZebraGridScanParams(
            dwell_time_ms=test_dwell_times,
            transmission_fraction=0.01,
        )
        assert params.dwell_time_ms == test_dwell_times
    else:
        with pytest.raises(ValueError):
            ZebraGridScanParams(
                dwell_time_ms=test_dwell_times,
                transmission_fraction=0.01,
            )
