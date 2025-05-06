#!/usr/bin/env python3
import networkx as nx
import random
import os
import math # For ceiling division
import json # To save traffic data
import shutil # To remove directory tree

# --------------------------------------------------------------------------
# Functions for Graph Generation & Simulation
# --------------------------------------------------------------------------
# (Assuming these functions are correctly defined as in previous versions)
def generate_graph(num_nodes, density_factor, seed):
    """Generate graph."""
    random.seed(seed)
    density_factor = max(0, min(1, density_factor)); edge_probability = 0.05 + 0.3 * density_factor
    speed_limit_range = (30, 50) if density_factor > 0.5 else (70, 120)
    for _ in range(5): # Retry loop for graph generation
        G = nx.erdos_renyi_graph(n=num_nodes, p=edge_probability, seed=random.randint(1, 10000))
        if G.number_of_edges() > 0: break
    else: print(f"[Warning] Could not generate a graph with edges after multiple attempts.")
    if G.number_of_edges() == 0: # Handle case where graph still has no edges
         print("[Error] Graph generation resulted in 0 edges. Cannot proceed.")
         return None # Return None or raise an error
    for (u, v) in G.edges():
        G[u][v]['speed_limit'] = random.randint(*speed_limit_range)
        G[u][v]['capacity'] = random.randint(20, 100); G[u][v]['current_traffic'] = 0
    return G

def calculate_travel_time(speed_limit, traffic, capacity):
    """Calculate travel time considering congestion."""
    if capacity <= 0: return float('inf')
    congestion_factor = min(1.0, traffic / capacity)
    if congestion_factor <= 0.1: effective_speed = speed_limit
    else: effective_speed = max(1, speed_limit / (2 ** (congestion_factor * 3))) # Ensure speed doesn't drop below 1
    if effective_speed <= 0: return float('inf') # Avoid division by zero if effective speed becomes 0
    return 1 / (effective_speed / 60) # Time = Dist(1)/Speed(km/min)

def find_fastest_route(G, source, destination):
    """Find fastest route using Dijkstra."""
    try:
        def weight_function(u, v, data): return calculate_travel_time(data.get('speed_limit', 60), data.get('current_traffic', 0), data.get('capacity', 50))
        return nx.shortest_path(G, source, destination, weight=weight_function)
    except nx.NetworkXNoPath: return None # Handle case where no path exists cleanly
    except Exception as e: print(f"[Error] Pathfinding failed between {source} and {destination}: {e}"); return None

def simulate_traffic(G, num_cars, num_iterations, seed):
    """Simulate traffic flow, populating 'current_traffic' edge attribute."""
    random.seed(seed); nodes = list(G.nodes())
    if not nodes or len(nodes) < 2:
        print("[Warning] Not enough nodes in graph to simulate traffic.")
        return G # Return graph with 0 traffic if no simulation possible
    print(f"Simulating {num_cars} cars for {num_iterations} iterations...")
    # Ensure traffic is reset before simulation
    for u, v in G.edges(): G[u][v]['current_traffic'] = 0
    for iteration in range(num_iterations):
        cars_this_iteration = 0
        for _ in range(num_cars):
            source = random.choice(nodes); destination = random.choice(nodes)
            while destination == source: destination = random.choice(nodes) # Ensure different source/dest

            path = find_fastest_route(G, source, destination)
            if path and len(path) > 1:
                cars_this_iteration += 1
                for i in range(len(path) - 1):
                    u, v = path[i], path[i + 1]
                    if G.has_edge(u, v): G[u][v]['current_traffic'] += 1
                    # If graph was directed, might need G[v][u] logic too
        # Print progress periodically
        if (iteration + 1) % 5 == 0 or iteration == 0 or (iteration + 1) == num_iterations:
             print(f"  Iteration {iteration + 1}: {cars_this_iteration} cars routed.")
    return G
# --------------------------------------------------------------------------

# List to keep track of generated command snippet files (clients ONLY now)
command_snippet_files = []
snippet_dir = "cmd_snippets" # Directory for command snippets


