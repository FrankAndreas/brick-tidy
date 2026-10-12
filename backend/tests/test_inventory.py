from app.services.inventory import (
    InventoryItem,
    equivalent_parts,
    get_inventory,
    get_set,
    normalize_set_num,
)


def test_normalize_set_num():
    assert normalize_set_num(" 10696 ") == "10696-1"
    assert normalize_set_num("10696-2") == "10696-2"


def test_import_parses_bools_and_rows(db):
    assert db.execute("SELECT is_trans FROM colors WHERE id = 4").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM inventory_parts WHERE is_spare = 1").fetchone()[0] == 1
    assert get_set(db, "1234")["name"] == "Test House"


def test_reimport_replaces_rows(db):
    from app.services.inventory import import_directory
    from tests.conftest import FIXTURES

    import_directory(db, FIXTURES)
    assert db.execute("SELECT COUNT(*) FROM inventory_parts").fetchone()[0] == 6


def test_inventory_uses_latest_version_skips_spares_and_includes_minifigs(db):
    assert get_inventory(db, "1234") == [
        InventoryItem("3001", 4, 4),
        InventoryItem("3001", 71, 1),
        InventoryItem("3003", 0, 2),
        InventoryItem("973c01", 4, 2),
    ]


def test_unknown_set_has_empty_inventory(db):
    assert get_inventory(db, "9999") == []


def test_equivalent_parts_follow_molds_and_prints(db):
    assert equivalent_parts(db, "3003a") == ["3003a", "3003"]
    assert equivalent_parts(db, "3003") == ["3003", "3003a"]
    assert equivalent_parts(db, "973") == ["973", "973c01"]
    assert equivalent_parts(db, "3001") == ["3001", "3001pr0001"]
    assert equivalent_parts(db, "nope") == ["nope"]
