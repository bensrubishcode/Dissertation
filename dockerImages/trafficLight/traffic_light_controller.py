#!/usr/bin/env python3

import time
import requests
import json
import os
import random
import socket
import threading
import numpy as np
import skfuzzy as fuzz
from skfuzzy import control as ctrl

# --- NO ML IMPORTS NEEDED HERE ANYMORE ---
# # import ml_risk_assessor (REMOVED)

# --- Configuration ---
CENTRAL_SERVER_URL = "http://192.168.254.200:5000"
NODE_ID_FILE = "/etc/node_id"
LIGHT_SENSOR_MAP_FILE = "/shared/light_sensor_map.json" # Expects static features + ML preds
EVALUATION_INTERVAL_SECONDS = 5.0
SENSOR_QUERY_TIMEOUT_SECONDS = 1.5
CENTRAL_QUERY_TIMEOUT_SECONDS = 3.0
SENSOR_LISTEN_PORT = 5001
CONFIG_WAIT_TIMEOUT_SECONDS = 35
CONFIG_CHECK_INTERVAL_SECONDS = 0.5

# --- Trust & Attribute Configuration ---
INITIAL_DATA_TRUST_SCORE = 75.0
TRUST_UPDATE_ALPHA = 0.3
TRUST_DECAY_FAILURE = 5.0
TRUST_DECAY_IMPLAUSIBLE = 3.0
TRUST_FLOOR = 10.0
TRUST_CEILING = 100.0
TRUST_THRESHOLD_FOR_PREDICTION = 40.0
MAX_PLAUSIBLE_TRAFFIC = 200

# Fallbacks are mostly for internal use now if map is malformed,
# as ML preds are expected to be IN the map file.
FALLBACK_DEVICE_RELIABILITY_MAP = 70.0
FALLBACK_PREDICTED_NOISE_PROP_MAP = 0.15
FALLBACK_DATA_CONSISTENCY_MAP = 0.80


# --- Fuzzy Logic Config (Antecedents will use values from sensor_attributes) ---
deviation_universe = np.arange(0, 21, 1)
device_reliability_universe = np.arange(0, 101, 1)
data_consistency_universe = np.arange(0, 1.01, 0.01)
predicted_noise_prop_universe = np.arange(0, 1.01, 0.01)
trust_update_output_universe = np.arange(0, 101, 1)

# --- Global State ---
my_node_id = None
# sensor_static_and_ml_profiles_map:
# { 'edge_str': {'ip':..., 'manufacturer':..., 'ml_predicted_reliability':..., ...}, ... }
sensor_static_and_ml_profiles_map = {}
state_lock = threading.Lock()
sensor_data_trust_scores = {} # { 'sensor_ip': current_data_trust_score }
# sensor_attributes now directly uses values from the map (static + ML-derived)
# and might hold dynamically updated scores like a running data_consistency
sensor_attributes = {}   # { 'sensor_ip': {'device_reliability': X, 'data_consistency': Y, 'predicted_noise_propensity': Z} }

# --- Fuzzy Logic Setup (remains the same as your last correct version) ---
deviation = ctrl.Antecedent(deviation_universe, 'deviation')
device_reliability_input = ctrl.Antecedent(device_reliability_universe, 'device_reliability_input')
data_consistency_input = ctrl.Antecedent(data_consistency_universe, 'data_consistency_input')
predicted_noise_prop_input = ctrl.Antecedent(predicted_noise_prop_universe, 'predicted_noise_prop_input')
trust_update_consequent = ctrl.Consequent(trust_update_output_universe, 'trust_update_output')

