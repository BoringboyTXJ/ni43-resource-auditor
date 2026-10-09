import pytest

from resource_auditor.config import RoleConfig, Settings
from resource_auditor.models import GroundTruth
from resource_auditor.pdf import parse_pages


def test_page_selection_rejects_out_of_bounds_and_accepts_ranges():
    assert parse_pages("1, 3-5,3", 5) == [1, 3, 4, 5]
    for spec in ("0", "6", "4-2", "abc", "1,"):
        with pytest.raises(ValueError):
            parse_pages(spec, 5)


def test_same_model_is_not_an_independent_critic():
    role = RoleConfig("https://example.test/v1", "same-model", "test-key")
    with pytest.raises(ValueError, match="different"):
        Settings(role, role).validate()


def test_ground_truth_is_a_separate_contract():
    assert "ground_truth" not in Settings.__dataclass_fields__
    with pytest.raises(ValueError):
        GroundTruth.model_validate({"rows": []})
