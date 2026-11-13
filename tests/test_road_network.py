"""Independently check the painted road area and all real resident entrances."""
import copy
import json
import math
import unittest

from town_roads import build_road_network


# Approved layout: only the northern row moved sixty pixels from its saved
# 1.12.0 position. Every other row and every slot retain their coordinates.
PLOTS = [
    (800, 910), (800, 530), (800, 1290), (2080, 910), (2080, 530), (2080, 1290),
    (1197, 90), (1563, 90), (1202, 1670), (1558, 1670),
    (480, 910), (480, 530), (480, 1290), (2400, 910), (2400, 530), (2400, 1290),
    (160, 910), (840, 90), (1920, 90), (840, 1670), (1920, 1670),
    (160, 530), (160, 1290), (2720, 910), (2720, 530), (2720, 1290),
    (480, 90), (2400, 1670),
]
WORLD = [-64, -580, 2944, 1940]
VILLAGE = [960, 330, 1920, 990]
HOMES = [[cx - 160, cy - 140, cx + 160, cy + 140] for cx, cy in PLOTS]


def overlap(first, second):
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def contains(bounds, point):
    return bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]


def graph():
    return build_road_network(PLOTS, plot_size=(320, 280), core=(960, 330, 960, 660),
                              world_origin=(-64, -580), world_size=(3008, 2520),
                              pedestrian_accesses=(
                                  dict(id='forest-path', doorstep=[1380, 227], point=[1380, 280], width=26),
                                  dict(id='village-path', doorstep=[1420, 330], point=[1420, 280],
                                       width=64, surface='street'),
                              ))