deviation['low'] = fuzz.trimf(deviation.universe, [0, 0, 5])
deviation['medium'] = fuzz.trimf(deviation.universe, [3, 10, 17])
deviation['high'] = fuzz.trimf(deviation.universe, [15, 20, 20])
device_reliability_input['low'] = fuzz.trimf(device_reliability_input.universe, [0, 25, 50])
device_reliability_input['medium'] = fuzz.trimf(device_reliability_input.universe, [40, 60, 80])
device_reliability_input['high'] = fuzz.trimf(device_reliability_input.universe, [70, 85, 100])
data_consistency_input['poor'] = fuzz.trimf(data_consistency_input.universe, [0, 0.25, 0.5])
data_consistency_input['fair'] = fuzz.trimf(data_consistency_input.universe, [0.4, 0.6, 0.8])
data_consistency_input['good'] = fuzz.trimf(data_consistency_input.universe, [0.7, 0.85, 1.0])
predicted_noise_prop_input['low'] = fuzz.trimf(predicted_noise_prop_input.universe, [0, 0.15, 0.3])
predicted_noise_prop_input['medium'] = fuzz.trimf(predicted_noise_prop_input.universe, [0.2, 0.4, 0.6])
predicted_noise_prop_input['high'] = fuzz.trimf(predicted_noise_prop_input.universe, [0.5, 0.75, 1.0])
trust_update_consequent['low'] = fuzz.trimf(trust_update_consequent.universe, [0, 20, 40])
trust_update_consequent['medium'] = fuzz.trimf(trust_update_consequent.universe, [30, 50, 70])
trust_update_consequent['high'] = fuzz.trimf(trust_update_consequent.universe, [60, 80, 100])

rule1 = ctrl.Rule(deviation['low'] & device_reliability_input['high'] & data_consistency_input['good'] & predicted_noise_prop_input['low'], trust_update_consequent['high'])
rule2 = ctrl.Rule(deviation['medium'] & device_reliability_input['medium'], trust_update_consequent['medium'])
rule3 = ctrl.Rule(deviation['high'] | device_reliability_input['low'] | data_consistency_input['poor'] | predicted_noise_prop_input['high'], trust_update_consequent['low'])
rule4 = ctrl.Rule(predicted_noise_prop_input['medium'] & deviation['low'], trust_update_consequent['medium'])
# Add more rules for comprehensive logic

try:
    fuzzy_antecedents = [deviation, device_reliability_input, data_consistency_input, predicted_noise_prop_input]
    trust_ctrl_system = ctrl.ControlSystem([rule1, rule2, rule3, rule4])
    trust_simulation_instance = ctrl.ControlSystemSimulation(trust_ctrl_system)
    print("Fuzzy control system initialized successfully.")
except Exception as e:
    print(f"FATAL: Failed to initialize fuzzy control system: {e}")
    trust_simulation_instance = None
# --- End Fuzzy Logic Setup ---

def get_node_id():
    # ... (no changes) ...
    if not os.path.exists(NODE_ID_FILE): print(f"Error: {NODE_ID_FILE} not found"); return None
    try:
        with open(NODE_ID_FILE, 'r') as f: return int(f.readline().strip())
    except Exception as e: print(f"Error reading {NODE_ID_FILE}: {e}"); return None


