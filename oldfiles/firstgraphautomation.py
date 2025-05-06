#!/usr/bin/env python3
import networkx as nx
import random
import os
import math # For ceiling division

# --------------------------------------------------------------------------
# Functions from RealTrafficSim.py (Graph Generation & Simulation)
# --------------------------------------------------------------------------

def generate_graph(num_nodes, density_factor, seed):
    """
    Generate a graph with density-dependent edge probability and speed limits.

    :param num_nodes: Number of nodes in the graph.
    :param density_factor: Value between 0 (open roads) and 1 (dense city).
    :param seed: Seed for random number generation.
    :return: A NetworkX graph with edge attributes.
    """
    random.seed(seed)
    # Ensure density factor is within bounds
    density_factor = max(0, min(1, density_factor))
    # Probability higher for denser graphs, minimum ensures some connectivity
    edge_probability = 0.05 + 0.3 * density_factor # Scale edge probability
    # Speed limits depend on density (urban vs rural)
    speed_limit_range = (30, 50) if density_factor > 0.5 else (70, 120)

    # Generate the base graph using Erdos-Renyi model
    # We retry a few times if the graph is initially empty or too sparse
    for _ in range(5): # Try up to 5 times
        G = nx.erdos_renyi_graph(n=num_nodes, p=edge_probability, seed=random.randint(1, 10000)) # Use different seed each try
        if G.number_of_edges() > 0:
            break
    else:
        print(f"[Warning] Could not generate a graph with edges after multiple attempts. Using last attempt.")


    # Assign speed limits, capacity, and initial traffic to edges
    for (u, v) in G.edges():
        G[u][v]['speed_limit'] = random.randint(*speed_limit_range)
        G[u][v]['capacity'] = random.randint(20, 100) # Capacity of the road segment
        G[u][v]['current_traffic'] = 0 # Initial traffic is zero

    return G

def calculate_travel_time(speed_limit, traffic, capacity):
    """ Calculates travel time considering congestion. """
    # Prevent division by zero if capacity is somehow zero
    if capacity <= 0:
        return float('inf') # Infinite time if no capacity

    congestion_factor = min(1.0, traffic / capacity) # Cap congestion at 1

    # Simple congestion model: speed decreases exponentially with congestion
    # Adjust the exponent base and multiplier as needed for desired effect
    if congestion_factor <= 0.1: # Less than 10% capacity, assume free flow
        effective_speed = speed_limit
    else:
        # Speed halves as congestion approaches capacity (adjust 10 for sensitivity)
        effective_speed = max(1, speed_limit / (2 ** (congestion_factor * 3))) # Ensure speed is at least 1km/h

    # Assuming edge represents 1 logical unit of distance (e.g., 1km)
    # Time = Distance / Speed. Convert speed km/h to km/min (divide by 60)
    if effective_speed <= 0:
        return float('inf')
    travel_time = 1 / (effective_speed / 60) # Time in minutes for 1 unit distance

    return travel_time

def find_fastest_route(G, source, destination):
    """ Finds the fastest route using Dijkstra's algorithm with dynamic weights. """
    try:
        # Define weight function for shortest_path based on current traffic
        def weight_function(u, v, data):
            # data is the edge attribute dict G[u][v]
            return calculate_travel_time(
                data.get('speed_limit', 60), # Default speed limit if missing
                data.get('current_traffic', 0),
                data.get('capacity', 50) # Default capacity if missing
            )

        # Find the shortest path using the dynamic weight function
        path = nx.shortest_path(G, source, destination, weight=weight_function)
        return path
    except nx.NetworkXNoPath:
        # Handle cases where no path exists between source and destination
        return None
    except Exception as e:
        print(f"[Error] Pathfinding failed between {source} and {destination}: {e}")
        return None


