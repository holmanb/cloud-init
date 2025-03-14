#!/usr/bin/env python3

import time
from copy import deepcopy

import networkx as nx
from pprint import pprint
import pygraphviz
import matplotlib.pyplot as plt
import matplotlib as mpl

# TODO: note to future self
# it's probably easier to just iterate over every combination of nodes
# referenced in cloud-init's unit files
#
# setup
# -----
# sudo apt install libgraphviz-dev
# python3 -m venv .venv
# . .venv/bin/activate
# pip install networkx pygraphviz matplotlib
#
# Legend: systemd-analyze dot
# ---------------------------
#
#   Color legend: black     = Requires
#                 dark blue = Requisite
#                 gold      = BindsTo
#                 dark grey = Wants
#                 red       = Conflicts
#                 green     = After
#


CONFLICTS = "red"
ORDER_ATTR = "green"
MEMBERSHIP_ATTRS = ["black", "gold", "grey66", "black", "darkblue"]
CLOUD_INIT_UNITS = [
        "cloud-init.target",
        "cloud-init-local.service",
        "cloud-init-network.service",
        "cloud-config.service",
        "cloud-final.service",
        "cloud-init-main.service"
]

SHUTDOWN = "shutdown.target"
DEFAULT = "graphical.target"

order_edges = []
requirement_edges = []

full_graph = nx.DiGraph(
    nx.nx_agraph.read_dot("./out/dot-oracular-cleaned.dot")
)
order_graph_raw = nx.DiGraph(
    nx.nx_agraph.read_dot("./out/dot-order-oracular-cleaned.dot")
)
require_graph = nx.DiGraph(
    nx.nx_agraph.read_dot("./out/dot-require-oracular-cleaned.dot")
)

assert nx.is_directed_acyclic_graph(order_graph_raw)
assert nx.has_path(order_graph_raw, source="cloud-init.target", target="cloud-config.service")
assert nx.has_path(order_graph_raw, source="cloud-init-local.service", target="cloud-init-main.service")
assert nx.has_path(order_graph_raw, source="cloud-init-network.service", target="cloud-init-main.service")
assert nx.has_path(order_graph_raw, source="cloud-config.service", target="cloud-init-main.service")
assert nx.has_path(order_graph_raw, source="cloud-final.service", target="cloud-init-main.service")
assert nx.has_path(order_graph_raw, source="cloud-init.target", target="cloud-init-local.service")

#for edge in full_graph.edges(data=True):
#    src, dst, attr = edge
#    if src == "cloud-init.target":
#        print(src, dst, attr)


for source, dest, attr in require_graph.edges.data("color"):
    if attr == CONFLICTS or source == SHUTDOWN or dest == SHUTDOWN:
        continue
    elif attr in MEMBERSHIP_ATTRS:
        requirement_edges.append((source, dest))
    else:
        breakpoint()

for source, dest, attr in order_graph_raw.edges.data("color"):
    if source == SHUTDOWN or dest == SHUTDOWN:
        continue
    elif attr == ORDER_ATTR:
        order_edges.append((source, dest))
    else:
        breakpoint()

# order_graph contains all ordering relationships
order_graph = full_graph.edge_subgraph(order_edges)

# requirement_graph contains all membership relationships between nodes
requirement_graph = require_graph.edge_subgraph(requirement_edges)

# get descendants of the default target from the requirement graph
default_descendants = nx.descendants(requirement_graph, DEFAULT)
non_descendants = requirement_graph.nodes - default_descendants
boot_order = order_graph.subgraph(default_descendants)
print("non-descendants:")
pprint(non_descendants)


# find neighbors of cloud-init units
neighbors = [*CLOUD_INIT_UNITS]
for unit in CLOUD_INIT_UNITS:
    assert unit in boot_order.nodes
    for neighbor in boot_order.neighbors(unit):
        neighbors.append(neighbor)
neighbors = list(set(neighbors))
print("neighbors:")
pprint(neighbors)

paths_between_neighbors = set()
start = time.time()
print(f"getting simple paths for {neighbors}")
for src in neighbors:
    nodes = []
    # get all paths via reachable nodes
    for node in neighbors:
        if nx.has_path(boot_order, source=src, target=node):
            nodes.append(node)
    print(f"getting simple paths for {src} -> {nodes}")
    for path in nx.all_simple_paths(boot_order, source=src, target=nodes):
        paths_between_neighbors.update(set(path))

print(f"getting paths took {time.time() - start}s")
breakpoint()
paths_between_neighbors = list(set(paths_between_neighbors))
paths_between_neighbors.append(DEFAULT)
print("paths between neighbors:")
pprint(paths_between_neighbors)

cloud_init_graph = boot_order.subgraph(paths_between_neighbors)

reversed= nx.reverse_view(cloud_init_graph)
reduced = nx.transitive_reduction(reversed)
if set(reduced.edges()) != set(reversed.edges()):
    print("transitive reduction did nothing!")
    breakpoint()
graph = reversed


# https://networkx.org/documentation/stable/auto_examples/graph/plot_dag_layout.html#sphx-glr-auto-examples-graph-plot-dag-layout-py
for layer, nodes in enumerate(nx.topological_generations(graph)):
    # `multipartite_layout` expects the layer as a node attribute, so add the
    # numeric layer value as a node attribute
    for node in nodes:
        graph.nodes[node]["layer"] = layer

# Compute the multipartite_layout using the "layer" node attribute
pos = nx.multipartite_layout(graph, subset_key="layer")

fig, ax = plt.subplots()
nx.draw_networkx(graph, pos=pos, ax=ax, with_labels=False,) #arrowsize=1, linewidths=0.1 )
nx.draw_networkx_labels(graph, pos=pos, ax=ax, verticalalignment="bottom", font_weight="normal")
ax.set_title("DAG layout in topological order")
fig.set_layout_engine(layout="constrained")
plt.show()

#breakpoint()