def load_sensor_map_and_attributes(node_id):
    """
    Loads the sensor map (which includes static features AND pre-calculated ML predictions).
    Initializes data_trust_scores and sensor_attributes from the map.
    """
    global sensor_static_and_ml_profiles_map, sensor_data_trust_scores, sensor_attributes
    if node_id is None: return False
    if not os.path.exists(LIGHT_SENSOR_MAP_FILE):
        print(f"Error: Map file {LIGHT_SENSOR_MAP_FILE} not found.")
        return False
    try:
        with open(LIGHT_SENSOR_MAP_FILE, 'r') as f: full_map_data = json.load(f)
        node_id_str = str(node_id)
        if node_id_str in full_map_data:
            sensor_static_and_ml_profiles_map = full_map_data[node_id_str] # This is the rich map
            print(f"Loaded sensor static & ML profiles map for Node {node_id}: {len(sensor_static_and_ml_profiles_map)} sensors.")

            with state_lock:
                sensor_data_trust_scores.clear()
                sensor_attributes.clear()
                for edge_str, full_profile in sensor_static_and_ml_profiles_map.items():
                    sensor_ip = full_profile.get("ip")
                    if not sensor_ip:
                        print(f"Warning: Sensor IP missing for edge {edge_str} in map. Skipping.")
                        continue

                    sensor_data_trust_scores[sensor_ip] = INITIAL_DATA_TRUST_SCORE # Initial trust in data stream

                    # Populate sensor_attributes directly from the map (ML predictions + fallbacks done by automation.py)
                    current_sensor_attrs = {}
                    current_sensor_attrs['device_reliability'] = float(full_profile.get('ml_predicted_reliability', FALLBACK_DEVICE_RELIABILITY_MAP))
                    current_sensor_attrs['predicted_noise_propensity'] = float(full_profile.get('ml_predicted_noise_propensity', FALLBACK_PREDICTED_NOISE_PROP_MAP))
                    current_sensor_attrs['data_consistency'] = float(full_profile.get('ml_initial_data_consistency', FALLBACK_DATA_CONSISTENCY_MAP))
                    # Add other static features if fuzzy logic needs them directly (though usually not)
                    # current_sensor_attrs['manufacturer'] = full_profile.get('manufacturer') 
                    sensor_attributes[sensor_ip] = current_sensor_attrs

                print(f"Initialized data trust scores: {sensor_data_trust_scores}")
                print(f"Initialized sensor attributes from map (ML-derived/fallback): {sensor_attributes}")
            return bool(sensor_static_and_ml_profiles_map)
        else:
            print(f"Warning: Node {node_id_str} not found in map file: {LIGHT_SENSOR_MAP_FILE}")
            sensor_static_and_ml_profiles_map = {}
            return False
    except Exception as e:
        print(f"Error loading/parsing augmented map {LIGHT_SENSOR_MAP_FILE}: {e}")
        sensor_static_and_ml_profiles_map = {}
        return False

def query_sensor_raw(sensor_ip, port):
    # ... (no changes) ...
    try:
        with socket.create_connection((sensor_ip, port), timeout=SENSOR_QUERY_TIMEOUT_SECONDS) as sock:
            sock.sendall(b"GET_TRAFFIC\n"); response_bytes = sock.recv(1024)
            response_str = response_bytes.decode('utf-8').strip()
            if response_str.startswith("TRAFFIC="):
                try: value = int(response_str.split('=', 1)[1]); return value
                except (IndexError, ValueError): print(f"Error: Bad traffic value from {sensor_ip}: {response_str}"); return None
            else: print(f"Error: Bad response format from {sensor_ip}: {response_str}"); return None
    except socket.timeout: print(f"Error: Timeout connecting to sensor {sensor_ip}:{port}"); return None
    except socket.error as e: print(f"Error: Socket error for sensor {sensor_ip}:{port} - {e}"); return None
    except Exception as e: print(f"Error: Unexpected error querying sensor {sensor_ip}:{port} - {e}"); return None


def get_local_traffic_readings():
    # ... (uses sensor_static_and_ml_profiles_map to iterate, no other changes) ...
    if not sensor_static_and_ml_profiles_map: print("Warning: No sensor map loaded."); return None
    local_traffic = {}; print("Querying local sensors via raw sockets...")
    for edge_str, sensor_profile in sensor_static_and_ml_profiles_map.items():
        sensor_ip = sensor_profile.get("ip")
        if not sensor_ip: continue
        traffic_value = query_sensor_raw(sensor_ip, SENSOR_LISTEN_PORT)
        local_traffic[edge_str] = traffic_value; print(f"  Sensor {sensor_ip} (Edge {edge_str}): Reported {traffic_value}")
    if all(v is None for v in local_traffic.values()) and sensor_static_and_ml_profiles_map: print("Warning: Failed to get readings from any local sensor this cycle."); return None
    else: return local_traffic


