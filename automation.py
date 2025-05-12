#!/usr/bin/env python3
import networkx as nx
import random
import os
import math
import json
import shutil
import csv
import argparse # For command-line arguments
from datetime import datetime, timedelta

# --- ML Assessor Import ---
ML_ASSESSOR_AVAILABLE = False
ML_MODELS_PRESENT = False
try:
    # This script (automation.py) runs on the host.
    # It needs access to ml_risk_assessor.py and the trained .joblib model files.
    # Ensure ml_risk_assessor.py is in the same directory or in PYTHONPATH.
    # The .joblib files should also be in the current working directory when this runs.
    import ml_risk_assessor
    if (os.path.exists(ml_risk_assessor.RELIABILITY_MODEL_PATH) and
        os.path.exists(ml_risk_assessor.NOISY_CONFIG_MODEL_PATH) and
        os.path.exists(ml_risk_assessor.PREPROCESSOR_PATH)):
        ML_ASSESSOR_AVAILABLE = True
        ML_MODELS_PRESENT = True # Both module and models are there
        print("INFO: ML Risk Assessor module and models found by automation.py. Predictions will be attempted.")
    elif ml_risk_assessor: # Module imported but models missing
        ML_ASSESSOR_AVAILABLE = True # Module is there, but models aren't
        print("WARNING: ML Risk Assessor module loaded, but model/preprocessor files are missing. Predictions will use fallbacks.")
    else: # Should not happen if import itself succeeded
        print("WARNING: ml_risk_assessor module was found but is in an unexpected state (e.g. paths not defined).")
except ImportError:
    print("WARNING: ml_risk_assessor.py not found by automation.py. Initial sensor attributes will use fallbacks.")
except AttributeError: # This can happen if ml_risk_assessor.py is imported but paths are not defined in it
    print("WARNING: ml_risk_assessor.py might be missing model path constants. Initial sensor attributes will use fallbacks.")
except Exception as e:
    print(f"WARNING: Error importing or checking ml_risk_assessor in automation.py: {e}. Predictions will use fallbacks.")

# --- Static Device Profile Configurations ---
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

# --- Kathara Config Generation (globals & constants) ---
CMD_SNIPPET_DIR = "cmd_snippets" # Standardized snippet directory name
ML_TRAINING_DATA_FILE = "ml_training_data.csv"

FALLBACK_DEVICE_RELIABILITY_RANGE_AUTO = (60.0, 90.0)
FALLBACK_PREDICTED_NOISE_PROB_RANGE_AUTO = (0.05, 0.30)
FALLBACK_DATA_CONSISTENCY_BASELINE_AUTO = (0.6, 0.95)

# --- Modular Device Configuration ---
DEPLOYABLE_DEVICE_CONFIGS = [
    {
        "type": "traffic_light",
        "image": "bensrubishcode/traffic_light",
        "placement_type": "on_nodes",
        "candidate_logic": "degree_threshold",
        "node_degree_min": 2,
        "selection_fraction": 0.4,
        "min_total_devices": 1,
        "max_total_devices": 5
    },
    {
        "type": "traffic_sensor",
        "image": "bensrubishcode/traffic_sensor",
        "placement_type": "on_edges",
        "monitoring_logic": "traffic_light_approaches",
        "min_sensors_per_edge": 1,
        "max_sensors_per_edge": 3,
        "fallback_monitored_edge_count": 3
    }
]

# --- Core Infrastructure Docker Images & Network Details ---
CORE_INFRA_CONFIG = {
    "router": {
        "image": "bensrubishcode/my_router_image" # Ensure this image exists or use a Kathara default one
    },
    "traffic_server": {
        "image": "bensrubishcode/traffic_server", # Ensure this image exists
        "ip_address": "192.168.254.253",
        "kathara_name": "traffic_server"
    },
    "backbone_lan": {
        "name": "backbone0",
        "subnet_prefix": "192.168.254",
        "default_gateway_ip_suffix": ".1"
    },
    "sensor_lan_base_prefix": "10.{{cluster_id}}.1"
}

