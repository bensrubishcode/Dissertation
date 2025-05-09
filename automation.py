#!/usr/bin/env python3
import networkx as nx
import random
import os
import math
import json
import shutil
import csv
from datetime import datetime, timedelta

# --- ML Assessor Import (remains the same) ---
ML_ASSESSOR_AVAILABLE = False
ML_MODELS_PRESENT = False
try:
    import ml_risk_assessor
    if (os.path.exists(ml_risk_assessor.RELIABILITY_MODEL_PATH) and
        os.path.exists(ml_risk_assessor.NOISY_CONFIG_MODEL_PATH) and
        os.path.exists(ml_risk_assessor.PREPROCESSOR_PATH)):
        ML_ASSESSOR_AVAILABLE = True
        ML_MODELS_PRESENT = True
        print("INFO: ML Risk Assessor module and models loaded by automation.py for predictions.")
    elif ml_risk_assessor:
        ML_ASSESSOR_AVAILABLE = True
        print("WARNING: ML Risk Assessor module loaded, but model/preprocessor files are missing. Predictions will use fallbacks.")
    else:
        print("WARNING: ml_risk_assessor module was found but is in an unexpected state.")
except ImportError:
    print("WARNING: ml_risk_assessor.py not found by automation.py. Initial sensor attributes will use fallbacks.")
except Exception as e:
    print(f"WARNING: Error importing or checking ml_risk_assessor in automation.py: {e}. Predictions will use fallbacks.")

# --- Configuration (MANUFACTURER_PROFILES, SOFTWARE_PROFILES, etc. remain the same) ---
MANUFACTURER_PROFILES = {
    "GoodSensorCorp": {"base_reliability": 95, "base_noise_probability": 0.05, "signature_bonus": 5, "age_degradation_factor": 0.5, "sw_age_penalty_factor": 0.3},
    "OkayDevices":    {"base_reliability": 80, "base_noise_probability": 0.15, "signature_bonus": 3, "age_degradation_factor": 1.0, "sw_age_penalty_factor": 0.6},
    "ShadySensorsLtd":{"base_reliability": 65, "base_noise_probability": 0.30, "signature_bonus": 0, "age_degradation_factor": 1.5, "sw_age_penalty_factor": 1.0},
    "LegacySystems":  {"base_reliability": 55, "base_noise_probability": 0.25, "signature_bonus": 1, "age_degradation_factor": 2.0, "sw_age_penalty_factor": 1.2}
}
SOFTWARE_PROFILES = {
    "v1.0.0":              {"reliability_modifier": -10, "noise_modifier_factor": 1.5, "is_signed": False, "release_date_offset_years": 4.0},
    "v1.0.1-signed":       {"reliability_modifier": 0,   "noise_modifier_factor": 1.0, "is_signed": True,  "release_date_offset_years": 3.5},
    "v1.2.0-beta-unsigned":{"reliability_modifier": -20, "noise_modifier_factor": 2.0, "is_signed": False, "release_date_offset_years": 2.5},
    "v2.0.0-signed":       {"reliability_modifier": 10,  "noise_modifier_factor": 0.7, "is_signed": True,  "release_date_offset_years": 1.5},
    "v2.1.0-signed":       {"reliability_modifier": 5,   "noise_modifier_factor": 0.85,"is_signed": True,  "release_date_offset_years": 0.5},
    "v0.8.0-legacy":       {"reliability_modifier": -25, "noise_modifier_factor": 1.8, "is_signed": False, "release_date_offset_years": 6.0}
}
MAX_SOFTWARE_AGE_FOR_PENALTY = 5.0
MAX_DEVICE_AGE_YEARS = 5.0
MIN_DEVICE_AGE_YEARS = 0.1
MIN_EDGE_DISTANCE = 0.5
MAX_EDGE_DISTANCE = 5.0
command_snippet_files = []
snippet_dir = "cmd_snippets"
ML_TRAINING_DATA_FILE = "ml_training_data.csv"