def get_ground_truth_traffic(node_id):
    # ... (no changes) ...
    if node_id is None: return None
    target_url = f"{CENTRAL_SERVER_URL}/approaching_traffic/{node_id}"
    try:
        response = requests.get(target_url, timeout=CENTRAL_QUERY_TIMEOUT_SECONDS); response.raise_for_status(); data = response.json()
        return data.get("traffic_per_approach", {})
    except Exception as e: print(f"Error querying central server for ground truth: {e}"); return None

def update_trust_scores(local_readings, ground_truth_data):
    # ... (logic remains very similar, but it fetches initial scores from sensor_attributes) ...
    global sensor_data_trust_scores, sensor_attributes, trust_simulation_instance
    if not sensor_static_and_ml_profiles_map: return
    if trust_simulation_instance is None:
        print("Warning: Fuzzy system not available, applying simple decay for data trust update.")
        with state_lock:
            for profile in sensor_static_and_ml_profiles_map.values(): # Iterate over profiles to get IPs
                sensor_ip = profile.get("ip")
                if sensor_ip:
                    current_score = sensor_data_trust_scores.get(sensor_ip, INITIAL_DATA_TRUST_SCORE)
                    new_score = max(TRUST_FLOOR, current_score - TRUST_DECAY_FAILURE / 2)
                    sensor_data_trust_scores[sensor_ip] = new_score
                    # print(f"    Sensor {sensor_ip}: FUZZY UNAVAILABLE. Simple decay. DataTrust -> {new_score:.1f}")
        return

    print("  Updating Data Trust Scores (using Fuzzy Logic with pre-calculated ML-derived attributes):")
    with state_lock:
        for edge_str, sensor_profile_in_map in sensor_static_and_ml_profiles_map.items():
            sensor_ip = sensor_profile_in_map.get("ip")
            if not sensor_ip: continue

            local_value = local_readings.get(edge_str) if local_readings else None
            truth_value = ground_truth_data.get(edge_str) if ground_truth_data else None
            
            current_data_trust = sensor_data_trust_scores.get(sensor_ip, INITIAL_DATA_TRUST_SCORE)
            # Get pre-calculated ML-derived attributes for this sensor
            attrs = sensor_attributes.get(sensor_ip, {}) # Should have been populated by load_sensor_map_and_attributes
            initial_ml_device_reliability = attrs.get('device_reliability', FALLBACK_DEVICE_RELIABILITY_MAP)
            initial_ml_data_consistency = attrs.get('data_consistency', FALLBACK_DATA_CONSISTENCY_MAP)
            initial_ml_predicted_noise_prop = attrs.get('predicted_noise_propensity', FALLBACK_PREDICTED_NOISE_PROP_MAP)

            new_data_trust = current_data_trust

            if local_value is None or local_value < 0:
                new_data_trust -= TRUST_DECAY_FAILURE
                print(f"    Sensor {sensor_ip} (Edge {edge_str}): Query/Report FAILED ({local_value}). DataTrust -> {new_data_trust:.1f}")
            elif not (0 <= local_value <= MAX_PLAUSIBLE_TRAFFIC):
                new_data_trust -= TRUST_DECAY_IMPLAUSIBLE
                print(f"    Sensor {sensor_ip} (Edge {edge_str}): IMPLAUSIBLE Reading ({local_value}). DataTrust -> {new_data_trust:.1f}")
            elif truth_value is not None and truth_value >= 0:
                dev = abs(local_value - truth_value)
                dev_clipped = min(dev, deviation.universe[-1])
                try:
                    trust_simulation_instance.input['deviation'] = dev_clipped
                    trust_simulation_instance.input['device_reliability_input'] = initial_ml_device_reliability
                    trust_simulation_instance.input['data_consistency_input'] = initial_ml_data_consistency
                    trust_simulation_instance.input['predicted_noise_prop_input'] = initial_ml_predicted_noise_prop
                    
                    trust_simulation_instance.compute()
                    fuzzy_trust_output = trust_simulation_instance.output['trust_update_output']
                    
                    new_data_trust = (1 - TRUST_UPDATE_ALPHA) * current_data_trust + TRUST_UPDATE_ALPHA * fuzzy_trust_output
                    print(f"    Sensor {sensor_ip} (Edge {edge_str}): Local={local_value}, Truth={truth_value}, Dev={dev:.1f}")
                    print(f"      FuzzyInputs (from map/ML): DevRel={initial_ml_device_reliability:.1f}, DataCons={initial_ml_data_consistency:.2f}, NoiseProp={initial_ml_predicted_noise_prop:.2f}")
                    print(f"      FuzzyOutput={fuzzy_trust_output:.1f} => NewDataTrust={new_data_trust:.1f}")
                except Exception as e:
                     print(f"    Error computing fuzzy logic for sensor {sensor_ip}: {e}")
                     new_data_trust -= TRUST_DECAY_FAILURE
                     print(f"    Sensor {sensor_ip} (Edge {edge_str}): FUZZY ERROR. DataTrust -> {new_data_trust:.1f}")
            else:
                new_data_trust -= (TRUST_DECAY_FAILURE / 2)
                print(f"    Sensor {sensor_ip} (Edge {edge_str}): Plausible Reading ({local_value}), but no ground truth. DataTrust -> {new_data_trust:.1f}")
            
            sensor_data_trust_scores[sensor_ip] = max(TRUST_FLOOR, min(TRUST_CEILING, new_data_trust))