# --- Global list for command snippet files, populated during lab generation ---
command_snippet_files = []


def generate_graph(num_nodes, density_factor, seed_val_str):
    """Generates a random graph based on input parameters."""
    if seed_val_str == "random":
        seed_val = random.randint(1, 100000)
    else:
        try:
            seed_val = int(seed_val_str)
        except ValueError:
            print(f"Warning: Invalid seed '{seed_val_str}', using random.")
            seed_val = random.randint(1, 100000)
    print(f"Using graph generation seed: {seed_val}")
    random.seed(seed_val)

    density_factor = max(0.0, min(1.0, density_factor))
    edge_probability = 0.05 + 0.3 * density_factor
    speed_limit_range = (30, 60) if density_factor > 0.5 else (70, 120)

    G_candidate = None
    for _ in range(10):
        G_candidate = nx.erdos_renyi_graph(n=num_nodes, p=edge_probability, seed=random.randint(1, 10000))
        if G_candidate.number_of_edges() > 0:
            break
    else:
        print(f"[Warning] Could not generate graph with edges for n={num_nodes}, p={edge_probability}.")
        return None

    if G_candidate.number_of_edges() == 0:
        print(f"[Error] Graph resulted in 0 edges for n={num_nodes}, p={edge_probability}.")
        return None

    for (u, v) in G_candidate.edges():
        G_candidate[u][v]['speed_limit'] = random.randint(*speed_limit_range)
        G_candidate[u][v]['capacity'] = random.randint(20, 100)
        G_candidate[u][v]['distance'] = round(random.uniform(MIN_EDGE_DISTANCE, MAX_EDGE_DISTANCE), 2)
    return G_candidate