FALLBACK_DEVICE_RELIABILITY_RANGE_AUTO = (60.0, 90.0)
FALLBACK_PREDICTED_NOISE_PROB_RANGE_AUTO = (0.05, 0.30)
FALLBACK_DATA_CONSISTENCY_BASELINE_AUTO = (0.6, 0.95)

def generate_graph(num_nodes, density_factor, seed):
    # ... (no changes to this function from the artifact) ...
    random.seed(seed)
    density_factor = max(0, min(1, density_factor)); edge_probability = 0.05 + 0.3 * density_factor
    speed_limit_range = (30, 50) if density_factor > 0.5 else (70, 120)
    for _ in range(5):
        G = nx.erdos_renyi_graph(n=num_nodes, p=edge_probability, seed=random.randint(1, 10000))
        if G.number_of_edges() > 0: break
    else:
        print(f"[Warning] Could not generate a graph with edges after multiple attempts.")
        return None
    if G.number_of_edges() == 0:
        print("[Error] Graph generation resulted in 0 edges.")
        return None
    for (u, v) in G.edges():
        G[u][v]['speed_limit'] = random.randint(*speed_limit_range)
        G[u][v]['capacity'] = random.randint(20, 100)
        G[u][v]['distance'] = round(random.uniform(MIN_EDGE_DISTANCE, MAX_EDGE_DISTANCE), 2)
    return G

