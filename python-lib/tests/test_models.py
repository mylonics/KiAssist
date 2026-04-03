"""Tests for the base data models."""

import pytest
from kiassist_utils.kicad_parser.models import (
    Position, Color, Stroke, FontConfig, Effects, Property, Pts, Fill, new_uuid,
)


class TestPosition:
    def test_default(self):
        p = Position()
        assert p.x == 0.0 and p.y == 0.0 and p.angle == 0.0

    def test_to_sexpr_no_angle(self):
        assert Position(1.0, 2.0).to_sexpr() == ["at", 1.0, 2.0]

    def test_to_sexpr_with_angle(self):
        assert Position(1.0, 2.0, 90.0).to_sexpr() == ["at", 1.0, 2.0, 90.0]

    def test_from_sexpr(self):
        p = Position.from_sexpr(["at", 10.0, 20.0, 45.0])
        assert p.x == 10.0 and p.y == 20.0 and p.angle == 45.0

    def test_round_trip(self):
        p = Position(5.5, -3.2, 180.0)
        assert Position.from_sexpr(p.to_sexpr()) == p


class TestStroke:
    def test_from_sexpr(self):
        s = Stroke.from_sexpr(["stroke", ["width", 0.254], ["type", "solid"]])
        assert s.width == 0.254 and s.type == "solid"

    def test_round_trip(self):
        s = Stroke(width=0.1, type="dash")
        s2 = Stroke.from_sexpr(s.to_sexpr())
        assert s2.width == s.width and s2.type == s.type


class TestEffects:
    def test_from_sexpr(self):
        e = Effects.from_sexpr(["effects", ["font", ["size", 1.27, 1.27]], "hide"])
        assert e.font.size_x == 1.27 and e.hide is True

    def test_round_trip(self):
        e = Effects(font=FontConfig(size_x=2.0, size_y=2.0, bold=True), justify="left", hide=True)
        e2 = Effects.from_sexpr(e.to_sexpr())
        assert e2.font.bold is True and e2.justify == "left" and e2.hide is True


class TestProperty:
    def test_from_sexpr(self):
        p = Property.from_sexpr(["property", "Reference", "R1", ["id", 0],
                                  ["at", 100.0, 50.0], ["effects", ["font", ["size", 1.27, 1.27]]]])
        assert p.key == "Reference" and p.value == "R1" and p.id == 0
        assert p.position.x == 100.0

    def test_round_trip(self):
        p = Property(key="Value", value="10k", id=1, position=Position(10, 20))
        p2 = Property.from_sexpr(p.to_sexpr())
        assert p2.key == p.key and p2.value == p.value


class TestPts:
    def test_from_sexpr(self):
        pts = Pts.from_sexpr(["pts", ["xy", 0.0, 0.0], ["xy", 10.0, 20.0]])
        assert len(pts.points) == 2
        assert pts.points[1].x == 10.0

    def test_round_trip(self):
        pts = Pts(points=[Position(0, 0), Position(5, 10)])
        pts2 = Pts.from_sexpr(pts.to_sexpr())
        assert len(pts2.points) == 2
        assert pts2.points[0].x == 0.0 and pts2.points[1].y == 10.0


class TestNewUuid:
    def test_returns_string(self):
        assert isinstance(new_uuid(), str)

    def test_unique(self):
        assert new_uuid() != new_uuid()