def simulate_traffic(G, num_cars, num_iterations, seed):
    """ Simulates traffic flow over several iterations. """
    random.seed(seed)
    nodes = list(G.nodes())
    if not nodes:
        print("[Warning] Graph has no nodes for traffic simulation.")
        return G

    print(f"Simulating {num_cars} cars for {num_iterations} iterations...")
    # Reset traffic before simulation
    for u, v in G.edges():
        G[u][v]['current_traffic'] = 0

    for iteration in range(num_iterations):
        cars_this_iteration = 0
        # Simulate each car for this iteration
        for _ in range(num_cars):
            # Randomly pick source and destination nodes
            source = random.choice(nodes)
            destination = random.choice(nodes)
            # Ensure source and destination are different
            while destination == source and len(nodes) > 1:
                destination = random.choice(nodes)

            if len(nodes) <= 1: continue # Skip if only one node

            # Find the fastest route based on current congestion
            path = find_fastest_route(G, source, destination)

            # If a path exists, increment traffic count along the path edges
            if path and len(path) > 1:
                cars_this_iteration += 1
                for i in range(len(path) - 1):
                    u, v = path[i], path[i + 1]
                    # Increment traffic count for the edge (handle both directions if needed)
                    if G.has_edge(u, v):
                         # Basic increment, could be more complex (e.g., time-based)
                        G[u][v]['current_traffic'] += 1
                    # If graph is undirected, the above check is sufficient.
                    # If directed, you might need G[v][u]['current_traffic'] += 1 depending on model.

        print(f"  Iteration {iteration + 1}: {cars_this_iteration} cars routed.")
        # Optional: Decay traffic slightly each iteration?
        # for u,v in G.edges(): G[u][v]['current_traffic'] *= 0.95

    return G

# --------------------------------------------------------------------------
# Kathara Lab Generation Function (Unchanged from previous version)
# --------------------------------------------------------------------------

def generate_lab_config_with_routers(num_clusters, min_machines, max_machines, client_image, router_image):
    """
    Generates Kathara lab configuration with dedicated routers per cluster.
    Args, Returns: See previous version.
    """
    config_lines = []
    startup_lines = []

    backbone_lan_name = "backbone0"
    backbone_subnet_prefix = "192.168.254"

    router_details = {}

    print(f"--- Generating Kathara config for {num_clusters} Clusters/Routers ---")

    # --- First Pass: Define Clusters, Machines, Routers, and IPs ---
    for cluster_id in range(1, num_clusters + 1):
        num_machines_in_cluster = random.randint(min_machines, max_machines)
        cluster_lan_name = f"lan{cluster_id}"
        router_name = f"router{cluster_id}"
        router_ip_on_lan = f"10.{cluster_id}.1.254"
        router_ip_on_backbone = f"{backbone_subnet_prefix}.{cluster_id}"

        router_details[cluster_id] = {
            "name": router_name,
            "ip_lan": router_ip_on_lan,
            "ip_backbone": router_ip_on_backbone
        }

        # Define Router for this Cluster
        print(f"  Defining {router_name} (interfaces on {cluster_lan_name} and {backbone_lan_name})")
        config_lines.append(f"{router_name}[image]={router_image}    $")
        config_lines.append(f"{router_name}[privileged]=true    $") # Assumed needed by router image
        config_lines.append(f"{router_name}[0]={cluster_lan_name}    $ip({router_ip_on_lan}/24);")
        config_lines.append(f"{router_name}[1]={backbone_lan_name}    $ip({router_ip_on_backbone}/24);")

        # Define Client Machines (Sensors) in this Cluster
        print(f"  Defining {num_machines_in_cluster} client machine(s) for cluster {cluster_id} on {cluster_lan_name}")
        for machine_id in range(1, num_machines_in_cluster + 1):
            machine_name = f"cluster{cluster_id}_machine{machine_id}"
            ip_address = f"10.{cluster_id}.1.{machine_id}"
            config_lines.append(f"{machine_name}[image]={client_image}    $")
            # Assign default gateway
            config_lines.append(f"{machine_name}[0]={cluster_lan_name}    $ip({ip_address}/24); to(default, {router_ip_on_lan});")
            # --- TODO LATER: Add command here to pass edge info if needed ---
            # Example: $ip(...); to(...); echo EDGE=u-v > /edge_info; echo TRAFFIC=N >> /edge_info;

        print(f"  Finished Kathara definition for cluster {cluster_id}")

    # --- Second Pass: Add RIP Configuration to Routers ---
    print("\n--- Adding RIP configuration to router definitions ---")
    for cluster_id in range(1, num_clusters + 1):
        router_name = router_details[cluster_id]["name"]
        lan_subnet = f"10.{cluster_id}.1.0/24"
        backbone_subnet = f"{backbone_subnet_prefix}.0/24"
        rip_command = f"rip({router_name}, {lan_subnet}, connected); rip({router_name}, {backbone_subnet}, connected);"
        print(f"  Configuring RIP for {router_name}...")

        router_eth1_line_start = f"{router_name}[1]={backbone_lan_name}"
        found_line_to_append = False
        for i in range(len(config_lines) - 1, -1, -1):
            line_parts = config_lines[i].split('$', 1)
            definition_part = line_parts[0].strip()
            if definition_part.startswith(router_eth1_line_start):
                existing_commands = line_parts[1].strip() if len(line_parts) > 1 else ""
                if existing_commands and not existing_commands.endswith(';'):
                    existing_commands += ";"
                separator = " " if existing_commands else ""
                config_lines[i] = f"{definition_part}    ${existing_commands}{separator}{rip_command}"
                # print(f"    Appended RIP config to line {i+1}") # Less verbose
                found_line_to_append = True
                break
        if not found_line_to_append:
            print(f"  Warning: Could not find config line for '{router_eth1_line_start}' to append RIP config.")

    print("--- Kathara Configuration Generation Complete ---")
    return "\n".join(config_lines), "\n".join(startup_lines)