def generate_lab_config(
    G,
    placed_traffic_lights_nodes, # Changed name for clarity: it's a set/list of nodes
    sensor_cluster_definitions_map, # Changed name for clarity
    device_configs_map, # Changed name for clarity
    infra_config, # Changed name for clarity
    ml_training_data_writer # Changed name for clarity
):
    """Generates the content for Kathara's lab.confu file and device command snippets."""
    config_lines = []
    global command_snippet_files
    command_snippet_files = [] # Reset for this generation run

    backbone_lan_name = infra_config["backbone_lan"]["name"]
    backbone_subnet_prefix = infra_config["backbone_lan"]["subnet_prefix"]
    default_gw_ip_on_backbone = f"{backbone_subnet_prefix}{infra_config['backbone_lan']['default_gateway_ip_suffix']}"

    # Extract core image names and IPs from the infra_config dictionary
    core_router_img = infra_config["router"]["image"]
    core_ts_img = infra_config["traffic_server"]["image"]
    core_ts_ip = infra_config["traffic_server"]["ip_address"]
    core_ts_kathara_name = infra_config["traffic_server"]["kathara_name"]

    router_details_generated = {}
    light_sensor_map_for_json = {}
    all_newly_generated_sensor_profiles = {}

    num_sensor_clusters_defined = len(sensor_cluster_definitions_map)
    print(f"\n--- Generating Kathara config for {num_sensor_clusters_defined} Sensor Clusters (Monitored Edges) ---")

    for cluster_id_str, cluster_data in sensor_cluster_definitions_map.items():
        cluster_id_int = int(cluster_id_str)
        current_cluster_lan = f"lan{cluster_id_str}"
        current_router_name = f"router{cluster_id_str}"

        sensor_lan_ip_prefix = infra_config["sensor_lan_base_prefix"].replace("{{cluster_id}}", cluster_id_str)
        router_ip_on_lan = f"{sensor_lan_ip_prefix}.254"
        router_ip_on_backbone = f"{backbone_subnet_prefix}.{cluster_id_int}"

        router_details_generated[cluster_id_int] = {"name": current_router_name, "ip_lan": router_ip_on_lan, "ip_backbone": router_ip_on_backbone}
        config_lines.extend([
            f"{current_router_name}[image]={core_router_img}    $",
            f"{current_router_name}[privileged]=true    $",
            f"{current_router_name}[0]={current_cluster_lan}    $ip({router_ip_on_lan}/24);",
            f"{current_router_name}[1]={backbone_lan_name}    $ip({router_ip_on_backbone}/24);"
        ])

        num_sensors_for_this_cluster = cluster_data.get("num_sensors", 0)
        edge_tuple_for_cluster = tuple(cluster_data.get('edge', ('N/A','N/A')))
        edge_str_display = f"{edge_tuple_for_cluster[0]}-{edge_tuple_for_cluster[1]}"
        sensor_type_defined = cluster_data.get("sensor_type", "unknown_sensor")
        sensor_config_details = device_configs_map.get(sensor_type_defined, {})
        sensor_image_name = sensor_config_details.get("image", "default_sensor_image")

        for sensor_index_in_cluster in range(1, num_sensors_for_this_cluster + 1):
            sensor_base_name = sensor_config_details.get("type", sensor_type_defined)
            sensor_kathara_name = f"cluster{cluster_id_str}_{sensor_base_name}{sensor_index_in_cluster}"
            sensor_ip_on_lan = f"{sensor_lan_ip_prefix}.{sensor_index_in_cluster}"
            globally_unique_sensor_id = f"s_{cluster_id_str}_{sensor_index_in_cluster}"

            config_lines.extend([
                f"{sensor_kathara_name}[image]={sensor_image_name}    $",
                f"{sensor_kathara_name}[0]={current_cluster_lan}    $ip({sensor_ip_on_lan}/24); to(default, {router_ip_on_lan});"
            ])

            mfg_name = random.choice(list(MANUFACTURER_PROFILES.keys()))
            sw_version_key = random.choice(list(SOFTWARE_PROFILES.keys()))
            sw_version_profile = SOFTWARE_PROFILES[sw_version_key]
            is_sw_signed = sw_version_profile["is_signed"]
            sw_age_val = sw_version_profile["release_date_offset_years"]
            dev_age_val = round(random.uniform(MIN_DEVICE_AGE_YEARS, MAX_DEVICE_AGE_YEARS), 2)
            static_features_for_prediction = {
                "manufacturer": mfg_name, "software_version": sw_version_key,
                "is_signed": 1 if is_sw_signed else 0,
                "software_age_years": sw_age_val, "device_age_years": dev_age_val
            }
            ml_predictions = None
            if ML_ASSESSOR_AVAILABLE and ML_MODELS_PRESENT:
                try:
                    ml_predictions = ml_risk_assessor.predict_initial_attributes(static_features_for_prediction)
                except Exception as e:
                    print(f"  WARNING: ML prediction for {globally_unique_sensor_id} failed: {e}. Fallbacks used.")
                    ml_predictions = None
            current_sensor_full_profile = {
                "ip": sensor_ip_on_lan, "unique_sensor_id": globally_unique_sensor_id,
                "manufacturer": mfg_name, "software_version": sw_version_key,
                "is_signed": is_sw_signed, "software_age_years": sw_age_val, "device_age_years": dev_age_val,
                "ml_predicted_reliability": round(ml_predictions.get("predicted_inherent_reliability", random.uniform(*FALLBACK_DEVICE_RELIABILITY_RANGE_AUTO)),1) if ml_predictions else round(random.uniform(*FALLBACK_DEVICE_RELIABILITY_RANGE_AUTO),1),
                "ml_predicted_noise_propensity": round(ml_predictions.get("predicted_is_noisy_probability", random.uniform(*FALLBACK_PREDICTED_NOISE_PROB_RANGE_AUTO)),2) if ml_predictions else round(random.uniform(*FALLBACK_PREDICTED_NOISE_PROB_RANGE_AUTO),2),
                "ml_initial_data_consistency": round(random.uniform(*FALLBACK_DATA_CONSISTENCY_BASELINE_AUTO), 2)
            }
            all_newly_generated_sensor_profiles[globally_unique_sensor_id] = current_sensor_full_profile
            mfg_p = MANUFACTURER_PROFILES[mfg_name]
            gt_inherent_reliability = mfg_p["base_reliability"] + sw_version_profile["reliability_modifier"]
            if is_sw_signed: gt_inherent_reliability += mfg_p["signature_bonus"]
            gt_inherent_reliability -= dev_age_val * mfg_p["age_degradation_factor"]
            if sw_age_val > 1.0: gt_inherent_reliability -= min(sw_age_val, MAX_SOFTWARE_AGE_FOR_PENALTY) * mfg_p["sw_age_penalty_factor"]
            gt_inherent_reliability = round(max(0, min(100, gt_inherent_reliability)),1)
            gt_sensor_noise_probability = mfg_p["base_noise_probability"] * sw_version_profile["noise_modifier_factor"]
            gt_sensor_noise_probability += (dev_age_val / MAX_DEVICE_AGE_YEARS) * 0.10
            if sw_age_val > 1.0: gt_sensor_noise_probability += (min(sw_age_val, MAX_SOFTWARE_AGE_FOR_PENALTY) / MAX_SOFTWARE_AGE_FOR_PENALTY) * 0.05
            gt_sensor_noise_probability = max(0.01, min(0.99, gt_sensor_noise_probability))
            is_sensor_configured_noisy_gt = random.random() < gt_sensor_noise_probability
            ml_training_data_writer.writerow([
                globally_unique_sensor_id, mfg_name, sw_version_key, int(is_sw_signed),
                sw_age_val, dev_age_val, gt_inherent_reliability, int(is_sensor_configured_noisy_gt)
            ])

            sensor_cmds_file = os.path.join(CMD_SNIPPET_DIR, f"{sensor_kathara_name}.cmds")
            sensor_cmds_content = (
                f"# Sensor {globally_unique_sensor_id} on edge {edge_str_display}\n"
                f'echo "{cluster_id_str}" > /etc/cluster_id\n'
                f'echo "{globally_unique_sensor_id}" > /etc/sensor_id\n'
                f'echo "EDGE={edge_str_display}" > /etc/edge_info\n'
                f'echo "MANUFACTURER={mfg_name}" > /etc/sensor_profile\n'
                f'echo "SOFTWARE_VERSION={sw_version_key}" >> /etc/sensor_profile\n'
                f'echo "IS_SIGNED={str(is_sw_signed).lower()}" >> /etc/sensor_profile\n'
                f'echo "SOFTWARE_AGE_YEARS={sw_age_val}" >> /etc/sensor_profile\n'
                f'echo "DEVICE_AGE_YEARS={dev_age_val}" >> /etc/sensor_profile\n'
                f'echo "MAKE_NOISY={str(is_sensor_configured_noisy_gt).lower()}" > /etc/sensor_config\n'
                f'echo "{core_ts_ip}" > /etc/traffic_server_ip\n'  # <<< THIS LINE IS NOW CORRECTLY ADDED FOR SENSORS
            )
            try:
                with open(sensor_cmds_file, "w") as f: f.write(sensor_cmds_content)
                command_snippet_files.append(sensor_cmds_file)
            except IOError as e: print(f"[ERROR] Writing {sensor_cmds_file}: {e}")

    print("\n--- Creating Traffic Light to Sensor Map (Populating with Sensor Profile Lists) ---")
    for light_node_id in placed_traffic_lights_nodes: # Iterate over the passed set/list
        light_node_id_str = str(light_node_id)
        light_sensor_map_for_json[light_node_id_str] = {}
        if light_node_id not in G:
            print(f"DEBUG MAP GEN: Light node {light_node_id} not in G.")
            continue
        for neighbor_of_light in G.neighbors(light_node_id):
            current_edge_tuple = tuple(sorted((light_node_id, neighbor_of_light)))
            current_edge_str = f"{current_edge_tuple[0]}-{current_edge_tuple[1]}"
            cluster_id_monitoring_this_edge = None
            for c_id, c_data in sensor_cluster_definitions_map.items():
                if tuple(sorted(c_data['edge'])) == current_edge_tuple:
                    cluster_id_monitoring_this_edge = c_id
                    break
            if cluster_id_monitoring_this_edge:
                num_individual_sensors = sensor_cluster_definitions_map[cluster_id_monitoring_this_edge].get("num_sensors",0)
                if num_individual_sensors > 0:
                    if current_edge_str not in light_sensor_map_for_json[light_node_id_str]:
                         light_sensor_map_for_json[light_node_id_str][current_edge_str] = []
                    for s_idx_lookup in range(1, num_individual_sensors + 1):
                        unique_sensor_id_to_find = f"s_{cluster_id_monitoring_this_edge}_{s_idx_lookup}"
                        sensor_profile_to_add = all_newly_generated_sensor_profiles.get(unique_sensor_id_to_find)
                        if sensor_profile_to_add:
                            light_sensor_map_for_json[light_node_id_str][current_edge_str].append(sensor_profile_to_add)

    tl_config_details = device_configs_map.get("traffic_light", {})
    tl_docker_image = tl_config_details.get("image", "unknown_traffic_light_image")
    print(f"\n--- Defining Kathara entries for {len(placed_traffic_lights_nodes)} potential Traffic Light Devices using image {tl_docker_image} ---")
    light_ip_alloc_counter = 100
    actual_traffic_lights_in_kathara = 0

    for tl_node_id in placed_traffic_lights_nodes:
        tl_node_id_str = str(tl_node_id)
        if tl_node_id_str in light_sensor_map_for_json and light_sensor_map_for_json[tl_node_id_str]:
            actual_traffic_lights_in_kathara += 1
            tl_type_for_name = tl_config_details.get("type", "traffic_light")
            tl_kathara_name = f"{tl_type_for_name}_{tl_node_id_str}"
            tl_ip_addr = f"{backbone_subnet_prefix}.{light_ip_alloc_counter}"
            light_ip_alloc_counter += 1
            config_lines.extend([
                f"{tl_kathara_name}[image]={tl_docker_image}    $",
                f"{tl_kathara_name}[0]={backbone_lan_name}    $ip({tl_ip_addr}/24); to(default, {default_gw_ip_on_backbone});"
            ])
            tl_cmds_file_path = os.path.join(CMD_SNIPPET_DIR, f"{tl_kathara_name}.cmds")
            tl_cmds_file_content = (
                f"# {tl_kathara_name} Startup Commands\n"
                f'echo "{tl_node_id}" > /etc/node_id\n'
                f'echo "{core_ts_ip}" > /etc/traffic_server_ip\n'
            )
            try:
                with open(tl_cmds_file_path, "w") as f: f.write(tl_cmds_file_content)
                command_snippet_files.append(tl_cmds_file_path)
            except IOError as e: print(f"[ERROR] Writing {tl_cmds_file_path}: {e}")
    print(f"--- Actually configured {actual_traffic_lights_in_kathara} traffic light devices in Kathara ---")

    config_lines.extend([
        f"{core_ts_kathara_name}[image]={core_ts_img}  $",
        f"{core_ts_kathara_name}[0]={backbone_lan_name}  $ip({core_ts_ip}/24); to(default, {default_gw_ip_on_backbone});"
    ])
    ts_cmds_file_path = os.path.join(CMD_SNIPPET_DIR, f"{core_ts_kathara_name}.cmds")
    ts_cmds_content = (f"# {core_ts_kathara_name} Startup Commands\n"
                       f'echo "Traffic Server Ready."\n')
    try:
        with open(ts_cmds_file_path, "w") as f: f.write(ts_cmds_content)
        command_snippet_files.append(ts_cmds_file_path)
    except IOError as e: print(f"[ERROR] Writing {ts_cmds_file_path}: {e}")

    print(f"\n--- Adding RIP configuration to {num_sensor_clusters_defined} router definitions ---")
    if num_sensor_clusters_defined > 0:
        for c_id_int_key in router_details_generated: # Iterate directly over keys
            r_name_for_rip = router_details_generated[c_id_int_key]["name"]
            lan_sub_prefix = infra_config["sensor_lan_base_prefix"].replace("{{cluster_id}}", str(c_id_int_key))
            lan_sub = f"{lan_sub_prefix}.0/24"
            bb_sub = f"{backbone_subnet_prefix}.0/24"

            rip_cmd_str = f"rip({r_name_for_rip}, {lan_sub}, connected); rip({r_name_for_rip}, {bb_sub}, connected);"
            router_eth1_line_prefix = f"{r_name_for_rip}[1]={backbone_lan_name}"
            found_rip_line = False
            for line_idx_cfg in range(len(config_lines) - 1, -1, -1):
                parts_cfg = config_lines[line_idx_cfg].split('$', 1)
                def_part_cfg = parts_cfg[0].strip()
                if def_part_cfg.startswith(router_eth1_line_prefix):
                    existing_cmds_cfg = parts_cfg[1].strip() if len(parts_cfg) > 1 else ""
                    if existing_cmds_cfg and not existing_cmds_cfg.endswith(';'):
                        existing_cmds_cfg += ";"
                    sep_cfg = " " if existing_cmds_cfg else ""
                    config_lines[line_idx_cfg] = f"{def_part_cfg}    ${existing_cmds_cfg}{sep_cfg}{rip_cmd_str}"
                    found_rip_line = True
                    break
            if not found_rip_line:
                print(f"Warn: Could not find backbone line for router {r_name_for_rip} for RIP.")
    else:
        print("INFO: No sensor clusters, so no RIP configuration for sensor routers.")

    print("--- Kathara Configuration Generation Complete ---")
    return "\n".join(config_lines), "", light_sensor_map_for_json


