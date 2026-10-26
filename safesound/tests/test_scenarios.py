import pytest

from safesound.scenarios import SCENARIOS


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_reference_scenarios(sim, scenario):
    """The proposal's scenario table, end to end: device code -> alert -> VMS -> fusion -> routing."""
    res = sim.run_scenario(scenario)["response"]
    assert abs(res["score"] - scenario.expected_score) <= 5, res
    assert res["level"] == scenario.expected_level
    assert res["action"] == scenario.expected_action
