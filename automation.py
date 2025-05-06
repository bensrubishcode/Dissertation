#!/usr/bin/env python3
# No leading spaces or tabs before this line or any other
import networkx as nx
import random
import os
import math
import json
import shutil

# --- Configuration ---
# NOISY_SENSOR_FRACTION = 0.15 # Can add this back later if needed

# --- Graph Generation Function (Unchanged) ---
def generate_graph(num_nodes, density_factor, seed):
    """Generate a graph with edge attributes (speed_limit, capacity)."""
    random.seed(seed)
    density_factor = max(0, min(1, density_factor)); edge_probability = 0.05 + 0.3 * density_factor
    speed_limit_range = (30, 50) if density_factor > 0.5 else (70, 120)
    for _ in range(5):
        G = nx.erdos_renyi_graph(n=num_nodes, p=edge_probability, seed=random.randint(1, 10000))
        if G.number_of_edges() > 0: break
    else: print(f"[Warning] Could not generate a graph with edges."); return None
    if G.number_of_edges() == 0: print("[Error] Graph generation resulted in 0 edges."); return None
    for (u, v) in G.edges():
        G[u][v]['speed_limit'] = random.randint(*speed_limit_range)
        G[u][v]['capacity'] = random.randint(20, 100)
        G[u][v]['distance'] = 1.0 # Assume unit distance
    return G

# --- Kathara Config Generation ---
command_snippet_files = []
snippet_dir = "cmd_snippets"