def generate_lab_config(num_clusters, G, nodes_with_lights, cluster_edge_map_input, client_image, router_image, traffic_light_image, traffic_server_image, traffic_server_ip, ml_training_log_writer):
    # ... (This function remains largely the same as in the artifact,
    #      as it consumes the pre-calculated num_clusters, nodes_with_lights,
    #      and cluster_edge_map_input. The core logic change is in how these
    #      are determined in the __main__ block.)
    config_lines = []
    global command_snippet_files # Uses global from main script context
    command_snippet_files = [] # Reset list

    backbone_lan_name = "backbone0"
    backbone_subnet_prefix = "192.168.254"
    router_details = {}
    # sensor_ips = {} # Not strictly needed if IP is in the static profile
    light_sensor_map_data = {} # Will contain static profiles + ML predictions
    sensors_static_profiles_for_map_build = {} # cluster_id -> full profile for map construction

    print(f"\n--- Generating Kathara config for {num_clusters} Clusters/Routers ---")
    if num_clusters == 0:
        print("WARNING: num_clusters is 0. No sensors or routers for sensors will be created.")


    for cluster_id in range(1, num_clusters + 1): # This loop runs if num_clusters > 0
        # Router Definition
        cluster_lan_name = f"lan{cluster_id}"; router_name = f"router{cluster_id}"
        router_ip_on_lan = f"10.{cluster_id}.1.254"; router_ip_on_backbone = f"{backbone_subnet_prefix}.{cluster_id}"
        router_details[cluster_id] = {"name": router_name, "ip_lan": router_ip_on_lan, "ip_backbone": router_ip_on_backbone}
        config_lines.extend([
            f"{router_name}[image]={router_image}    $",
            f"{router_name}[privileged]=true    $",
            f"{router_name}[0]={cluster_lan_name}    $ip({router_ip_on_lan}/24);",
            f"{router_name}[1]={backbone_lan_name}    $ip({router_ip_on_backbone}/24);"
        ])

        # Sensor (Client Machine) Definition & Static Profile Assignment
        machine_name = f"cluster{cluster_id}_machine1" # This is our sensor
        sensor_ip_address = f"10.{cluster_id}.1.1"
        # sensor_ips[cluster_id] = sensor_ip_address
        config_lines.extend([
            f"{machine_name}[image]={client_image}    $",
            f"{machine_name}[0]={cluster_lan_name}    $ip({sensor_ip_address}/24); to(default, {router_ip_on_lan});"
        ])

        # Assign static profile
        sensor_manufacturer = random.choice(list(MANUFACTURER_PROFILES.keys()))
        sensor_software_version_str = random.choice(list(SOFTWARE_PROFILES.keys()))
        sw_profile = SOFTWARE_PROFILES[sensor_software_version_str]
        software_is_signed = sw_profile["is_signed"]
        software_age_years = sw_profile["release_date_offset_years"]
        device_age_years = round(random.uniform(MIN_DEVICE_AGE_YEARS, MAX_DEVICE_AGE_YEARS), 2)

        current_sensor_static_features_for_ml = {
            "manufacturer": sensor_manufacturer,
            "software_version": sensor_software_version_str,
            "is_signed": 1 if software_is_signed else 0,
            "software_age_years": software_age_years,
            "device_age_years": device_age_years
        }

        ml_predicted_attributes = None
        if ML_ASSESSOR_AVAILABLE and ML_MODELS_PRESENT:
            try:
                ml_predicted_attributes = ml_risk_assessor.predict_initial_attributes(current_sensor_static_features_for_ml)
            except Exception as e:
                print(f"  WARNING: ML prediction failed for sensor {cluster_id}: {e}. Using fallbacks.")
                ml_predicted_attributes = None
        
        full_sensor_profile_for_map = {
            "ip": sensor_ip_address,
            "manufacturer": sensor_manufacturer,
            "software_version": sensor_software_version_str,
            "is_signed": software_is_signed,
            "software_age_years": software_age_years,
            "device_age_years": device_age_years,
            "ml_predicted_reliability": round(ml_predicted_attributes.get("predicted_inherent_reliability", random.uniform(*FALLBACK_DEVICE_RELIABILITY_RANGE_AUTO)),1) if ml_predicted_attributes else round(random.uniform(*FALLBACK_DEVICE_RELIABILITY_RANGE_AUTO),1),
            "ml_predicted_noise_propensity": round(ml_predicted_attributes.get("predicted_is_noisy_probability", random.uniform(*FALLBACK_PREDICTED_NOISE_PROB_RANGE_AUTO)),2) if ml_predicted_attributes else round(random.uniform(*FALLBACK_PREDICTED_NOISE_PROB_RANGE_AUTO),2),
            "ml_initial_data_consistency": round(random.uniform(*FALLBACK_DATA_CONSISTENCY_BASELINE_AUTO), 2)
        }
        sensors_static_profiles_for_map_build[cluster_id] = full_sensor_profile_for_map

        mf_profile = MANUFACTURER_PROFILES[sensor_manufacturer]
        gt_reliability = mf_profile["base_reliability"] + sw_profile["reliability_modifier"]
        if software_is_signed: gt_reliability += mf_profile["signature_bonus"]
        device_age_penalty = device_age_years * mf_profile["age_degradation_factor"]
        gt_reliability -= device_age_penalty
        if software_age_years > 1.0:
            effective_sw_age_for_penalty = min(software_age_years, MAX_SOFTWARE_AGE_FOR_PENALTY)
            software_age_penalty = effective_sw_age_for_penalty * mf_profile["sw_age_penalty_factor"]
            gt_reliability -= software_age_penalty
        gt_reliability = round(max(0, min(100, gt_reliability)),1)

        gt_noise_probability = mf_profile["base_noise_probability"] * sw_profile["noise_modifier_factor"]
        gt_noise_probability += (device_age_years / MAX_DEVICE_AGE_YEARS) * 0.10
        if software_age_years > 1.0:
            effective_sw_age_for_noise_penalty = min(software_age_years, MAX_SOFTWARE_AGE_FOR_PENALTY)
            gt_noise_probability += (effective_sw_age_for_noise_penalty / MAX_SOFTWARE_AGE_FOR_PENALTY) * 0.05
        gt_noise_probability = max(0.01, min(0.99, gt_noise_probability))
        sensor_will_be_configured_noisy = random.random() < gt_noise_probability

        ml_training_log_writer.writerow([
            f"sensor_{cluster_id}", sensor_manufacturer, sensor_software_version_str,
            int(software_is_signed), software_age_years, device_age_years,
            gt_reliability, int(sensor_will_be_configured_noisy)
        ])

        edge_info_from_map = cluster_edge_map_input.get(str(cluster_id), {}).get('edge', ('N/A','N/A'))
        edge_str = f"{edge_info_from_map[0]}-{edge_info_from_map[1]}"
        client_cmds_filename = os.path.join(snippet_dir, f"{machine_name}.cmds")
        client_command_content = (
            f"\n# Client {machine_name} (Sensor) Startup Commands\n"
            f'echo "Setting client identity and behavior files..."\n'
            f"echo {cluster_id} > /etc/cluster_id\n"
            f"echo EDGE={edge_str} > /etc/edge_info\n"
            f"echo MANUFACTURER={sensor_manufacturer} > /etc/sensor_profile\n"
            f"echo SOFTWARE_VERSION={sensor_software_version_str} >> /etc/sensor_profile\n"
            f"echo IS_SIGNED={str(software_is_signed).lower()} >> /etc/sensor_profile\n"
            f"echo SOFTWARE_AGE_YEARS={software_age_years} >> /etc/sensor_profile\n"
            f"echo DEVICE_AGE_YEARS={device_age_years} >> /etc/sensor_profile\n"
            f"echo MAKE_NOISY={str(sensor_will_be_configured_noisy).lower()} > /etc/sensor_config\n"
        )
        try:
            with open(client_cmds_filename, "w") as f_cmd: f_cmd.write(client_command_content)
            command_snippet_files.append(client_cmds_filename)
        except IOError as e: print(f"[ERROR] Writing {client_cmds_filename}: {e}")

    print("\n--- Creating Traffic Light to Sensor Map (with Static Features & ML Preds) ---")
    # edge_to_cluster_id_map is based on cluster_edge_map_input which now contains ALL monitored edges
    edge_to_cluster_id_map = {tuple(sorted(v['edge'])): k
                           for k, v in cluster_edge_map_input.items()
                           if 'edge' in v and isinstance(v['edge'], list) and len(v['edge']) == 2}

    for light_node_id_int in nodes_with_lights: # nodes_with_lights is already determined
        light_node_id_str = str(light_node_id_int)
        light_sensor_map_data[light_node_id_str] = {} # Initialize entry for this light
        if light_node_id_int not in G:
             print(f"Warning: Node {light_node_id_int} for light not in graph G. Skipping map entry.")
             continue
        
        # For this light, find all its connected edges that are monitored
        for neighbor_node_int in G.neighbors(light_node_id_int):
            edge_key_tuple = tuple(sorted((light_node_id_int, neighbor_node_int)))
            edge_key_str = f"{edge_key_tuple[0]}-{edge_key_tuple[1]}" # For the JSON map key

            # Check if this edge has a sensor (i.e., a cluster_id)
            mapped_cluster_id_str = edge_to_cluster_id_map.get(edge_key_tuple)
            if mapped_cluster_id_str:
                mapped_cluster_id_int = int(mapped_cluster_id_str)
                # Get the full profile (static + ML preds) for the sensor on this edge
                full_sensor_profile_for_current_map_entry = sensors_static_profiles_for_map_build.get(mapped_cluster_id_int)
                if full_sensor_profile_for_current_map_entry:
                    light_sensor_map_data[light_node_id_str][edge_key_str] = full_sensor_profile_for_current_map_entry
                else: # Should not happen if sensors_static_profiles_for_map_build was populated correctly for all clusters
                    print(f"CRITICAL WARNING: Full sensor profile for cluster {mapped_cluster_id_int} (edge {edge_key_str}) not found during map creation for light {light_node_id_str}.")
            # else: # This edge connected to the light is NOT monitored by a sensor
            #     print(f"DEBUG: Edge {edge_key_str} for light {light_node_id_str} is not a monitored edge.")
    
    print(f"\n--- Defining {len(nodes_with_lights)} Traffic Light Devices ---")
    light_ip_counter = 100
    default_light_gateway = f"{backbone_subnet_prefix}.1"
    for node_id_int_tl in nodes_with_lights:
        node_id_str_tl = str(node_id_int_tl)
        # Only create traffic light device if it has at least one sensor mapped to it
        if node_id_str_tl in light_sensor_map_data and light_sensor_map_data[node_id_str_tl]:
            light_name = f"traffic_light_{node_id_str_tl}"
            light_ip = f"{backbone_subnet_prefix}.{light_ip_counter}"; light_ip_counter += 1
            config_lines.extend([
                f"{light_name}[image]={traffic_light_image}    $",
                f"{light_name}[0]={backbone_lan_name}    $ip({light_ip}/24); to(default, {default_light_gateway});"
            ])
            light_cmds_filename = os.path.join(snippet_dir, f"{light_name}.cmds")
            light_command_content_tl = (
                f"\n# Traffic Light {light_name} Startup Commands\n"
                f'echo "Setting traffic light identity file..."\n'
                f"echo {node_id_int_tl} > /etc/node_id\n"
            )
            try:
                with open(light_cmds_filename, "w") as f_cmd: f_cmd.write(light_command_content_tl)
                command_snippet_files.append(light_cmds_filename)
            except IOError as e: print(f"[ERROR] Writing {light_cmds_filename}: {e}")
        else:
            print(f"INFO: Skipping creation of traffic_light device for node {node_id_str_tl} as it has no mapped sensors.")


    traffic_server_name = "traffic_server"
    default_server_gateway = f"{backbone_subnet_prefix}.1"
    config_lines.extend([
        f"{traffic_server_name}[image]={traffic_server_image}  $",
        f"{traffic_server_name}[0]={backbone_lan_name}  $ip({traffic_server_ip}/24); to(default, {default_server_gateway});"
    ])
    
    print(f"\n--- Adding RIP configuration to router definitions ---")
    if num_clusters > 0: # Only add RIP config if there are routers for sensors
        for cluster_id_rip_cfg in range(1, num_clusters + 1):
            if cluster_id_rip_cfg not in router_details:
                print(f"Warning: Router details for cluster_id {cluster_id_rip_cfg} not found for RIP config.")
                continue
            router_name_rip_cfg = router_details[cluster_id_rip_cfg]["name"]
            lan_subnet_rip_cfg = f"10.{cluster_id_rip_cfg}.1.0/24"
            backbone_subnet_rip_cfg = f"{backbone_subnet_prefix}.0/24"
            rip_command = f"rip({router_name_rip_cfg}, {lan_subnet_rip_cfg}, connected); rip({router_name_rip_cfg}, {backbone_subnet_rip_cfg}, connected);"
            router_eth1_line_start = f"{router_name_rip_cfg}[1]={backbone_lan_name}"
            found_line_to_append = False
            for i_cfg_line in range(len(config_lines) - 1, -1, -1): # Renamed loop var
                line_parts = config_lines[i_cfg_line].split('$', 1); definition_part = line_parts[0].strip()
                if definition_part.startswith(router_eth1_line_start):
                    existing_commands = line_parts[1].strip() if len(line_parts) > 1 else ""
                    if existing_commands and not existing_commands.endswith(';'): existing_commands += ";"
                    separator = " " if existing_commands else ""
                    config_lines[i_cfg_line] = f"{definition_part}    ${existing_commands}{separator}{rip_command}"
                    found_line_to_append = True; break
            if not found_line_to_append:
                 print(f"Warning: Could not find backbone interface line for router {router_name_rip_cfg} to append RIP config.")
    else:
        print("INFO: No sensor clusters, so no RIP configuration needed for sensor routers.")

    print("--- Kathara Configuration Generation Complete ---")
    return "\n".join(config_lines), "", light_sensor_map_data