# ======================================================
# Main Execution Part
# ======================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Kathara lab configurations for ITS simulation.")
    parser.add_argument("--nodes", type=int, default=20, help="Number of nodes in the base graph (default: 20).")
    parser.add_argument("--density", type=float, default=0.3, help="Density factor (0.0-1.0) for graph edges (default: 0.3).")
    parser.add_argument("--seed", default="random", help="Seed for random number generation (integer or 'random', default: 'random').")
    args = parser.parse_args()

    print(f"Running automation with: Nodes={args.nodes}, Density={args.density}, Seed={args.seed}")
    # This ensures CORE_INFRA_CONFIG is used, which has the defined traffic server IP
    print(f"Traffic Server IP will be: {CORE_INFRA_CONFIG['traffic_server']['ip_address']}")

    confu_file = "lab.confu"
    graph_data_filename = "graph_structure.json"
    light_sensor_map_filename = "light_sensor_map.json"
    cluster_map_filename = "cluster_edge_map.json"

    device_configs_map_lookup = {conf["type"]: conf for conf in DEPLOYABLE_DEVICE_CONFIGS}

    if os.path.exists(CMD_SNIPPET_DIR): # Use standardized CMD_SNIPPET_DIR
        shutil.rmtree(CMD_SNIPPET_DIR)
    os.makedirs(CMD_SNIPPET_DIR)
    print(f"Initialized {CMD_SNIPPET_DIR} directory.")

    ml_log_file_exists = os.path.exists(ML_TRAINING_DATA_FILE)
    try:
        with open(ML_TRAINING_DATA_FILE, 'a', newline='') as ml_log_file_handle:
            ml_csv_writer = csv.writer(ml_log_file_handle)
            if not ml_log_file_exists or os.path.getsize(ML_TRAINING_DATA_FILE) == 0:
                ml_csv_writer.writerow([
                    "sensor_id", "manufacturer", "software_version", "is_signed",
                    "software_age_years", "device_age_years",
                    "gt_inherent_reliability", "gt_is_configured_noisy"
                ])
            print(f"ML training data will be logged to (or appended to): {ML_TRAINING_DATA_FILE}")

            print("+++ Starting Graph Generation +++")
            G_main = generate_graph(args.nodes, args.density, args.seed)
            if G_main is None: exit("[ERROR] Graph generation failed or returned None.")

            if not nx.is_connected(G_main):
                print("Warning: Generated graph is not connected. Using the largest connected component.")
                largest_cc = max(nx.connected_components(G_main), key=len)
                G_main_component = G_main.subgraph(largest_cc).copy()
                if G_main_component.number_of_edges() == 0 or G_main_component.number_of_nodes() <= 1:
                    exit("[ERROR] Largest connected component is too small or empty.")
                G_main = G_main_component
            print(f"Graph finalized with {G_main.number_of_nodes()} nodes and {G_main.number_of_edges()} edges.")
            if G_main.number_of_edges() == 0: exit("[ERROR] Final graph has no edges. Cannot proceed.")

            placed_traffic_light_nodes = set()
            tl_config_details = device_configs_map_lookup.get("traffic_light")
            if tl_config_details:
                print(f"\n--- Determining Candidate Nodes for {tl_config_details['type']}s ---")
                if tl_config_details.get("candidate_logic") == "degree_threshold":
                    candidate_nodes_for_tl = [
                        n for n in G_main.nodes()
                        if G_main.degree(n) >= tl_config_details.get("node_degree_min", 2)
                    ]
                    if candidate_nodes_for_tl:
                        num_tl_to_select = math.ceil(len(candidate_nodes_for_tl) * tl_config_details.get("selection_fraction", 0.3))
                        num_tl_to_select = max(tl_config_details.get("min_total_devices", 0), num_tl_to_select)
                        num_tl_to_select = min(tl_config_details.get("max_total_devices", len(candidate_nodes_for_tl)), num_tl_to_select)
                        k_sample_tl = min(num_tl_to_select, len(candidate_nodes_for_tl))
                        if k_sample_tl > 0:
                             placed_traffic_light_nodes = set(random.sample(candidate_nodes_for_tl, k=k_sample_tl))
                print(f"Selected {len(placed_traffic_light_nodes)} nodes for {tl_config_details['type']}s: {placed_traffic_light_nodes if placed_traffic_light_nodes else 'None'}")

            edges_to_be_monitored = set()
            sensor_config_details = device_configs_map_lookup.get("traffic_sensor")
            if sensor_config_details:
                if placed_traffic_light_nodes and sensor_config_details.get("monitoring_logic") == "traffic_light_approaches":
                    print("\n--- Identifying edges to be monitored (all edges connected to traffic lights) ---")
                    for light_n in placed_traffic_light_nodes:
                        if light_n in G_main:
                            for neighbor_n in G_main.neighbors(light_n):
                                edges_to_be_monitored.add(tuple(sorted((light_n, neighbor_n))))
                if not edges_to_be_monitored and G_main.number_of_edges() > 0:
                    print("INFO: No edges monitored via traffic lights (or no lights placed). Selecting random edges for sensors.")
                    num_fallback_edges = min(
                        max(1, G_main.number_of_edges() // 5),
                        sensor_config_details.get("fallback_monitored_edge_count", 3)
                    )
                    all_graph_edges_as_list = list(G_main.edges())
                    if all_graph_edges_as_list:
                        k_sample_edges = min(num_fallback_edges, len(all_graph_edges_as_list))
                        if k_sample_edges > 0:
                            selected_fallback_edges = random.sample(all_graph_edges_as_list, k=k_sample_edges)
                            for u_edge, v_edge in selected_fallback_edges:
                                edges_to_be_monitored.add(tuple(sorted((u_edge, v_edge))))

            final_num_sensor_clusters = 0
            final_sensor_cluster_definitions = {}
            if not edges_to_be_monitored:
                print("WARNING: No edges designated for monitoring. No 'traffic_sensor' devices or associated routers will be created.")
            else:
                print(f"Total of {len(edges_to_be_monitored)} unique edges will be monitored.")
                final_num_sensor_clusters = len(edges_to_be_monitored)
                current_cluster_id = 1
                for edge_tuple_item in edges_to_be_monitored:
                    cluster_id_key_str = str(current_cluster_id)
                    num_sensors_for_this_edge = 1
                    if sensor_config_details:
                        num_sensors_for_this_edge = random.randint(
                            sensor_config_details.get("min_sensors_per_edge",1),
                            sensor_config_details.get("max_sensors_per_edge",1)
                        )
                    final_sensor_cluster_definitions[cluster_id_key_str] = {
                        "edge": [int(n) for n in edge_tuple_item],
                        "num_sensors": num_sensors_for_this_edge,
                        "sensor_type": "traffic_sensor"
                    }
                    current_cluster_id += 1

            print(f"\nSaving graph structure to {graph_data_filename}...")
            try:
                graph_json_data = nx.node_link_data(G_main)
                with open(graph_data_filename, 'w') as f:
                    json.dump(graph_json_data, f, indent=4)
                print("Successfully saved graph structure.")
            except Exception as e:
                print(f"[ERROR] Saving graph: {e}")

            print("\n+++ Starting Kathara Lab Generation +++")
            if os.path.exists(confu_file):
                try:
                    os.remove(confu_file)
                    print(f"Deleted existing {confu_file}")
                except OSError as e:
                    print(f"Error deleting {confu_file}: {e}")

            # Call lab config generation with standardized parameter names
            lab_config_str_content, _, final_light_sensor_map = generate_lab_config(
                G_main,
                placed_traffic_light_nodes, # Pass the set directly
                final_sensor_cluster_definitions,
                device_configs_map_lookup, # Pass the lookup map by type
                CORE_INFRA_CONFIG, # Pass the core infrastructure configuration dictionary
                ml_csv_writer # Pass the CSV writer object
            )

        # This block should be inside the 'with open(ML_TRAINING_DATA_FILE...'
        # but after the call to generate_lab_config if that function needs the writer.
        # Assuming generate_lab_config uses the writer.

        print(f"ML training data generation for this iteration complete. Appended to {ML_TRAINING_DATA_FILE}")

        print(f"\nSaving light->sensor map to {light_sensor_map_filename}...")
        try:
            with open(light_sensor_map_filename, 'w') as f:
                json.dump(final_light_sensor_map, f, indent=4)
            print("Successfully saved light->sensor map.")
        except IOError as e:
            print(f"[ERROR] Failed to save light->sensor map: {e}")

        print(f"\nSaving cluster (monitored edge) map to {cluster_map_filename}...")
        try:
            with open(cluster_map_filename, 'w') as f:
                json.dump(final_sensor_cluster_definitions, f, indent=4)
            print("Successfully saved cluster (monitored edge) map with sensor counts.")
        except IOError as e:
            print(f"[ERROR] Failed to save cluster (monitored edge) map: {e}")

        try:
            with open(confu_file, "w") as f:
                lines = lab_config_str_content.splitlines()
                for line in lines:
                    f.write(line.rstrip() + "\n") # Ensure clean lines
            print(f"Successfully generated {confu_file}")
        except IOError as e:
            print(f"Error writing {confu_file}: {e}")
            exit(1)

        print("+++ Lab Generation Script Finished +++")
        # Ensure CMD_SNIPPET_DIR is used here as well for consistency
        print(f"+++ Generated {len(command_snippet_files)} command snippet files in '{CMD_SNIPPET_DIR}/'. +++")
        print(f"ROUTERS_GENERATED={final_num_sensor_clusters}")

    except IOError as e:
        print(f"[ERROR] Could not open or write to ML training data log file {ML_TRAINING_DATA_FILE}: {e}")
        exit(1)
    except Exception as e:
        print(f"[FATAL ERROR] An unexpected error occurred in main: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