def generate_lab_config(num_clusters, G, nodes_with_lights, cluster_edge_map, client_image, router_image, traffic_light_image, traffic_server_image, traffic_server_ip):
    """
    Generates Kathara lab.confu config including routers, sensors (with IPs),
    traffic lights, and the traffic server.
    Also generates client/light command snippets (identity ONLY) and the light->sensor map.
    """
    config_lines = []
    global command_snippet_files, snippet_dir
    command_snippet_files = [] # Reset list

    backbone_lan_name = "backbone0"
    backbone_subnet_prefix = "192.168.254"
    router_details = {}
    sensor_ips = {} # cluster_id -> IP address
    light_sensor_map_data = {} # node_id -> { edge_str: sensor_ip }

    # --- Designate noisy sensors (Optional) ---
    # num_sensors = num_clusters
    # num_noisy = math.ceil(num_sensors * NOISY_SENSOR_FRACTION)
    # noisy_sensor_cluster_ids = set(random.sample(range(1, num_sensors + 1), min(num_noisy, num_sensors)))
    # print(f"\n--- Designating {len(noisy_sensor_cluster_ids)} sensors as noisy: {noisy_sensor_cluster_ids} ---")
    noisy_sensor_cluster_ids = set() # Default to none noisy for now

    print(f"\n--- Generating Kathara config for {num_clusters} Clusters/Routers ---")

    # --- Define Routers and Sensor Clients ---
    for cluster_id in range(1, num_clusters + 1):
        cluster_lan_name = f"lan{cluster_id}"; router_name = f"router{cluster_id}"
        router_ip_on_lan = f"10.{cluster_id}.1.254"; router_ip_on_backbone = f"{backbone_subnet_prefix}.{cluster_id}"
        router_details[cluster_id] = {"name": router_name, "ip_lan": router_ip_on_lan, "ip_backbone": router_ip_on_backbone}

        # Define Router
        config_lines.append(f"{router_name}[image]={router_image}    $")
        config_lines.append(f"{router_name}[privileged]=true    $")
        config_lines.append(f"{router_name}[0]={cluster_lan_name}    $ip({router_ip_on_lan}/24);")
        config_lines.append(f"{router_name}[1]={backbone_lan_name}    $ip({router_ip_on_backbone}/24);")

        # Define Client Machine (Sensor)
        machine_name = f"cluster{cluster_id}_machine1"
        ip_address = f"10.{cluster_id}.1.1" # Sensor IP
        sensor_ips[cluster_id] = ip_address
        config_lines.append(f"{machine_name}[image]={client_image}    $")
        startup_cmd = f"ip({ip_address}/24); to(default, {router_ip_on_lan});"
        config_lines.append(f"{machine_name}[0]={cluster_lan_name}    ${startup_cmd}")

        # Generate commands for client snippet file (IDENTITY ONLY)
        edge_info = cluster_edge_map.get(str(cluster_id), {}).get('edge', ('N/A','N/A'))
        edge_str = f"{edge_info[0]}-{edge_info[1]}"
        is_noisy = cluster_id in noisy_sensor_cluster_ids

        client_cmds_filename = os.path.join(snippet_dir, f"{machine_name}.cmds")
        client_command_content = f"\n# Client {machine_name} Startup Commands\n"
        client_command_content += f'echo "Setting client identity files..."\n'
        client_command_content += f"echo {cluster_id} > /etc/cluster_id\n"
        client_command_content += f"echo EDGE={edge_str} > /etc/edge_info\n"
        client_command_content += f"echo NOISY={str(is_noisy).lower()} > /etc/sensor_config\n"
        client_command_content += f"# Python script started by Docker CMD\n"
        try:
            with open(client_cmds_filename, "w") as f_cmd: f_cmd.write(client_command_content)
            command_snippet_files.append(client_cmds_filename)
        except IOError as e: print(f"[ERROR] Writing {client_cmds_filename}: {e}")

    # --- Create Light -> Sensor Mapping ---
    print("\n--- Creating Traffic Light to Sensor Map ---")
    edge_cluster_map = {tuple(v['edge']): k for k, v in cluster_edge_map.items() if 'edge' in v}
    for node_id in nodes_with_lights:
        light_sensor_map_data[str(node_id)] = {}
        # Check if node_id exists in the graph before iterating neighbors
        if node_id not in G:
             print(f"Warning: Node {node_id} selected for light but not found in graph G. Skipping map entry.")
             continue
        for neighbor in G.neighbors(node_id):
            edge_key = tuple(sorted((node_id, neighbor)))
            cluster_id_str = edge_cluster_map.get(edge_key)
            if cluster_id_str:
                sensor_ip = sensor_ips.get(int(cluster_id_str))
                if sensor_ip: light_sensor_map_data[str(node_id)][f"{edge_key[0]}-{edge_key[1]}"] = sensor_ip

    # --- Define Traffic Light Devices ---
    print(f"\n--- Defining {len(nodes_with_lights)} Traffic Light Devices ---")
    light_ip_counter = 100
    for node_id in nodes_with_lights:
        light_name = f"traffic_light_{node_id}"
        light_ip = f"{backbone_subnet_prefix}.{light_ip_counter}"; light_ip_counter += 1
        config_lines.append(f"{light_name}[image]={traffic_light_image}    $")
        startup_cmd_light = f"ip({light_ip}/24); to(default, {backbone_subnet_prefix}.1);"
        config_lines.append(f"{light_name}[0]={backbone_lan_name}    ${startup_cmd_light}")

        # Generate commands for traffic light snippet file (IDENTITY ONLY)
        light_cmds_filename = os.path.join(snippet_dir, f"{light_name}.cmds")
        light_command_content = f"\n# Traffic Light {light_name} Startup Commands\n"
        light_command_content += f'echo "Setting traffic light identity file..."\n'
        light_command_content += f"echo {node_id} > /etc/node_id\n"
        light_command_content += f"# Python script started by Docker CMD\n"
        try:
            with open(light_cmds_filename, "w") as f_cmd: f_cmd.write(light_command_content)
            command_snippet_files.append(light_cmds_filename)
        except IOError as e: print(f"[ERROR] Writing {light_cmds_filename}: {e}")

    # --- Define Traffic Server (Keep CMD, no startup script command needed) ---
    traffic_server_name = "traffic_server"; config_lines.append(f"{traffic_server_name}[image]={traffic_server_image}  $")
    startup_cmd_server = f"ip({traffic_server_ip}/24); to(default, {backbone_subnet_prefix}.1);"; config_lines.append(f"{traffic_server_name}[0]={backbone_lan_name}  ${startup_cmd_server}")

    # --- Add RIP Configuration to Routers ---
    print(f"\n--- Adding RIP configuration to router definitions ---")
    for cluster_id in range(1, num_clusters + 1):
        router_name = router_details[cluster_id]["name"]; lan_subnet = f"10.{cluster_id}.1.0/24"; backbone_subnet = f"{backbone_subnet_prefix}.0/24"
        rip_command = f"rip({router_name}, {lan_subnet}, connected); rip({router_name}, {backbone_subnet}, connected);"
        router_eth1_line_start = f"{router_name}[1]={backbone_lan_name}"; found_line_to_append = False
        for i in range(len(config_lines) - 1, -1, -1):
            line_parts = config_lines[i].split('$', 1); definition_part = line_parts[0].strip()
            if definition_part.startswith(router_eth1_line_start):
                existing_commands = line_parts[1].strip() if len(line_parts) > 1 else "";
                if existing_commands and not existing_commands.endswith(';'): existing_commands += ";"
                separator = " " if existing_commands else ""; config_lines[i] = f"{definition_part}    ${existing_commands}{separator}{rip_command}"
                found_line_to_append = True; break

    print("--- Kathara Configuration Generation Complete ---")
    return "\n".join(config_lines), "", light_sensor_map_data


