#!/usr/bin/env python3
import networkx as nx
import random
import os
import math
import json
import shutil
import csv
from datetime import datetime, timedelta

# --- NEW: Attempt to import ML assessor for prediction by automation.py ---
ML_ASSESSOR_AVAILABLE = False
try:
    import ml_risk_assessor # Expects ml_risk_assessor.py in the same directory or PYTHONPATH
    # We also need to ensure model files exist for prediction to actually work
    if (os.path.exists(ml_risk_assessor.RELIABILITY_MODEL_PATH) and
        os.path.exists(ml_risk_assessor.NOISY_CONFIG_MODEL_PATH) and
        os.path.exists(ml_risk_assessor.PREPROCESSOR_PATH)):
        ML_ASSESSOR_AVAILABLE = True
        print("INFO: ML Risk Assessor module and models loaded by automation.py for predictions.")
    else:
        print("WARNING: ML Risk Assessor module loaded, but model/preprocessor files are missing. Predictions will use fallbacks.")
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

# --- Fallback AI-Driven Attributes (if ML model not available during automation.py prediction step) ---
FALLBACK_DEVICE_RELIABILITY_RANGE_AUTO = (60.0, 90.0)
FALLBACK_PREDICTED_NOISE_PROB_RANGE_AUTO = (0.05, 0.30)
FALLBACK_DATA_CONSISTENCY_BASELINE_AUTO = (0.6, 0.95)


