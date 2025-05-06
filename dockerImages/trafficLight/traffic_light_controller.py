#!/usr/bin/env python3
# No leading spaces or tabs before this line or any other
import time
import requests
import json
import os
import random
import socket

# --- Configuration ---
CENTRAL_SERVER_URL = "http://192.168.254.200:5000" # For ground truth evaluation
NODE_ID_FILE = "/etc/node_id"
LIGHT_SENSOR_MAP_FILE = "/shared/light_sensor_map.json" # Map file: node_id -> { "edge_u-v": "sensor_ip", ... }
EVALUATION_INTERVAL_SECONDS = 5.0
SENSOR_QUERY_TIMEOUT_SECONDS = 1.5 # Timeout for connecting to EACH sensor
CENTRAL_QUERY_TIMEOUT_SECONDS = 3.0
SENSOR_LISTEN_PORT = 5001
CONFIG_WAIT_TIMEOUT_SECONDS = 35 # Max time to wait for config files
CONFIG_CHECK_INTERVAL_SECONDS = 0.5

# --- State Variables ---
my_node_id = None
sensor_map = {}
current_predicted_green_edge = None

# --- Functions ---

def get_node_id():
    """Reads the node ID from the specified file. Returns None if not found/error."""
    if not os.path.exists(NODE_ID_FILE):
        # print(f"Debug: {NODE_ID_FILE} not found yet.") # Optional debug
        return None
    try:
        with open(NODE_ID_FILE, 'r') as f:
            return int(f.readline().strip())
    except Exception as e:
        print(f"Error reading {NODE_ID_FILE}: {e}")
        return None

def load_sensor_map(node_id):
    """Loads the sensor map for this node. Returns True on success."""
    global sensor_map
    if node_id is None: return False
    if not os.path.exists(LIGHT_SENSOR_MAP_FILE):
        # print(f"Debug: {LIGHT_SENSOR_MAP_FILE} not found yet.") # Optional debug
        return False # File not ready
    try:
        with open(LIGHT_SENSOR_MAP_FILE, 'r') as f:
            full_map = json.load(f)
        node_id_str = str(node_id)
        if node_id_str in full_map:
            sensor_map = full_map[node_id_str]
            print(f"Loaded sensor map for Node {node_id}: {sensor_map}")
            return bool(sensor_map) # Success if map for this node is not empty
        else:
            # Node ID might be valid but just not have sensors mapped in this run
            print(f"Info: Node {node_id} not found in map file {LIGHT_SENSOR_MAP_FILE} or has no mapped sensors.")
            sensor_map = {}
            return True # Consider loading successful even if map is empty
    except Exception as e:
        print(f"Error loading/parsing {LIGHT_SENSOR_MAP_FILE}: {e}")
        sensor_map = {}
        return False

def query_sensor_raw(sensor_ip, port):
    """Connects to a sensor via raw socket and requests traffic data."""
    # (Implementation unchanged)
    try:
        # print(f"DEBUG Light {my_node_id}: Connecting to sensor {sensor_ip}:{port}") # Verbose
        with socket.create_connection((sensor_ip, port), timeout=SENSOR_QUERY_TIMEOUT_SECONDS) as sock:
            sock.sendall(b"GET_TRAFFIC\n")
            response_bytes = sock.recv(1024)
            response_str = response_bytes.decode('utf-8').strip()
            # print(f"DEBUG Light {my_node_id}: Received from {sensor_ip}: '{response_str}'") # Verbose
            if response_str.startswith("TRAFFIC="):
                try: value = int(response_str.split('=', 1)[1]); return value if value != -1 else None
                except (IndexError, ValueError): print(f"Error: Bad traffic value from {sensor_ip}: {response_str}"); return None
            else: print(f"Error: Bad response format from {sensor_ip}: {response_str}"); return None
    except socket.timeout: print(f"Error: Timeout connecting to sensor {sensor_ip}:{port}"); return None
    except socket.error as e: print(f"Error: Socket error for sensor {sensor_ip}:{port} - {e}"); return None
    except Exception as e: print(f"Error: Unexpected error querying sensor {sensor_ip}:{port} - {e}"); return None