def generate_lab_config_with_routers(num_clusters, min_machines, max_machines, client_image, router_image, traffic_server_image, traffic_server_ip, cluster_edge_map):
    """
    Generates Kathara lab.confu config and client command snippets.
    Does NOT generate command snippets for the traffic server anymore.
    """
    config_lines = []
    startup_lines = [] # Still unused, but kept for structure
    global command_snippet_files
    global snippet_dir
    command_snippet_files = [] # Reset list for this generation run

    backbone_lan_name = "backbone0"
    backbone_subnet_prefix = "192.168.254"
    router_details = {}

    print(f"--- Generating Kathara config for {num_clusters} Clusters/Routers ---")

    # Define Routers and Clients
    for cluster_id in range(1, num_clusters + 1):
        num_machines_in_cluster = random.randint(min_machines, max_machines)
        cluster_lan_name = f"lan{cluster_id}"
        router_name = f"router{cluster_id}"
        router_ip_on_lan = f"10.{cluster_id}.1.254"
        router_ip_on_backbone = f"{backbone_subnet_prefix}.{cluster_id}"
        router_details[cluster_id] = {"name": router_name, "ip_lan": router_ip_on_lan, "ip_backbone": router_ip_on_backbone}

        # Define Router
        config_lines.append(f"{router_name}[image]={router_image}    $")
        config_lines.append(f"{router_name}[privileged]=true    $") # Assumed needed
        config_lines.append(f"{router_name}[0]={cluster_lan_name}    $ip({router_ip_on_lan}/24);")
        config_lines.append(f"{router_name}[1]={backbone_lan_name}    $ip({router_ip_on_backbone}/24);")

        # Define Client Machines (Sensors)
        edge_info = cluster_edge_map.get(cluster_id, {}).get('edge', 'unknown')
        edge_str = f"{edge_info[0]}-{edge_info[1]}" if isinstance(edge_info, tuple) else "unknown"
        for machine_id in range(1, num_machines_in_cluster + 1):
            machine_name = f"cluster{cluster_id}_machine{machine_id}"
            ip_address = f"10.{cluster_id}.1.{machine_id}"
            config_lines.append(f"{machine_name}[image]={client_image}    $")
            # Only Tacata commands (ip, to) in the lab.confu line
            startup_cmd = f"ip({ip_address}/24); to(default, {router_ip_on_lan});"
            config_lines.append(f"{machine_name}[0]={cluster_lan_name}    ${startup_cmd}")

            # Generate commands for client snippet file
            client_cmds_filename = os.path.join(snippet_dir, f"{machine_name}.cmds")
            client_command_content = f"\n# --- Commands Added by bash.sh for client {machine_name} ---\n"
            client_command_content += f'echo "Setting client identity (Cluster ID: {cluster_id}, Edge: {edge_str})..."\n'
            client_command_content += f"echo {cluster_id} > /etc/cluster_id\n"
            client_command_content += f"echo EDGE={edge_str} >> /etc/cluster_id\n"
            client_command_content += f"# --- End Client Commands ---\n"
            try:
                with open(client_cmds_filename, "w") as f_cmd:
                    f_cmd.write(client_command_content)
                command_snippet_files.append(client_cmds_filename) # Track generated file
            except IOError as e:
                print(f"[ERROR] Could not write client command snippet {client_cmds_filename}: {e}")

    # Define Traffic Server - NO python command in startup here
    traffic_server_name = "traffic_server"
    config_lines.append(f"{traffic_server_name}[image]={traffic_server_image}  $")
    # Basic network setup commands only - relies on Dockerfile CMD to start the python script
    startup_cmd_server = f"ip({traffic_server_ip}/24); to(default, {backbone_subnet_prefix}.1);"
    config_lines.append(f"{traffic_server_name}[0]={backbone_lan_name}  ${startup_cmd_server}")

    # --- REMOVED BLOCK THAT GENERATED traffic_server.cmds ---

    # Add RIP Configuration to Routers
    print(f"\n--- Adding RIP configuration to router definitions ---") # Added newline for clarity
    for cluster_id in range(1, num_clusters + 1):
        router_name = router_details[cluster_id]["name"]
        lan_subnet = f"10.{cluster_id}.1.0/24"
        backbone_subnet = f"{backbone_subnet_prefix}.0/24"
        rip_command = f"rip({router_name}, {lan_subnet}, connected); rip({router_name}, {backbone_subnet}, connected);"
        # print(f"  Configuring RIP for {router_name}...") # Less verbose
        router_eth1_line_start = f"{router_name}[1]={backbone_lan_name}"
        found_line_to_append = False
        for i in range(len(config_lines) - 1, -1, -1):
            line_parts = config_lines[i].split('$', 1)
            definition_part = line_parts[0].strip()
            if definition_part.startswith(router_eth1_line_start):
                existing_commands = line_parts[1].strip() if len(line_parts) > 1 else ""
                if existing_commands and not existing_commands.endswith(';'): existing_commands += ";"
                separator = " " if existing_commands else ""
                config_lines[i] = f"{definition_part}    ${existing_commands}{separator}{rip_command}"
                found_line_to_append = True
                break
        if not found_line_to_append: print(f"  Warning: Could not find config line for '{router_eth1_line_start}' to append RIP config.")


    print("--- Kathara Configuration Generation Complete ---")
    return "\n".join(config_lines), "\n".join(startup_lines)


# ======================================================
# Main Execution Part
# ======================================================

