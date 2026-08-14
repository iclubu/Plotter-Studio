"""Tests for the API endpoints and WebSocket command processing."""
import pytest
from httpx import ASGITransport, AsyncClient
from server import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── SVG Preprocessing API ──

class TestPreprocessSvgEndpoint:
    @pytest.mark.anyio
    async def test_simple_svg(self, client):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<path d="M 0 0 L 50 50 L 100 0"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("test.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "paths" in data
        assert "bbox" in data
        assert "stats" in data
        assert data["stats"]["simplified_paths"] >= 1
        assert data["stats"]["total_points"] >= 2

    @pytest.mark.anyio
    async def test_svg_with_rect(self, client):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<rect x="10" y="10" width="80" height="80"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("rect.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["stats"]["simplified_paths"] >= 1
        # Rectangle has 5 points (closed)
        assert data["stats"]["total_points"] >= 4

    @pytest.mark.anyio
    async def test_svg_with_transform(self, client):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<g transform="translate(10, 20)">'
            '<path d="M 0 0 L 50 0"/>'
            '</g>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("t.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert data["stats"]["simplified_paths"] == 1
        path = data["paths"][0]
        # First point should be translated
        assert path[0][0] == pytest.approx(10, abs=0.1)
        assert path[0][1] == pytest.approx(20, abs=0.1)

    @pytest.mark.anyio
    async def test_svg_with_multiple_subpaths(self, client):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<path d="M 0 0 L 10 10 M 20 20 L 30 30 M 40 40 L 50 50"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("multi.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert data["stats"]["simplified_paths"] == 3

    @pytest.mark.anyio
    async def test_simplification_reduces_points(self, client):
        # Many collinear points
        points = " ".join(f"L {i} 0" for i in range(1, 100))
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            f'<path d="M 0 0 {points}"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("s.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "1"},
        )
        data = resp.json()
        # Collinear points should simplify to just 2
        assert data["stats"]["total_points"] == 2

    @pytest.mark.anyio
    async def test_invalid_svg(self, client):
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("bad.svg", b"not xml", "image/svg+xml")},
            data={"simplify": "0"},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    @pytest.mark.anyio
    async def test_empty_svg(self, client):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"></svg>'
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("empty.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert data["stats"]["simplified_paths"] == 0

    @pytest.mark.anyio
    async def test_bbox_correct(self, client):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<path d="M 10 20 L 30 40"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("bb.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        bbox = data["bbox"]
        assert bbox["minX"] == pytest.approx(10, abs=0.1)
        assert bbox["minY"] == pytest.approx(20, abs=0.1)
        assert bbox["maxX"] == pytest.approx(30, abs=0.1)
        assert bbox["maxY"] == pytest.approx(40, abs=0.1)

    @pytest.mark.anyio
    async def test_auto_simplify(self, client):
        """When simplify=0, auto-tolerance is used (0.1% of diagonal)."""
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000">'
            '<path d="M 0 0 L 500 0.01 L 1000 0"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("auto.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        # 0.01 deviation on 1414 diagonal = auto tolerance ~1.4, should simplify
        assert data["stats"]["total_points"] == 2

    @pytest.mark.anyio
    async def test_filled_default_black(self, client):
        """Paths without fill attribute default to 0.0 (black)."""
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<rect x="10" y="10" width="80" height="80"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("f.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert "filled" in data
        assert all(f == pytest.approx(0.0, abs=0.01) for f in data["filled"])

    @pytest.mark.anyio
    async def test_filled_fill_none(self, client):
        """Paths with fill='none' get filled=None."""
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<rect x="10" y="10" width="80" height="80" fill="none" stroke="black"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("fn.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert all(f is None for f in data["filled"])

    @pytest.mark.anyio
    async def test_filled_style_fill_none(self, client):
        """Paths with style='fill:none' get filled=None."""
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<path d="M 0 0 L 50 50 L 100 0 Z" style="fill:none;stroke:black"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("sn.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert all(f is None for f in data["filled"])

    @pytest.mark.anyio
    async def test_filled_inherited_from_group(self, client):
        """fill='none' on parent <g> is inherited by children."""
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<g fill="none" stroke="black">'
            '<rect x="10" y="10" width="80" height="80"/>'
            '</g>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("gi.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert all(f is None for f in data["filled"])

    @pytest.mark.anyio
    async def test_filled_grey_returns_brightness(self, client):
        """Grey fill returns a brightness value between 0 and 1."""
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<rect x="10" y="10" width="80" height="80" fill="#808080"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("grey.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        # #808080 = rgb(128,128,128) → brightness ≈ 0.502
        assert data["filled"][0] == pytest.approx(0.502, abs=0.01)

    @pytest.mark.anyio
    async def test_svg_with_circle(self, client):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<circle cx="50" cy="50" r="20" fill="none" stroke="black"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("circ.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert data["stats"]["simplified_paths"] == 1
        assert data["stats"]["total_points"] >= 12
        # Check path forms a closed loop around (50,50) with radius 20
        path = data["paths"][0]
        for x, y in path:
            dist = ((x - 50) ** 2 + (y - 50) ** 2) ** 0.5
            assert dist == pytest.approx(20, abs=0.5)

    @pytest.mark.anyio
    async def test_svg_with_ellipse(self, client):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<ellipse cx="50" cy="50" rx="30" ry="15" fill="none" stroke="black"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("ell.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert data["stats"]["simplified_paths"] == 1
        assert data["stats"]["total_points"] >= 12

    @pytest.mark.anyio
    async def test_svg_with_filled_circle(self, client):
        """Circle with fill='black' should report brightness 0.0."""
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<circle cx="50" cy="50" r="5" fill="black"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("fc.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert data["stats"]["simplified_paths"] == 1
        assert data["filled"][0] == pytest.approx(0.0, abs=0.01)

    @pytest.mark.anyio
    async def test_filled_mixed_colors(self, client):
        """Mix of filled, unfilled, and colored elements."""
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<rect x="0" y="0" width="50" height="50" fill="black"/>'
            '<rect x="50" y="0" width="50" height="50" fill="#cccccc"/>'
            '<rect x="0" y="50" width="50" height="50" fill="none" stroke="black"/>'
            '</svg>'
        )
        resp = await client.post(
            "/api/preprocess-svg",
            files={"file": ("mix.svg", svg.encode(), "image/svg+xml")},
            data={"simplify": "0"},
        )
        data = resp.json()
        assert data["filled"][0] == pytest.approx(0.0, abs=0.01)   # black
        assert data["filled"][1] == pytest.approx(0.8, abs=0.01)    # light grey
        assert data["filled"][2] is None                              # none


# ── Static file serving ──

class TestStaticServing:
    @pytest.mark.anyio
    async def test_index_page(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "Plotter Console" in resp.text

    @pytest.mark.anyio
    async def test_ports_api(self, client):
        resp = await client.get("/api/ports")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
