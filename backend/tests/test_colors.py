import sqlite3

from app.db import init_db
from app.services.colors import MATCH_DISTANCE, color_distance, measure_color
from tests.images import BLUE, RED, make_photo


def test_measures_brick_color_and_ignores_background(tmp_path):
    photo = make_photo(tmp_path / "p.jpg", [(500, 400, 400, 250, BLUE)], size=(1000, 1400))
    measured = measure_color(photo)
    assert color_distance(measured, "0055BF") < MATCH_DISTANCE  # Rebrickable Blue
    assert color_distance(measured, "0055BF") < color_distance(measured, "0A3463")  # not Dark Blue


def test_no_brick_no_measurement(tmp_path):
    assert measure_color(make_photo(tmp_path / "p.jpg", [])) is None
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"x")
    assert measure_color(bad) is None


def test_washed_out_red_is_closer_to_red_than_dark_red():
    # Measured on a photo of a red plate: right lightness, lower saturation.
    assert color_distance("BD2C2E", "C91A09") < color_distance("BD2C2E", "720E0F")


def test_color_distance_is_zero_for_identical_colors():
    assert color_distance("C91A09", "C91A09") == 0


def test_measures_red_brick(tmp_path):
    photo = make_photo(tmp_path / "p.jpg", [(500, 400, 400, 250, RED)], size=(1000, 1400))
    assert color_distance(measure_color(photo), "C91A09") < 10


def test_migration_adds_color_column_to_existing_database(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE detections (id INTEGER PRIMARY KEY, photo_id INTEGER)")
    init_db(path)
    init_db(path)  # idempotent
    with sqlite3.connect(path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(detections)")]
    assert columns == ["id", "photo_id", "color"]
