import struct
import unittest
import zlib

from tdflowmcp import network_diagram as diagram


def pixels(png):
    data = png[8:]
    chunks = {}
    while data:
        length, kind = struct.unpack(">I4s", data[:8])
        chunks[kind] = data[8:8 + length]
        data = data[12 + length:]
    return chunks, zlib.decompress(chunks[b"IDAT"])


class NetworkDiagramTests(unittest.TestCase):
    def test_png_contains_only_raster_and_shows_eligible_wire(self):
        nodes = [
            {"id": 1, "family": "TOP", "x": 0, "y": 0, "name": "image_not_using_this_name"},
            {"id": 2, "family": "CHOP", "x": 200, "y": 0},
        ]
        edge = {"source": 1, "target": 2, "output_index": 0, "input_index": 0}
        png = diagram.render(nodes, [edge])
        chunks, image = pixels(png)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(set(chunks), {b"IHDR", b"IDAT", b"IEND"})
        self.assertEqual(struct.unpack(">II", chunks[b"IHDR"][:8]), (diagram.WIDTH, diagram.HEIGHT))
        point = (3 * diagram.WIDTH + 1) * (diagram.HEIGHT // 2) + 1 + 3 * (diagram.WIDTH // 2)
        self.assertEqual(tuple(image[point:point + 3]), diagram.WIRE)
        self.assertEqual(png, diagram.render([dict(n, name="renamed") for n in nodes], [edge]))
        self.assertNotEqual(png, diagram.render(nodes, []))

    def test_off_limits_generation_is_refused(self):
        with self.assertRaises(diagram.RenderLimit):
            diagram.render([{}] * (diagram.MAX_NODES + 1), [])
        with self.assertRaises(diagram.RenderLimit):
            diagram.render([], [{}] * (diagram.MAX_EDGES + 1))

    def test_coincident_coordinates_still_fit_bounded_image(self):
        nodes = [{"id": n + 1, "family": "COMP", "x": 0, "y": 0} for n in range(diagram.MAX_NODES)]
        result = diagram.render(nodes, [])
        self.assertLess(len(result), diagram.MAX_PNG_BYTES)
        self.assertNotEqual(result, diagram.render([], []))
