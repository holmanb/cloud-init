#!/usr/bin/env python3

import networkx as nx
from pprint import pprint
import pygraphviz
import matplotlib.pyplot as plt

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
MEMBERSHIP_ATTRS = ["black", "gold", "grey66", "black"]
CLOUD_INIT_UNITS = ["cloud-init.target", "cloud-init-local.service", "cloud-init-network.service", "cloud-config.service", "cloud-final.service", "cloud-init-main.service"]

SHUTDOWN = "shutdown.target"
DEFAULT = "graphical.target"

order_edges = []
membership_edges = []

full_graph = nx.DiGraph(nx.nx_agraph.read_dot("./out/dot-oracular-cleaned.dot"))

for source, dest, attr in full_graph.edges.data("color"):
    if attr in CONFLICTS or source == SHUTDOWN or dest == SHUTDOWN:
        continue
    elif attr in ORDER_ATTR:
        order_edges.append((source, dest))
    elif attr in MEMBERSHIP_ATTRS:
        membership_edges.append((source, dest))

# order_graph contains all ordering relationships
order_graph = full_graph.edge_subgraph(order_edges)
assert nx.is_directed_acyclic_graph(order_graph)

# membership_graph contains all membership relationships between nodes
membership_graph = full_graph.edge_subgraph(membership_edges)

boot_target_nodes = []
for node in membership_graph.nodes:
    if node not in order_graph:
        print(f"node: {node} not in order_graph: {order_graph}")
    if node in nx.descendants(membership_graph, DEFAULT):
        boot_target_nodes.append(node)
breakpoint()

# boot_target contains all nodes required in the boot target
boot_target = order_graph.subgraph(boot_target_nodes)

found_cloud_init_units = []
cloud_init_neighbors = []
for node in boot_target.nodes:
    if node in CLOUD_INIT_UNITS:
        found_cloud_init_units.append(node)
        for neighbor in boot_target.neighbors(node):
            cloud_init_neighbors.append(neighbor)
assert set(found_cloud_init_units) == set(CLOUD_INIT_UNITS)
breakpoint()

# identify the _order_ of all nodes that are members of the boot transaction
# identified by the boot target graphical.target
member_order = order_graph.subgraph(boot_target.nodes)
assert nx.is_directed_acyclic_graph(member_order)
breakpoint()

# identify only nodes that are descendants of graphical.target
# graph = member_order.subgraph(nx.ancestors(member_order, DEFAULT))
#nx.transitive_reduction(member_order)
#graph = member_order

graph = nx.reverse_view(member_order)

# https://networkx.org/documentation/stable/auto_examples/graph/plot_dag_layout.html#sphx-glr-auto-examples-graph-plot-dag-layout-py
for layer, nodes in enumerate(nx.topological_generations(graph)):
    # `multipartite_layout` expects the layer as a node attribute, so add the
    # numeric layer value as a node attribute
    for node in nodes:
        graph.nodes[node]["layer"] = layer

# Compute the multipartite_layout using the "layer" node attribute
pos = nx.multipartite_layout(graph, subset_key="layer")

fig, ax = plt.subplots()
nx.draw_networkx(graph, pos=pos, ax=ax)
ax.set_title("DAG layout in topological order")
fig.set_layout_engine(layout="constrained")
plt.show()

#breakpoint()