# ======================================================
# Main Execution Part
# ======================================================

if __name__ == "__main__":

    # --- Graph Simulation Parameters ---
    graph_num_nodes = 25       # Number of intersections/locations
    graph_density_factor = 0.5 # Mix of urban/rural characteristics
    graph_seed = 42            # For reproducibility
    sim_num_cars = 150         # Number of vehicles simulated per iteration
    sim_num_iterations = 25    # Number of simulation steps
    CLUSTER_EDGE_RATIO = 4     # Target: 1 cluster (monitoring station) per this many edges

    print("+++ Starting Graph Simulation +++")
    # 1. Generate Graph
    G = generate_graph(graph_num_nodes, graph_density_factor, graph_seed)
    print(f"Generated initial graph with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")

    # Ensure graph is connected for pathfinding (use largest component if not)
    if not nx.is_connected(G):
        print("Graph is not connected. Finding largest connected component...")
        largest_cc = max(nx.connected_components(G), key=len)
        G = G.subgraph(largest_cc).copy()
        print(f"Using largest component with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")

    # Handle cases where the graph/component might be too small or empty
    if G.number_of_edges() == 0:
        print("[ERROR] Generated graph component has no edges. Cannot create lab.")
        exit(1)
    if G.number_of_nodes() <= 1:
         print("[ERROR] Generated graph component has 1 or 0 nodes. Cannot simulate traffic.")
         exit(1)

    # 2. Simulate Traffic
    G_simulated = simulate_traffic(G.copy(), sim_num_cars, sim_num_iterations, graph_seed) # Simulate on a copy
    print("Traffic simulation complete.")
    # (Optional) Print simulated traffic for verification
    # print("\n--- Simulated Traffic Levels ---")
    # for u, v, data in G_simulated.edges(data=True):
    #     print(f"Edge ({u}-{v}): Traffic = {data['current_traffic']}")


    # 3. Determine Number of Clusters based on Edges
    all_edges = list(G_simulated.edges(data=True))
    num_edges = len(all_edges)
    # Calculate target clusters, ensuring at least 2 for routing
    num_clusters_target = max(2, math.ceil(num_edges / CLUSTER_EDGE_RATIO))
    # Ensure we don't request more clusters than available edges
    num_clusters = min(num_clusters_target, num_edges)
    print(f"\nTargeting {num_clusters} monitoring clusters based on {num_edges} edges (Ratio approx 1/{CLUSTER_EDGE_RATIO}).")

    # 4. Assign Edges to Clusters (Conceptual mapping for now)
    # Randomly select unique edges to be monitored by each cluster
    monitored_edges_indices = random.sample(range(num_edges), num_clusters)
    cluster_edge_map = {}
    print("--- Conceptual Edge->Cluster Mapping ---")
    for i, edge_index in enumerate(monitored_edges_indices):
        cluster_id = i + 1
        u, v, data = all_edges[edge_index]
        cluster_edge_map[cluster_id] = {"edge": (u, v), "traffic": data['current_traffic']}
        # Print only the mapping, not full data for brevity
        print(f"  Cluster {cluster_id} <-> Edge ({u}-{v}) (Simulated Traffic: {data['current_traffic']})")
    # Note: This map is not yet passed into the Kathara config below.

    # --- Kathara Lab Configuration ---
    min_machines_per_cluster = 1 # Number of sensor machines per cluster
    max_machines_per_cluster = 1 # Keep it simple: 1 sensor per edge/cluster
    client_image = "bensrubishcode/traffic_sensor"
    router_image = "bensrubishcode/my_router_image"

    print("\n+++ Starting Kathara Lab Generation +++")
    print(f"Generating {num_clusters} clusters.")
    print(f"Client Image: {client_image}")
    print(f"Router Image: {router_image}")

    # --- Delete existing configuration files ---
    confu_file = "lab.confu"
    startup_file = "startup" # Global startup, likely unused
    if os.path.exists(confu_file):
        try:
            os.remove(confu_file)
            print(f"Deleted existing {confu_file}")
        except OSError as e:
            print(f"Error deleting {confu_file}: {e}")
    # Only delete global startup if it exists
    if os.path.exists(startup_file):
         try:
             os.remove(startup_file)
             print(f"Deleted existing {startup_file}")
         except OSError as e:
             print(f"Error deleting {startup_file}: {e}")


    # --- Generate the configurations using the determined num_clusters ---
    lab_config_str, startup_commands_str = generate_lab_config_with_routers(
        num_clusters, # Use the calculated number of clusters
        min_machines_per_cluster,
        max_machines_per_cluster,
        client_image,
        router_image
    )

    # --- Write lab.confu file ---
    try:
        with open(confu_file, "w") as f:
            lines = lab_config_str.splitlines()
            for i, line in enumerate(lines):
                f.write(line.rstrip())
                if i < len(lines) - 1:
                    f.write("\n")
        print(f"Successfully generated {confu_file}")
    except IOError as e:
        print(f"Error writing {confu_file}: {e}")
        exit(1) # Exit if we can't write the config


    # --- Write global startup file (if needed/generated) ---
    if startup_commands_str:
        try:
            with open(startup_file, "w") as f:
                 lines = startup_commands_str.splitlines()
                 for i, line in enumerate(lines):
                     f.write(line.rstrip())
                     if i < len(lines) - 1:
                         f.write("\n")
            print(f"Successfully generated {startup_file}")
        except IOError as e:
            print(f"Error writing {startup_file}: {e}")
    # else: # No need to print if not generated
    #     print(f"No content for global {startup_file}, file not created.")

    print("+++ Lab Generation Script Finished +++")

    # --- Output the number of routers generated for bash.sh ---
    print(f"ROUTERS_GENERATED={num_clusters}")
