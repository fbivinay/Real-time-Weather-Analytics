from tools.build_basemap import clip_ring, douglas_peucker


def test_douglas_peucker_drops_collinear_points_and_keeps_corners():
    line = [(0, 0), (1, 0.001), (2, 0), (2, 2)]
    assert douglas_peucker(line, 0.01) == [(0, 0), (2, 0), (2, 2)]


def test_clip_ring_cuts_a_square_to_the_window():
    square = [(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)]
    clipped = clip_ring(square, (0, 0, 2, 2))
    assert all(0 <= x <= 2 and 0 <= y <= 2 for x, y in clipped)
    assert (1, 1) in clipped