def generate_graph(num_nodes, density_factor, seed):
    # ... (no changes to this function)
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
    config_lines = []
    global command_snippet_files # uses global from main script context
    command_snippet_files = []

    backbone_lan_name = "backbone0"
    backbone_subnet_prefix = "192.168.254"
    router_details = {}
    # sensor_ips = {} # Not strictly needed if IP is in the static profile
    light_sensor_map_data = {} # Will contain static profiles + ML predictions
    sensors_static_profiles_for_map_build = {} # cluster_id -> full profile for map construction

    print(f"\n--- Generating Kathara config for {num_clusters} Clusters/Routers ---")

    for cluster_id in range(1, num_clusters + 1):
        # Router Definition
        # ... (no changes to router definition part) ...
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
        machine_name = f"cluster{cluster_id}_machine1"
        sensor_ip_address = f"10.{cluster_id}.1.1"
        # sensor_ips[cluster_id] = sensor_ip_address # Not strictly needed anymore for map
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

        # This is the dictionary of features the ML model expects for prediction
        current_sensor_static_features_for_ml = {
            "manufacturer": sensor_manufacturer,
            "software_version": sensor_software_version_str,
            "is_signed": 1 if software_is_signed else 0, # ML model expects numerical
            "software_age_years": software_age_years,
            "device_age_years": device_age_years
        }

        # --- NEW: Perform ML Prediction for initial attributes ---
        ml_predicted_attributes = None
        if ML_ASSESSOR_AVAILABLE:
            try:
                ml_predicted_attributes = ml_risk_assessor.predict_initial_attributes(current_sensor_static_features_for_ml)
                # print(f"  Sensor {cluster_id} ML Preds: {ml_predicted_attributes}") # Can be verbose
            except Exception as e:
                print(f"  WARNING: ML prediction failed for sensor {cluster_id}: {e}. Using fallbacks.")
                ml_predicted_attributes = None # Ensure fallback

        # Full profile to store in light_sensor_map.json (static features + ML predictions/fallbacks)
        full_sensor_profile_for_map = {
            "ip": sensor_ip_address,
            "manufacturer": sensor_manufacturer,
            "software_version": sensor_software_version_str,
            "is_signed": software_is_signed, # Store boolean for easier reading in JSON
            "software_age_years": software_age_years,
            "device_age_years": device_age_years,
            # Add ML predictions or fallbacks
            "ml_predicted_reliability": round(ml_predicted_attributes.get("predicted_inherent_reliability", random.uniform(*FALLBACK_DEVICE_RELIABILITY_RANGE_AUTO)),1) if ml_predicted_attributes else round(random.uniform(*FALLBACK_DEVICE_RELIABILITY_RANGE_AUTO),1),
            "ml_predicted_noise_propensity": round(ml_predicted_attributes.get("predicted_is_noisy_probability", random.uniform(*FALLBACK_PREDICTED_NOISE_PROB_RANGE_AUTO)),2) if ml_predicted_attributes else round(random.uniform(*FALLBACK_PREDICTED_NOISE_PROB_RANGE_AUTO),2),
            "ml_initial_data_consistency": round(random.uniform(*FALLBACK_DATA_CONSISTENCY_BASELINE_AUTO), 2) # Fallback for now, ML could also predict this
        }
        sensors_static_profiles_for_map_build[cluster_id] = full_sensor_profile_for_map


        # --- Calculate "ground truth" for ML training based on static features (as before) ---
        mf_profile = MANUFACTURER_PROFILES[sensor_manufacturer]
        # sw_profile is already defined

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

        # Log for ML training data (features used for prediction + ground truth targets)
        ml_training_log_writer.writerow([
            f"sensor_{cluster_id}",
            sensor_manufacturer, # Feature
            sensor_software_version_str, # Feature
            int(software_is_signed), # Feature (already numerical for ML)
            software_age_years, # Feature
            device_age_years, # Feature
            gt_reliability, # Target 1
            int(sensor_will_be_configured_noisy) # Target 2
        ])

        # Generate commands for sensor's /etc/sensor_config and /etc/sensor_profile
        # ... (sensor config file generation remains largely the same, uses sensor_will_be_configured_noisy)
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
            f"# Python script for sensor_server.py is started by Docker CMD\n"
        )
        try:
            with open(client_cmds_filename, "w") as f_cmd: f_cmd.write(client_command_content)
            command_snippet_files.append(client_cmds_filename)
        except IOError as e: print(f"[ERROR] Writing {client_cmds_filename}: {e}")


    # --- Create Light -> Sensor Mapping (Augmented with Static Features + ML Predictions) ---
    print("\n--- Creating Traffic Light to Sensor Map (with Static Features & ML Preds) ---")
    edge_to_cluster_id_map = {tuple(sorted(v['edge'])): k
                           for k, v in cluster_edge_map_input.items()
                           if 'edge' in v and isinstance(v['edge'], list) and len(v['edge']) == 2}

    for light_node_id_int in nodes_with_lights:
        light_node_id_str = str(light_node_id_int)
        light_sensor_map_data[light_node_id_str] = {}
        if light_node_id_int not in G:
             print(f"Warning: Node {light_node_id_int} for light not in graph G. Skipping map entry.")
             continue
        for neighbor_node_int in G.neighbors(light_node_id_int):
            edge_key_tuple = tuple(sorted((light_node_id_int, neighbor_node_int)))
            edge_key_str = f"{edge_key_tuple[0]}-{edge_key_tuple[1]}"
            mapped_cluster_id_str = edge_to_cluster_id_map.get(edge_key_tuple)
            if mapped_cluster_id_str:
                mapped_cluster_id_int = int(mapped_cluster_id_str)
                # Get the full profile (static + ML preds) generated earlier
                full_sensor_profile_for_current_map_entry = sensors_static_profiles_for_map_build.get(mapped_cluster_id_int)
                if full_sensor_profile_for_current_map_entry:
                    light_sensor_map_data[light_node_id_str][edge_key_str] = full_sensor_profile_for_current_map_entry
                else:
                    print(f"CRITICAL WARNING: Full sensor profile for cluster {mapped_cluster_id_int} (edge {edge_key_str}) not found during map creation. This sensor will be missing in the light's map.")
    
    # --- Define Traffic Light Devices, Traffic Server, RIP Config (no changes needed in these sections for this request) ---
    # ... (these sections remain the same as your last correct version) ...
    # --- Define Traffic Light Devices ---
    print(f"\n--- Defining {len(nodes_with_lights)} Traffic Light Devices ---")
    light_ip_counter = 100
    default_light_gateway = f"{backbone_subnet_prefix}.1"
    for node_id_int_tl in nodes_with_lights: # renamed var to avoid conflict
        node_id_str_tl = str(node_id_int_tl)
        light_name = f"traffic_light_{node_id_str_tl}"
        light_ip = f"{backbone_subnet_prefix}.{light_ip_counter}"; light_ip_counter += 1
        config_lines.extend([
            f"{light_name}[image]={traffic_light_image}    $",
            f"{light_name}[0]={backbone_lan_name}    $ip({light_ip}/24); to(default, {default_light_gateway});"
        ])
        light_cmds_filename = os.path.join(snippet_dir, f"{light_name}.cmds")
        light_command_content_tl = ( # renamed var
            f"\n# Traffic Light {light_name} Startup Commands\n"
            f'echo "Setting traffic light identity file..."\n'
            f"echo {node_id_int_tl} > /etc/node_id\n"
            f"# Python script traffic_light_controller.py started by Docker CMD\n"
        )
        try:
            with open(light_cmds_filename, "w") as f_cmd: f_cmd.write(light_command_content_tl)
            command_snippet_files.append(light_cmds_filename)
        except IOError as e: print(f"[ERROR] Writing {light_cmds_filename}: {e}")

    # --- Define Traffic Server ---
    traffic_server_name = "traffic_server"
    default_server_gateway = f"{backbone_subnet_prefix}.1"
    config_lines.extend([
        f"{traffic_server_name}[image]={traffic_server_image}  $",
        f"{traffic_server_name}[0]={backbone_lan_name}  $ip({traffic_server_ip}/24); to(default, {default_server_gateway});"
    ])
    
    # --- Add RIP Configuration to Routers ---
    print(f"\n--- Adding RIP configuration to router definitions ---")
    for cluster_id_rip_cfg in range(1, num_clusters + 1): # renamed var
        if cluster_id_rip_cfg not in router_details:
            print(f"Warning: Router details for cluster_id {cluster_id_rip_cfg} not found for RIP config.")
            continue
        router_name_rip_cfg = router_details[cluster_id_rip_cfg]["name"]
        lan_subnet_rip_cfg = f"10.{cluster_id_rip_cfg}.1.0/24"
        backbone_subnet_rip_cfg = f"{backbone_subnet_prefix}.0/24"
        rip_command = f"rip({router_name_rip_cfg}, {lan_subnet_rip_cfg}, connected); rip({router_name_rip_cfg}, {backbone_subnet_rip_cfg}, connected);"
        router_eth1_line_start = f"{router_name_rip_cfg}[1]={backbone_lan_name}"
        found_line_to_append = False
        for i in range(len(config_lines) - 1, -1, -1):
            line_parts = config_lines[i].split('$', 1); definition_part = line_parts[0].strip()
            if definition_part.startswith(router_eth1_line_start):
                existing_commands = line_parts[1].strip() if len(line_parts) > 1 else ""
                if existing_commands and not existing_commands.endswith(';'): existing_commands += ";"
                separator = " " if existing_commands else ""
                config_lines[i] = f"{definition_part}    ${existing_commands}{separator}{rip_command}"
                found_line_to_append = True; break
        if not found_line_to_append:
             print(f"Warning: Could not find backbone interface line for router {router_name_rip_cfg} to append RIP config.")


    print("--- Kathara Configuration Generation Complete ---")
    return "\n".join(config_lines), "", light_sensor_map_data