def predict_priority_edge(traffic_data_source_map, is_local_data=False):
    # ... (logic to use sensor_static_and_ml_profiles_map for IP lookup remains similar)
    if not traffic_data_source_map: return None
    relevant_readings = {}
    if is_local_data:
        print("  Filtering local readings by data trust for prediction:")
        with state_lock: current_data_trust_map = sensor_data_trust_scores.copy()
        for edge_str, count in traffic_data_source_map.items():
            sensor_ip_for_edge = None
            if sensor_static_and_ml_profiles_map:
                profile_data = sensor_static_and_ml_profiles_map.get(edge_str)
                if profile_data: sensor_ip_for_edge = profile_data.get("ip")

            if sensor_ip_for_edge:
                trust_score = current_data_trust_map.get(sensor_ip_for_edge, 0)
                if count is not None and count >= 0:
                    if trust_score >= TRUST_THRESHOLD_FOR_PREDICTION:
                        relevant_readings[edge_str] = count
                        # print(f"    Edge {edge_str} (Sensor {sensor_ip_for_edge}): TRUSTED (DataTrust: {trust_score:.1f}, Reading: {count})")
                    # else:
                        # print(f"    Edge {edge_str} (Sensor {sensor_ip_for_edge}): UNTRUSTED (DataTrust: {trust_score:.1f}) - Reading {count} ignored.")
            elif count is not None:
                 print(f"    Warning: Edge {edge_str} (Reading: {count}) from local data not mapped to a known sensor IP. Ignoring.")
    else: 
        relevant_readings = traffic_data_source_map
    if not relevant_readings: return None
    valid_counts = {k: v for k, v in relevant_readings.items() if v is not None and v >=0}
    if not valid_counts: return None
    try: max_reading = max(valid_counts.values())
    except ValueError: return None
    priority_edges = [edge_str for edge_str, count in valid_counts.items() if count == max_reading]
    if not priority_edges: return None
    return sorted(priority_edges)[0]