class RoadNetworkTest(unittest.TestCase):
    def setUp(self):
        self.network = graph()
        self.nodes = {node['id']: node for node in self.network['nodes']}
        self.surfaces = ([edge['bounds'] for edge in self.network['edges']]
                         + [item['bounds'] for item in self.network['junctions']]
                         + [item['bounds'] for item in self.network['pedestrian_accesses']])

    def test_network_is_deterministic_pure_and_matches_the_published_contract(self):
        before = copy.deepcopy(PLOTS)
        self.assertEqual(self.network, graph())
        self.assertEqual(PLOTS, before)
        json.dumps(self.network)
        self.assertEqual(self.network['version'], 1)
        self.assertEqual(len(self.nodes), len(self.network['nodes']))
        self.assertEqual(len({edge['id'] for edge in self.network['edges']}), len(self.network['edges']))
        self.assertEqual(self.network['bounds'], WORLD)
        for edge in self.network['edges']:
            self.assertEqual(edge['surface'], 'street')
            self.assertEqual(edge['width'], 64)
            self.assertEqual(edge['speed'], 48)
            self.assertIn(edge['from'], self.nodes)
            self.assertIn(edge['to'], self.nodes)
        from server import PLOT_SLOTS, CORE_X, CORE_Y, CORE_W, CORE_H
        self.assertEqual(PLOT_SLOTS, PLOTS)
        self.assertEqual((CORE_X, CORE_Y, CORE_W, CORE_H), (960, 330, 960, 660))

    def test_all_edge_rectangles_and_square_caps_stay_inside_world_and_clear_of_homes(self):
        for edge in self.network['edges']:
            a, b = self.nodes[edge['from']], self.nodes[edge['to']]
            self.assertNotEqual((a['x'], a['y']), (b['x'], b['y']))
            self.assertTrue(a['x'] == b['x'] or a['y'] == b['y'])
            half = edge['width'] / 2
            # Verify the actual drawable bounds, including the end caps. Merely
            # checking a centre line would miss roads that protrude into homes.
            expected = [min(a['x'], b['x']) - half, min(a['y'], b['y']) - half,
                        max(a['x'], b['x']) + half, max(a['y'], b['y']) + half]
            self.assertEqual(edge['bounds'], expected, edge['id'])
            self.assertEqual(edge['length'], abs(a['x'] - b['x']) + abs(a['y'] - b['y']))
        for rectangle in self.surfaces:
            with self.subTest(surface=rectangle):
                self.assertGreaterEqual(rectangle[0], WORLD[0])
                self.assertGreaterEqual(rectangle[1], WORLD[1])
                self.assertLessEqual(rectangle[2], WORLD[2])
                self.assertLessEqual(rectangle[3], WORLD[3])
                self.assertFalse(overlap(rectangle, VILLAGE))
                self.assertFalse(any(overlap(rectangle, home) for home in HOMES))

    def test_every_door_has_an_actual_connected_node_on_the_drawn_road(self):
        accesses = self.network['accesses']
        self.assertEqual(sorted(access['slot'] for access in accesses), list(range(28)))
        neighbours = {identity: set() for identity in self.nodes}
        for edge in self.network['edges']:
            neighbours[edge['from']].add(edge['to'])
            neighbours[edge['to']].add(edge['from'])
        seen, queue = set(), [accesses[0]['node']]
        while queue:
            identity = queue.pop()
            if identity in seen:
                continue
            seen.add(identity)
            queue.extend(neighbours[identity] - seen)
        self.assertEqual(seen, set(self.nodes))
        expected_y = {90: 280, 530: 720, 910: 1100, 1290: 1480, 1670: 1860}
        for access in accesses:
            cx, cy = PLOTS[access['slot']]
            node = self.nodes[access['node']]
            with self.subTest(slot=access['slot']):
                self.assertIn(access['node'], seen)
                self.assertEqual(access['doorstep'], [cx, cy + 140])
                self.assertEqual(access['point'], [cx, expected_y[cy]])
                self.assertEqual([node['x'], node['y']], access['point'])
                # In particular, the two northern doorways must land on a real
                # rectangle at their x coordinate, not just share a street y.
                self.assertTrue(any(contains(rectangle, access['point']) for rectangle in self.surfaces))
                driveway = [cx - 16, cy + 140, cx + 16, access['point'][1]]
                self.assertFalse(overlap(driveway, VILLAGE))
                self.assertFalse(any(overlap(driveway, home) for home in HOMES))

    def test_northern_street_matches_all_roads_and_leaves_both_shoulders_clear(self):
        north = [edge for edge in self.network['edges']
                 if self.nodes[edge['from']]['y'] == self.nodes[edge['to']]['y'] == 280]
        self.assertTrue(north)
        self.assertTrue(all(edge['width'] == 64 and edge['surface'] == 'street' and edge['speed'] == 48
                            for edge in north))
        self.assertTrue(all(edge['bounds'][1] == 248 and edge['bounds'][3] == 312 for edge in north))
        northern_homes = [home for home in HOMES if home[1] == -50]
        self.assertEqual(len(northern_homes), 5)
        self.assertEqual(248 - max(home[3] for home in northern_homes), 18)
        self.assertEqual(VILLAGE[1] - 312, 18)
        intervals = sorted((edge['bounds'][0], edge['bounds'][2]) for edge in north)
        right = intervals[0][1]
        for start, end in intervals[1:]:
            self.assertLessEqual(start, right)
            right = max(right, end)
        self.assertLessEqual(intervals[0][0], -32)
        self.assertGreaterEqual(right, 2912)
        for junction in self.network['junctions']:
            node = self.nodes[junction['node']]
            incident = [edge for edge in self.network['edges'] if node['id'] in (edge['from'], edge['to'])]
            self.assertEqual(junction['width'], max(edge['width'] for edge in incident))
            if node['y'] == 280 and node['x'] in (-32, 2912):
                allowed = [-64, 248, 0, 312] if node['x'] == -32 else [2880, 248, 2944, 312]
                self.assertEqual(junction['bounds'], allowed)
        middle_nodes = [node for node in self.nodes.values() if node['y'] == 720]
        self.assertIn(928, [node['x'] for node in middle_nodes])
        self.assertIn(1952, [node['x'] for node in middle_nodes])
        self.assertFalse(any(928 < node['x'] < 1952 for node in middle_nodes))

    def test_forest_and_village_walkways_meet_the_wide_northern_street(self):
        entries = {entry['id']: entry for entry in self.network['pedestrian_accesses']}
        self.assertEqual(entries['forest-path']['bounds'], [1367, 227, 1393, 280])
        self.assertEqual(entries['forest-path']['width'], 26)
        village = entries['village-path']
        self.assertEqual(village['bounds'], [1388, 280, 1452, 330])
        self.assertEqual((village['width'], village['surface']), (64, 'street'))
        self.assertEqual(village['doorstep'], [1420, VILLAGE[1]])
        for entry in entries.values():
            node = self.nodes[entry['node']]
            self.assertEqual([node['x'], node['y']], entry['point'])
            junction = next(item for item in self.network['junctions'] if item['node'] == node['id'])
            self.assertEqual(junction['width'], 64)
            self.assertEqual(junction['surface'], 'street')
            self.assertEqual(junction['bounds'][1:4:2], [248, 312])
            incident = [edge for edge in self.network['edges'] if node['id'] in (edge['from'], edge['to'])]
            self.assertEqual(len(incident), 2)
            self.assertTrue(all(edge['surface'] == 'street' for edge in incident))
        # Widening the drawn entrance does not add a car route through the core.
        self.assertFalse(any(VILLAGE[0] < node['x'] < VILLAGE[2]
                             and VILLAGE[1] <= node['y'] < VILLAGE[3]
                             for node in self.nodes.values()))

    def test_legacy_forty_pixel_gap_still_supports_a_shared_lane(self):
        legacy_plots = [(1197, 150), (1563, 150)]
        legacy = build_road_network(legacy_plots, plot_size=(320, 280), core=(960, 330, 960, 660),
                                    world_origin=(-64, -520), world_size=(3008, 2460))
        self.assertTrue(legacy['edges'])
        for edge in legacy['edges']:
            self.assertEqual((edge['surface'], edge['width'], edge['speed']), ('shared', 32, 24))
            self.assertEqual(edge['bounds'][1:4:2], [294, 326])
            self.assertFalse(overlap(edge['bounds'], VILLAGE))
            for cx, cy in legacy_plots:
                self.assertFalse(overlap(edge['bounds'], [cx - 160, cy - 140, cx + 160, cy + 140]))

    def test_painted_surface_is_connected_without_clipping_away_collisions(self):
        # Flood-fill the emitted rectangles themselves. Unlike the previous
        # check, do not remove obstacle pixels first and thereby conceal errors.
        pixels = set()
        for left, top, right, bottom in self.surfaces:
            pixels.update((x, y)
                          for x in range(math.ceil(left / 4) * 4, math.ceil(right / 4) * 4, 4)
                          for y in range(math.ceil(top / 4) * 4, math.ceil(bottom / 4) * 4, 4))
        seen, queue = {min(pixels)}, [min(pixels)]
        while queue:
            x, y = queue.pop()
            for neighbour in ((x + 4, y), (x - 4, y), (x, y + 4), (x, y - 4)):
                if neighbour in pixels and neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        self.assertEqual(seen, pixels)


if __name__ == '__main__':
    unittest.main()