if __name__ == "__main__":
    # --- Parameters ---
    graph_num_nodes = 25; graph_density_factor = 0.5; graph_seed = 42
    sim_num_cars = 150; sim_num_iterations = 25; CLUSTER_EDGE_RATIO = 4
    min_machines_per_cluster = 1; max_machines_per_cluster = 1
    client_image = "bensrubishcode/traffic_sensor"; router_image = "bensrubishcode/my_router_image"
    traffic_server_image = "bensrubishcode/traffic_server"; traffic_server_ip = "192.168.254.200"
    traffic_data_filename = "traffic_data.json"; confu_file = "lab.confu"

    # --- Create/Clean snippet directory ---
    snippet_dir = "cmd_snippets"
    if os.path.exists(snippet_dir):
        print(f"Cleaning up old {snippet_dir} directory...")
        try: shutil.rmtree(snippet_dir)
        except OSError as e: print(f"Warning: Could not remove directory {snippet_dir}: {e}")
    print(f"Creating {snippet_dir} directory...")
    try: os.makedirs(snippet_dir)
    except OSError as e: exit(f"[ERROR] Could not create directory {snippet_dir}: {e}")

    # --- Graph Simulation, Cluster Calc, Traffic Save ---
    print("+++ Starting Graph Simulation +++")
    G = generate_graph(graph_num_nodes, graph_density_factor, graph_seed)
    if G is None: exit("[ERROR] Graph generation failed.") # Check if generate_graph returned None
    if not nx.is_connected(G):
        print("Graph not connected. Using largest component.")
        largest_cc_nodes = max(nx.connected_components(G), key=len)
        G = G.subgraph(largest_cc_nodes).copy()
        print(f"Using component with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")
    if G.number_of_edges() == 0 or G.number_of_nodes() <= 1: exit("[ERROR] Graph component too small or empty.")
    G_simulated = simulate_traffic(G.copy(), sim_num_cars, sim_num_iterations, graph_seed)
    print("Traffic simulation complete.")
    all_edges = list(G_simulated.edges(data=True)); num_edges = len(all_edges)
    num_clusters_target = max(2, math.ceil(num_edges / CLUSTER_EDGE_RATIO)); num_clusters = min(num_clusters_target, num_edges)
    print(f"\nTargeting {num_clusters} monitoring clusters based on {num_edges} edges.")
    monitored_edges_indices = random.sample(range(num_edges), num_clusters); cluster_edge_map = {}; traffic_data_to_save = {}
    print("--- Conceptual Edge->Cluster Mapping ---")
    for i, edge_index in enumerate(monitored_edges_indices):
        cluster_id = i + 1; u, v, data = all_edges[edge_index]
        edge_tuple = tuple(sorted((u, v))); traffic = data.get('current_traffic', 0)
        cluster_edge_map[cluster_id] = {"edge": edge_tuple, "traffic": traffic}
        traffic_data_to_save[str(cluster_id)] = {"edge_u": edge_tuple[0], "edge_v": edge_tuple[1], "traffic": traffic}
        print(f"  Cluster {cluster_id} <-> Edge {edge_tuple} (SimTraffic: {traffic})")
    print(f"\nSaving traffic data to {traffic_data_filename}...")
    try:
        with open(traffic_data_filename, 'w') as f: json.dump(traffic_data_to_save, f, indent=4)
        print("Successfully saved traffic data.")
    except IOError as e: print(f"[ERROR] Failed to save traffic data: {e}")

    # --- Generate Kathara Config (also generates *.cmds files in snippet_dir) ---
    print("\n+++ Starting Kathara Lab Generation +++")
    if os.path.exists(confu_file):
        try: os.remove(confu_file); print(f"Deleted existing {confu_file}")
        except OSError as e: print(f"Error deleting {confu_file}: {e}")
    lab_config_str, _ = generate_lab_config_with_routers(
        num_clusters, min_machines_per_cluster, max_machines_per_cluster,
        client_image, router_image, traffic_server_image, traffic_server_ip, cluster_edge_map
    )

    # --- Write lab.confu ---
    try:
        with open(confu_file, "w") as f:
            # Write line by line ensuring newline
            lines = lab_config_str.splitlines()
            for line in lines:
                 f.write(line.rstrip() + "\n") # Add newline after each line
        print(f"Successfully generated {confu_file}")
    except IOError as e: print(f"Error writing {confu_file}: {e}"); exit(1)

    print("+++ Lab Generation Script Finished +++")
    print(f"+++ Generated {len(command_snippet_files)} command snippet files in '{snippet_dir}/'. +++") # command_snippet_files is now only clients

    # --- Output router count for bash.sh ---
    print(f"ROUTERS_GENERATED={num_clusters}")