# ======================================================
# Main Execution Part
# ======================================================
if __name__ == "__main__":
    graph_num_nodes = 20 # Increased node count
    graph_density_factor = 0.3 # Slightly less dense
    graph_seed = random.randint(1, 10000) 
    # CLUSTER_EDGE_RATIO is no longer the primary driver for num_clusters if all light edges are monitored
    FRACTION_NODES_WITH_LIGHTS = 0.3 # Fraction of suitable intersections to have traffic lights

    client_image = "bensrubishcode/traffic_sensor"
    router_image = "bensrubishcode/my_router_image"
    traffic_light_image = "bensrubishcode/traffic_light"
    traffic_server_image = "bensrubishcode/traffic_server"
    traffic_server_ip = "192.168.254.200"
    confu_file = "lab.confu"
    graph_data_filename = "graph_structure.json"
    light_sensor_map_filename = "light_sensor_map.json"
    cluster_map_filename = "cluster_edge_map.json" # Will map cluster_id to the monitored edge

    if os.path.exists(snippet_dir):
        shutil.rmtree(snippet_dir)
    os.makedirs(snippet_dir)
    print(f"Created/Cleaned {snippet_dir} directory.")

    file_exists = os.path.exists(ML_TRAINING_DATA_FILE)
    try:
        with open(ML_TRAINING_DATA_FILE, 'a', newline='') as ml_log_file:
            ml_log_writer = csv.writer(ml_log_file)
            if not file_exists or os.path.getsize(ML_TRAINING_DATA_FILE) == 0:
                ml_log_writer.writerow([
                    "sensor_id", "manufacturer", "software_version", "is_signed",
                    "software_age_years", "device_age_years",
                    "gt_inherent_reliability", "gt_is_configured_noisy"
                ])
            print(f"ML training data will be logged to: {ML_TRAINING_DATA_FILE}")

            print("+++ Starting Graph Generation +++")
            G = generate_graph(graph_num_nodes, graph_density_factor, graph_seed)
            if G is None: exit("[ERROR] Graph generation failed.")
            if not nx.is_connected(G):
                print("Warning: Graph is not connected. Using the largest connected component.")
                largest_cc_nodes = max(nx.connected_components(G), key=len)
                G_comp = G.subgraph(largest_cc_nodes).copy()
                if G_comp.number_of_edges() == 0 or G_comp.number_of_nodes() <= 1:
                    exit("[ERROR] Largest connected component is too small or empty.")
                G = G_comp
            print(f"Graph finalized with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")

            if G.number_of_edges() == 0: exit("[ERROR] No edges in the final graph to proceed.")

            # --- Determine Nodes with Traffic Lights FIRST ---
            print("\n--- Determining Candidate Nodes for Traffic Lights ---")
            # Candidates are intersections (degree > 1 or > 2 as preferred)
            candidate_intersections = [node for node in G.nodes() if G.degree(node) > 1] # Min degree 2 for an intersection
            
            nodes_with_lights = set()
            if not candidate_intersections:
                print("Warning: No suitable intersections (degree > 1) found to place traffic lights.")
            else:
                num_intersections_for_lights = math.ceil(len(candidate_intersections) * FRACTION_NODES_WITH_LIGHTS)
                k_lights = min(num_intersections_for_lights, len(candidate_intersections))
                if k_lights > 0:
                    nodes_with_lights = set(random.sample(candidate_intersections, k=k_lights))
            print(f"Selected {len(nodes_with_lights)} nodes for traffic lights: {nodes_with_lights if nodes_with_lights else 'None'}")

            # --- Determine Monitored Edges: ALL edges connected to selected traffic lights ---
            monitored_edge_tuples_set = set()
            if nodes_with_lights:
                print("\n--- Identifying edges to be monitored (connected to traffic lights) ---")
                for light_node in nodes_with_lights:
                    for neighbor in G.neighbors(light_node):
                        edge = tuple(sorted((light_node, neighbor)))
                        monitored_edge_tuples_set.add(edge)
            else:
                print("INFO: No traffic lights selected, so no edges will be monitored based on lights.")
            
            # Optionally, add more monitored edges if desired (e.g., to meet a certain coverage)
            # For now, only edges connected to lights are monitored.
            
            print(f"Total of {len(monitored_edge_tuples_set)} unique edges will be monitored by sensors.")

            # --- Create cluster_edge_map_data_for_sim based on these monitored edges ---
            num_clusters = len(monitored_edge_tuples_set) # Number of sensors/clusters needed
            cluster_edge_map_data_for_sim = {}
            cluster_id_counter = 1
            if monitored_edge_tuples_set:
                print("--- Assigning Clusters to Monitored Edges ---")
                for edge_tuple in monitored_edge_tuples_set:
                    cluster_edge_map_data_for_sim[str(cluster_id_counter)] = {"edge": list(edge_tuple)}
                    # print(f"  Cluster {cluster_id_counter} <-> Edge {edge_tuple}") # Can be verbose
                    cluster_id_counter += 1
            else:
                print("INFO: No edges were designated for monitoring. No sensor clusters will be created.")
                # num_clusters will be 0, generate_lab_config will handle this.

            # --- Save Graph Structure ---
            print(f"\nSaving graph structure to {graph_data_filename}...")
            try:
                graph_data_to_save = nx.node_link_data(G)
                with open(graph_data_filename, 'w') as f: json.dump(graph_data_to_save, f, indent=4)
                print("Successfully saved graph structure.")
            except Exception as e: print(f"[ERROR] Failed to save graph structure: {e}")

            # --- Generate Kathara Config ---
            print("\n+++ Starting Kathara Lab Generation +++")
            if os.path.exists(confu_file):
                try: os.remove(confu_file); print(f"Deleted existing {confu_file}")
                except OSError as e: print(f"Error deleting {confu_file}: {e}")
            if G is None: exit("[ERROR] Graph object G is None.")

            lab_config_str, _, generated_light_sensor_map = generate_lab_config(
                num_clusters, G, nodes_with_lights, cluster_edge_map_data_for_sim, 
                client_image, router_image, traffic_light_image,
                traffic_server_image, traffic_server_ip,
                ml_log_writer
            )

        print(f"ML training data generation for this iteration complete. Appended to {ML_TRAINING_DATA_FILE}")

        # --- Save Output Files ---
        print(f"\nSaving light->sensor map to {light_sensor_map_filename}...")
        try:
            with open(light_sensor_map_filename, 'w') as f: json.dump(generated_light_sensor_map, f, indent=4)
            print("Successfully saved light->sensor map.")
        except IOError as e: print(f"[ERROR] Failed to save light->sensor map: {e}")

        print(f"\nSaving cluster->edge map to {cluster_map_filename}...")
        try:
            with open(cluster_map_filename, 'w') as f: json.dump(cluster_edge_map_data_for_sim, f, indent=4)
            print("Successfully saved cluster->edge map.")
        except IOError as e: print(f"[ERROR] Failed to save cluster->edge map: {e}")

        try:
            with open(confu_file, "w") as f:
                lines = lab_config_str.splitlines();
                for line in lines: f.write(line.rstrip() + "\n")
            print(f"Successfully generated {confu_file}")
        except IOError as e: print(f"Error writing {confu_file}: {e}"); exit(1)

        print("+++ Lab Generation Script Finished +++")
        print(f"+++ Generated {len(command_snippet_files)} command snippet files in '{snippet_dir}/'. +++")
        print(f"ROUTERS_GENERATED={num_clusters}") # This is now number of sensor clusters

    except IOError as e:
        print(f"[ERROR] Could not open or write to ML training data log file {ML_TRAINING_DATA_FILE}: {e}")
        exit(1)
