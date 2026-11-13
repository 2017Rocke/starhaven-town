"""Pure road geometry shared by the town renderer and its route planner.

Coordinates are world coordinates. Edges are undirected centre lines with square
caps; their bounds include those caps. No resident positions are changed here.
"""


def build_road_network(plot_slots, *, plot_size, core, world_origin, world_size,
                       street_width=64, verge=18, shared_width=32,
                       shared_speed=24, street_speed=48, pedestrian_accesses=()):
    """Connect every plot to the ring without crossing a plot or the village.

    A full corridor uses the same street width and shoulders as other rows.
    Legacy layouts with only forty pixels above the village retain a narrower
    shared lane, so their saved geometry remains supported.
    The wider middle street ends one half-width before either village edge, so
    even its square end caps stay outside the pedestrian square.
    """
    plot_width, plot_height = plot_size
    core_x, core_y, core_width, core_height = core
    origin_x, origin_y = world_origin
    world_width, world_height = world_size
    if not plot_slots or min(plot_width, plot_height, street_width, shared_width, world_width, world_height) <= 0:
        raise ValueError("Road dimensions and plot slots must be positive")
    rows = sorted({cy for _, cy in plot_slots})
    ring_centres = (origin_x + street_width / 2, origin_x + world_width - street_width / 2)
    nodes, edges, accesses, streets = {}, [], [], []
    incident = {}
    row_segments = {}
    pedestrian_entries = [dict(item) for item in pedestrian_accesses]

    def number(value):
        return int(value) if int(value) == value else value

    def node(x, y):
        x, y = number(x), number(y)
        identity = "n_%s_%s" % (x, y)
        nodes.setdefault(identity, dict(id=identity, x=x, y=y))
        incident.setdefault(identity, [])
        return identity

    def edge(start, end, width, surface, speed):
        first, last = nodes[start], nodes[end]
        if start == end or (first["x"] != last["x"] and first["y"] != last["y"]):
            raise ValueError("Road edges must be nonzero and axis aligned")
        half = width / 2
        bounds = [min(first["x"], last["x"]) - half, min(first["y"], last["y"]) - half,
                  max(first["x"], last["x"]) + half, max(first["y"], last["y"]) + half]
        item = dict(id="e_%s__%s" % (start, end), **{"from": start, "to": end},
                    width=width, surface=surface, speed=speed,
                    bounds=[number(value) for value in bounds],
                    length=abs(first["x"] - last["x"]) + abs(first["y"] - last["y"]))
        edges.append(item)
        incident[start].append(item)
        incident[end].append(item)

    for row in rows:
        bottom = row + plot_height / 2
        width, surface, speed = street_width, "street", street_speed
        centre = bottom + verge + width / 2
        crosses_village = centre - width / 2 < core_y + core_height and centre + width / 2 > core_y
        homes_over_village = any(cy == row and core_x < cx < core_x + core_width for cx, cy in plot_slots)
        if crosses_village and homes_over_village and bottom < core_y:
            gap = core_y - bottom
            if shared_width > gap - 8:
                raise ValueError("The northern shared lane needs four pixels clear of each neighbour")
            centre = bottom + gap / 2
            width, surface, speed = shared_width, "shared", shared_speed
        crosses_village = centre - width / 2 < core_y + core_height and centre + width / 2 > core_y
        segments = ([(ring_centres[0], core_x - width / 2),
                     (core_x + core_width + width / 2, ring_centres[1])]
                    if crosses_village else [(ring_centres[0], ring_centres[1])])
        row_segments[row] = (centre, segments)
        streets.append(dict(row=number(row), centre=number(centre), top=number(centre - width / 2),
                            width=width, surface=surface, speed=speed))
        entrances = {cx for cx, cy in plot_slots if cy == row}
        entrances.update(item["point"][0] for item in pedestrian_entries if item["point"][1] == centre)
        for left, right in segments:
            if left >= right:
                raise ValueError("Village clearance leaves no street")
            points = sorted({left, right, *(cx for cx in entrances if left <= cx <= right)})
            identities = [node(x, centre) for x in points]
            for start, end in zip(identities, identities[1:]):
                edge(start, end, width, surface, speed)

    # Ring junctions share the same nodes as the horizontal streets. Their wider
    # caps remain strictly inside the two outer 64-pixel strips, including where
    # the narrow northern lane joins the ring.
    for ring in ring_centres:
        identities = [node(ring, street["centre"]) for street in streets]
        for start, end in zip(identities, identities[1:]):
            edge(start, end, street_width, "street", street_speed)

    for slot, (cx, cy) in enumerate(plot_slots):
        centre, segments = row_segments[cy]
        if not any(left <= cx <= right for left, right in segments):
            raise ValueError("A resident entrance has no real street surface")
        identity = node(cx, centre)
        if not incident[identity]:
            raise ValueError("A resident entrance is disconnected")
        accesses.append(dict(slot=slot, node=identity,
                             doorstep=[number(cx), number(cy + plot_height / 2)],
                             point=[number(cx), number(centre)]))

    for item in pedestrian_entries:
        x, y = item["point"]
        start_x, start_y = item["doorstep"]
        identity = "n_%s_%s" % (number(x), number(y))
        if identity not in nodes or not incident[identity] or start_x != x:
            raise ValueError("A pedestrian entrance must meet a road node vertically")
        half = item["width"] / 2
        if half <= 0:
            raise ValueError("Pedestrian width must be positive")
        item["node"] = identity
        item["doorstep"] = [number(start_x), number(start_y)]
        item["point"] = [number(x), number(y)]
        item["bounds"] = [number(x - half), number(min(y, start_y)),
                          number(x + half), number(max(y, start_y))]

    junctions = []
    for identity, item in nodes.items():
        connected = incident[identity]
        width = max(link["width"] for link in connected)
        half = width / 2
        junctions.append(dict(node=identity, width=width,
            surface="street" if any(link["surface"] == "street" for link in connected) else "shared",
            bounds=[number(item["x"] - half), number(item["y"] - half),
                    number(item["x"] + half), number(item["y"] + half)]))
    return dict(version=1, nodes=list(nodes.values()), edges=edges, accesses=accesses,
                junctions=junctions, streets=streets, pedestrian_accesses=pedestrian_entries,
                bounds=[origin_x, origin_y, origin_x + world_width, origin_y + world_height])