def get_local_traffic_readings():
    """Queries all mapped local sensors using raw sockets."""
    # (Implementation unchanged)
    if not sensor_map: print("Warning: No sensor map loaded."); return None
    local_traffic = {}; print("Querying local sensors via raw sockets...")
    for edge_str, sensor_ip in sensor_map.items():
        traffic_value = query_sensor_raw(sensor_ip, SENSOR_LISTEN_PORT)
        local_traffic[edge_str] = traffic_value; print(f"  Sensor {sensor_ip} (Edge {edge_str}): Reported {traffic_value}")
    if all(v is None for v in local_traffic.values()) and sensor_map: print("Warning: Failed to get readings from any local sensor."); return None
    else: return local_traffic

def get_ground_truth_traffic(node_id):
    """Queries the central server for ground truth approaching traffic."""
    # (Implementation unchanged)
    if node_id is None: return None
    target_url = f"{CENTRAL_SERVER_URL}/approaching_traffic/{node_id}"
    try:
        response = requests.get(target_url, timeout=CENTRAL_QUERY_TIMEOUT_SECONDS); response.raise_for_status(); data = response.json()
        return data.get("traffic_per_approach", {})
    except Exception as e: print(f"Error querying central server: {e}"); return None

def predict_priority_edge(traffic_readings):
    """Predicts priority based on traffic readings."""
    # (Implementation unchanged)
    if not traffic_readings: return None
    valid_readings = { edge_str: count for edge_str, count in traffic_readings.items() if count is not None and count >= 0 }
    if not valid_readings: return None
    max_reading = max(valid_readings.values())
    priority_edges = [ edge_str for edge_str, count in valid_readings.items() if count == max_reading ]
    if not priority_edges: return None
    return sorted(priority_edges)[0]

# --- Main Loop ---
if __name__ == "__main__":
    print("--- Traffic Light Controller (Raw Socket Query) Starting ---")

    # --- Wait loop for configuration ---
    print("Waiting for configuration files...")
    start_wait_time = time.time(); node_id_loaded = False; map_loaded = False
    while time.time() - start_wait_time < CONFIG_WAIT_TIMEOUT_SECONDS:
        if not node_id_loaded:
            my_node_id = get_node_id() # Try reading node ID
            if my_node_id is not None:
                node_id_loaded = True
                print(f"Node ID {my_node_id} loaded.")
        # Try loading map only after node ID is known
        if node_id_loaded and not map_loaded:
             if load_sensor_map(my_node_id): # Try loading map
                  map_loaded = True
                  # Map loading is successful even if empty for this node
                  print("Sensor map check complete.")

        # Exit loop once essential node ID is loaded AND map loading attempt is done
        if node_id_loaded and map_loaded:
            break

        time.sleep(CONFIG_CHECK_INTERVAL_SECONDS)

    if not node_id_loaded: exit("FATAL: Could not determine Node ID. Exiting.")
    # Allow running even if map is empty/missing, just print warning
    if not map_loaded: print("Warning: Could not load sensor map. Will proceed without querying sensors.")
    # --- END Wait loop ---

    print(f"Controller active for intersection Node ID: {my_node_id}")

    while True:
        # (Rest of the main loop remains the same)
        start_time = time.time(); print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Evaluating Node {my_node_id}")
        local_readings = get_local_traffic_readings() if sensor_map else {}; predicted_edge_str = predict_priority_edge(local_readings)
        ground_truth_data = get_ground_truth_traffic(my_node_id); actual_priority_edge_str = predict_priority_edge(ground_truth_data) if ground_truth_data is not None else "Error"
        print(f"  Local Sensor Readings: {local_readings if local_readings is not None else 'Query Failed'}"); print(f"  Prediction (based on local): Priority -> {predicted_edge_str}")
        print(f"  Ground Truth Traffic:        {ground_truth_data if ground_truth_data is not None else 'Query Failed'}"); print(f"  Ground Truth Priority:       -> {actual_priority_edge_str}")
        eval_result = "INCONCLUSIVE";
        if actual_priority_edge_str == "Error": eval_result = "TRUTH_ERROR"
        elif predicted_edge_str == actual_priority_edge_str: eval_result = "CORRECT"
        elif predicted_edge_str is None and actual_priority_edge_str is None: eval_result = "CORRECT (Both None)"
        else: eval_result = "INCORRECT"
        print(f"  EVALUATION:                  Prediction {eval_result}"); current_assumed_green_edge = actual_priority_edge_str if actual_priority_edge_str != "Error" else None
        end_time = time.time(); sleep_time = max(0, EVALUATION_INTERVAL_SECONDS - (end_time - start_time)); time.sleep(sleep_time)