# ======================================================
# Main Execution Part
# ======================================================
if __name__ == "__main__":
    # --- Parameters ---
    graph_num_nodes = 25; graph_density_factor = 0.5; graph_seed = 42
    CLUSTER_EDGE_RATIO = 4; FRACTION_NODES_WITH_LIGHTS = 0.3
    client_image = "bensrubishcode/traffic_sensor"; router_image = "bensrubishcode/my_router_image"
    traffic_light_image = "bensrubishcode/traffic_light"; traffic_server_image = "bensrubishcode/traffic_server"
    traffic_server_ip = "192.168.254.200"; confu_file = "lab.confu"
    graph_data_filename = "graph_structure.json"; light_sensor_map_filename = "light_sensor_map.json"
    cluster_map_filename = "cluster_edge_map.json"

    # --- Create/Clean snippet directory ---
    snippet_dir = "cmd_snippets"
    if os.path.exists(snippet_dir): print(f"Cleaning up old {snippet_dir} directory..."); shutil.rmtree(snippet_dir)
    print(f"Creating {snippet_dir} directory..."); os.makedirs(snippet_dir)

    # --- Generate Graph ---
    print("+++ Starting Graph Generation +++"); G = generate_graph(graph_num_nodes, graph_density_factor, graph_seed)
    if G is None: exit("[ERROR] Graph generation failed.")
    if not nx.is_connected(G):
        print("Graph not connected. Using largest component.")
        largest_cc_nodes = max(nx.connected_components(G), key=len)
        G = G.subgraph(largest_cc_nodes).copy()
    if G.number_of_edges() == 0 or G.number_of_nodes() <= 1: exit("[ERROR] Graph component too small or empty.")
    print(f"Graph generated with {G.number_of_edges()} edges.")

    # --- Determine Clusters & Map Edges to Clusters ---
    all_edges = list(G.edges(data=True)); num_edges = len(all_edges); num_clusters_target = max(2, math.ceil(num_edges / CLUSTER_EDGE_RATIO)); num_clusters = min(num_clusters_target, num_edges)
    print(f"\nTargeting {num_clusters} monitoring clusters based on {num_edges} edges."); monitored_edges_indices = random.sample(range(num_edges), num_clusters); cluster_edge_map_data = {}
    print("--- Cluster->Edge Mapping ---");
    for i, edge_index in enumerate(monitored_edges_indices): cluster_id = i + 1; u, v, _ = all_edges[edge_index]; edge_tuple = tuple(sorted((u, v))); cluster_edge_map_data[str(cluster_id)] = {"edge": edge_tuple}; print(f"  Cluster {cluster_id} <-> Edge {edge_tuple}")

    # --- Determine Nodes with Traffic Lights ---
    candidate_nodes = [node for node, degree in G.degree() if degree > 2]; num_lights = math.ceil(len(candidate_nodes) * FRACTION_NODES_WITH_LIGHTS); k = min(num_lights, len(candidate_nodes)); nodes_with_lights = set(random.sample(candidate_nodes, k)) if k > 0 else set()
    print(f"\nSelected {len(nodes_with_lights)} nodes for traffic lights: {nodes_with_lights}")

    # --- Save Graph Structure ---
    print(f"\nSaving graph structure to {graph_data_filename}...")
    graph_data = None # Initialize graph_data
    try:
        graph_data = nx.node_link_data(G)
    except Exception as e:
        print(f"[ERROR] Could not convert graph to node_link_data: {e}")

    if graph_data: # Check if conversion succeeded
        try:
            # Correct indentation for this block
            with open(graph_data_filename, 'w') as f:
                json.dump(graph_data, f, indent=4)
            print("Successfully saved graph structure.")
        except Exception as e:
            print(f"[ERROR] Failed to save graph structure: {e}")
    else:
        print("[ERROR] Graph data is invalid or conversion failed, skipping save.")


    # --- Generate Kathara Config ---
    print("\n+++ Starting Kathara Lab Generation +++")
    if os.path.exists(confu_file):
        try: os.remove(confu_file); print(f"Deleted existing {confu_file}")
        except OSError as e: print(f"Error deleting {confu_file}: {e}")
    lab_config_str, _, light_sensor_map_data = generate_lab_config(num_clusters, G, nodes_with_lights, cluster_edge_map_data, client_image, router_image, traffic_light_image, traffic_server_image, traffic_server_ip)

    # --- Save Light->Sensor Map ---
    print(f"\nSaving light->sensor map to {light_sensor_map_filename}...")
    try:
        # Correct indentation for this block
        with open(light_sensor_map_filename, 'w') as f:
            json.dump(light_sensor_map_data, f, indent=4)
        print("Successfully saved light->sensor map.")
    except IOError as e:
        print(f"[ERROR] Failed to save light->sensor map: {e}")

    # --- Save Cluster->Edge Map ---
    print(f"\nSaving cluster->edge map to {cluster_map_filename}...")
    try:
        # Correct indentation for this block
        with open(cluster_map_filename, 'w') as f:
            json.dump(cluster_edge_map_data, f, indent=4)
        print("Successfully saved cluster->edge map.")
    except IOError as e:
        print(f"[ERROR] Failed to save cluster->edge map: {e}")

    # --- Write lab.confu ---
    try:
        # Correct indentation for this block
        with open(confu_file, "w") as f:
            lines = lab_config_str.splitlines();
            for line in lines: f.write(line.rstrip() + "\n")
        print(f"Successfully generated {confu_file}")
    except IOError as e:
        print(f"Error writing {confu_file}: {e}"); exit(1)

    print("+++ Lab Generation Script Finished +++")
    print(f"+++ Generated {len(command_snippet_files)} command snippet files in '{snippet_dir}/'. +++")

    # --- Output router count for bash.sh ---
    print(f"ROUTERS_GENERATED={num_clusters}")

