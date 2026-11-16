import pytest

from bushido.workout import WorkoutRun, parse_zwo

ZWO = """<workout_file><name>Test</name><description>d</description><workout>
  <Warmup Duration="100" PowerLow="0.4" PowerHigh="0.8"/>
  <IntervalsT Repeat="2" OnDuration="30" OffDuration="20" OnPower="1.2" OffPower="0.5"/>
  <FreeRide Duration="60"/>
  <textevent message="ignored"/>
  <SteadyState Duration="50" Power="0.7"/>
</workout></workout_file>"""


def test_parse_expands_intervals():
    w = parse_zwo(ZWO)
    assert w.name == "Test"
    assert [s.duration_s for s in w.segments] == [100, 30, 20, 30, 20, 60, 50]
    assert w.total_s == 310
    assert w.segments[1].label == "Intervalle 1/2"


def test_targets_follow_the_ramp_and_ftp():
    run = WorkoutRun(parse_zwo(ZWO), ftp_w=200)
    assert run.target_w() == pytest.approx(80)       # 0.4 x 200
    run.advance(50)
    assert run.target_w() == pytest.approx(120)      # halfway up the ramp
    run.advance(60)                                  # 10 s into interval 1
    assert run.position() == (1, 10)
    assert run.target_w() == pytest.approx(240)


def test_free_ride_has_no_target():
    run = WorkoutRun(parse_zwo(ZWO), ftp_w=200)
    run.advance(210)
    assert run.target_w() is None


def test_skip_goes_to_the_next_segment_and_finish():
    run = WorkoutRun(parse_zwo(ZWO), ftp_w=200)
    run.advance(10)
    run.skip()
    assert run.position() == (1, 0)
    for _ in range(10):
        run.skip()
    assert run.finished and run.elapsed_s == 310


@pytest.mark.parametrize("text", ["not xml", "<workout_file/>",
                                  "<workout_file><workout/></workout_file>",
                                  '<workout_file><workout><SteadyState Power="1"/></workout></workout_file>'])
def test_bad_files_raise_value_error(text):
    with pytest.raises(ValueError):
        parse_zwo(text)