# --- Main Loop ---
if __name__ == "__main__":
    print("--- Traffic Light Controller (Consuming Pre-Calculated ML Scores for Fuzzy Logic) ---")
    
    # NO ML MODEL LOADING HERE ANYMORE

    start_wait_time = time.time()
    node_id_loaded = False
    map_and_attributes_loaded = False # This means the rich map is loaded
    while time.time() - start_wait_time < CONFIG_WAIT_TIMEOUT_SECONDS:
        if not node_id_loaded:
            my_node_id = get_node_id()
            if my_node_id is not None: node_id_loaded = True; print(f"Node ID {my_node_id} loaded.")
        
        if node_id_loaded and not map_and_attributes_loaded:
            if load_sensor_map_and_attributes(my_node_id): # This now expects ML scores in the map
                map_and_attributes_loaded = True
                print("Sensor map (with static features & pre-calculated ML attributes) loaded.")
        
        if node_id_loaded and map_and_attributes_loaded: break
        time.sleep(CONFIG_CHECK_INTERVAL_SECONDS)

    # ... (rest of the main loop is largely the same, logging and evaluation) ...
    if not node_id_loaded: exit("FATAL: Could not determine Node ID.")
    if not map_and_attributes_loaded: print("Warning: Sensor map/attributes not fully loaded. Predictions may be impaired.")
    if trust_simulation_instance is None: print("CRITICAL WARNING: Fuzzy logic system failed. Trust updates will be basic.")
    if not sensor_static_and_ml_profiles_map: print("CRITICAL WARNING: No sensors mapped. Prediction impossible.")

    print(f"Controller active for Node ID: {my_node_id if my_node_id else 'UNKNOWN'}")

    while True:
        start_time = time.time(); current_time_str = time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n[{current_time_str}] Evaluating Node {my_node_id if my_node_id else 'UNKNOWN'}")

        local_readings = get_local_traffic_readings() if sensor_static_and_ml_profiles_map else {}
        ground_truth_data = get_ground_truth_traffic(my_node_id)
        update_trust_scores(local_readings, ground_truth_data)

        predicted_edge_str = predict_priority_edge(local_readings, is_local_data=True)
        actual_priority_edge_str = predict_priority_edge(ground_truth_data, is_local_data=False) if ground_truth_data else "Error (No GT)"

        print(f"  Local Sensor Readings Raw: {local_readings if local_readings else 'Query Failed/No Sensors'}")
        with state_lock:
            print(f"  Current Data Trust Scores: { {ip: f'{score:.1f}' for ip, score in sensor_data_trust_scores.items()} }")
            print(f"  Initial Sensor Attributes (from map via automation.py/ML): ")
            for ip, attrs in sensor_attributes.items(): # sensor_attributes now holds the initial values
                 print(f"    {ip}: DevRel={attrs.get('device_reliability', 'N/A'):.1f}, DataCons={attrs.get('data_consistency', 'N/A'):.2f}, NoiseProp={attrs.get('predicted_noise_propensity', 'N/A'):.2f}")

        print(f"  Prediction (based on trusted local): Priority -> {predicted_edge_str if predicted_edge_str else 'None'}")
        print(f"  Ground Truth Traffic:        {ground_truth_data if ground_truth_data else 'Query Failed/No Node ID'}")
        print(f"  Ground Truth Priority:       -> {actual_priority_edge_str if actual_priority_edge_str else 'None'}")

        eval_result = "INCONCLUSIVE"
        if actual_priority_edge_str == "Error (No GT)": eval_result = "TRUTH_ERROR"
        elif predicted_edge_str == actual_priority_edge_str: eval_result = "CORRECT"
        elif predicted_edge_str is None and actual_priority_edge_str is None: eval_result = "CORRECT (Both None)"
        elif predicted_edge_str is None and actual_priority_edge_str is not None: eval_result = "INCORRECT (Predicted None, GT had priority)"
        elif predicted_edge_str is not None and actual_priority_edge_str is None: eval_result = "INCORRECT (Predicted priority, GT had None)"
        else: eval_result = "INCORRECT"
        print(f"  EVALUATION:                  Prediction {eval_result}")

        end_time = time.time(); sleep_time = max(0, EVALUATION_INTERVAL_SECONDS - (end_time - start_time))
        time.sleep(sleep_time)