# ======================================================
# Main Execution Part
# ======================================================
if __name__ == "__main__":
    # ... (parameters remain the same) ...
    graph_num_nodes = 15
    graph_density_factor = 0.4
    graph_seed = random.randint(1, 10000) 
    CLUSTER_EDGE_RATIO = 3
    FRACTION_NODES_WITH_LIGHTS = 0.4

    client_image = "bensrubishcode/traffic_sensor"
    router_image = "bensrubishcode/my_router_image"
    traffic_light_image = "bensrubishcode/traffic_light"
    traffic_server_image = "bensrubishcode/traffic_server"
    traffic_server_ip = "192.168.254.200"
    confu_file = "lab.confu"
    graph_data_filename = "graph_structure.json"
    light_sensor_map_filename = "light_sensor_map.json"
    cluster_map_filename = "cluster_edge_map.json"

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
                ]) # Matches the features and targets
            print(f"ML training data will be logged to: {ML_TRAINING_DATA_FILE}")

            print("+++ Starting Graph Generation +++")
            G = generate_graph(graph_num_nodes, graph_density_factor, graph_seed)
            # ... (rest of graph generation and main logic remains the same) ...
            if G is None: exit("[ERROR] Graph generation failed.")
            if not nx.is_connected(G):
                print("Warning: Graph is not connected. Using the largest connected component.")
                largest_cc_nodes = max(nx.connected_components(G), key=len)
                G = G.subgraph(largest_cc_nodes).copy()
                if G.number_of_edges() == 0 or G.number_of_nodes() <= 1:
                    exit("[ERROR] Largest connected component is too small or empty.")
            print(f"Graph finalized with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")

            all_edges = list(G.edges(data=True)); num_edges = len(all_edges)
            if num_edges == 0: exit("[ERROR] No edges in the graph.")
            num_clusters_target = max(1, math.ceil(num_edges / CLUSTER_EDGE_RATIO))
            num_clusters = min(num_clusters_target, num_edges)
            print(f"\nTargeting {num_clusters} monitoring clusters.");
            monitored_edges_indices = random.sample(range(num_edges), k=num_clusters)
            cluster_edge_map_data_for_sim = {} # Renamed
            print("--- Cluster->Edge Mapping ---");
            for i, edge_index in enumerate(monitored_edges_indices):
                cluster_id_loop = i + 1 # Renamed var
                u, v, data = all_edges[edge_index]
                edge_tuple = tuple(sorted((u, v)))
                cluster_edge_map_data_for_sim[str(cluster_id_loop)] = {"edge": list(edge_tuple)}

            candidate_nodes = [node for node, degree in G.degree() if degree > 1]
            if not candidate_nodes: nodes_with_lights = set()
            else:
                num_lights_to_place = math.ceil(len(candidate_nodes) * FRACTION_NODES_WITH_LIGHTS)
                k = min(num_lights_to_place, len(candidate_nodes))
                nodes_with_lights = set(random.sample(candidate_nodes, k=k)) if k > 0 else set()
            print(f"\nSelected {len(nodes_with_lights)} nodes for traffic lights: {nodes_with_lights if nodes_with_lights else 'None'}")

            print(f"\nSaving graph structure to {graph_data_filename}...")
            try:
                graph_data_to_save = nx.node_link_data(G)
                with open(graph_data_filename, 'w') as f: json.dump(graph_data_to_save, f, indent=4)
                print("Successfully saved graph structure.")
            except Exception as e: print(f"[ERROR] Failed to save graph structure: {e}")

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
            print("Successfully generated {confu_file}")
        except IOError as e: print(f"Error writing {confu_file}: {e}"); exit(1)

        print("+++ Lab Generation Script Finished +++")
        print(f"+++ Generated {len(command_snippet_files)} command snippet files in '{snippet_dir}/'. +++")
        print(f"ROUTERS_GENERATED={num_clusters}")

    except IOError as e:
        print(f"[ERROR] Could not open or write to ML training data log file {ML_TRAINING_DATA_FILE}: {e}")
        exit(1)
