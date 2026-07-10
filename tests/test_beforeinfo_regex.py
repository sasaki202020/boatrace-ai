from src.ingest.parsers.beforeinfo_parser import _find_number_before


def test_find_number_before_handles_labels_without_regex_range_error():
    text = "気温 23.4℃ 風速 3.2m 水温 18.5℃ 波高 1.0m"

    assert _find_number_before("気温", text) == 23.4
    assert _find_number_before("風速", text) == 3.2
    assert _find_number_before("水温", text) == 18.5
    assert _find_number_before("波高", text) == 1.0
