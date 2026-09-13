from scripts.reset_northflank_projects import _items


def test_items_accepts_northflank_data_results_shape():
    payload = {"data": {"results": [{"id": "p1", "name": "Old"}, {"id": "p2"}]}}
    assert [item["id"] for item in _items(payload)] == ["p1", "p2"]


def test_items_accepts_direct_data_list():
    payload = {"data": [{"id": "p1"}]}
    assert _items(payload) == [{"id": "p1"}]


def test_items_ignores_non_project_values():
    assert _items({"data": {"results": [None, "bad", {"id": "ok"}]}}) == [{"id": "ok"}]
