from app.database import _mongo_target_label


def test_atlas_log_label_hides_credentials():
    uri = "mongodb+srv://plane_user:super-secret@cluster0.example.mongodb.net/?retryWrites=true&w=majority"
    label = _mongo_target_label(uri)
    assert label == "mongodb+srv://cluster0.example.mongodb.net"
    assert "plane_user" not in label
    assert "super-secret" not in label


def test_local_mongo_log_label_is_safe():
    assert _mongo_target_label("mongodb://localhost:27017") == "mongodb://localhost"
