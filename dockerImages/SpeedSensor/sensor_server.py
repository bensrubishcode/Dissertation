#!/usr/bin/env python3
# No leading spaces or tabs before this line or any other
import time
import requests
import json
import os
import threading
import socket
import random

# --- Configuration ---
CENTRAL_SERVER_URL = "http://192.168.254.200:5000"
CLUSTER_ID_FILE = "/etc/cluster_id"
EDGE_INFO_FILE = "/etc/edge_info" # For edge string
SENSOR_PROFILE_FILE = "/etc/sensor_profile" # Contains Manufacturer, SW Version, etc.
SENSOR_BEHAVIOR_CONFIG_FILE = "/etc/sensor_config" # Contains MAKE_NOISY flag

QUERY_INTERVAL_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 3.0
LISTEN_PORT = 5001
LISTEN_HOST = '0.0.0.0'
# NOISE_MAGNITUDE is now context-dependent if we re-introduce levels.
# For binary noise, it can still be used if MAKE_NOISY is true.
DEFAULT_NOISE_MAGNITUDE = 5 # Example magnitude if MAKE_NOISY is true

CONFIG_WAIT_TIMEOUT_SECONDS = 30
CONFIG_CHECK_INTERVAL_SECONDS = 0.5
SOCKET_BIND_RETRY_DELAY = 1.0
SOCKET_BIND_MAX_ATTEMPTS = 10

# --- Global State ---
state_lock = threading.Lock()
my_cluster_id = None
my_edge_str = "unknown"

# Sensor's static profile (loaded from file)
sensor_static_profile = {
    "manufacturer": "Unknown",
    "software_version": "Unknown",
    "is_signed": "false", # Store as string initially
    "software_age_years": "0.0",
    "device_age_years": "0.0"
}

# Behavior flags
is_configured_noisy_this_run = False # Based on MAKE_NOISY from sensor_config
# The 'is_noisy' flag used in handle_light_connection will be directly this value

current_ground_truth_traffic = 0
last_query_success = False
last_query_time = 0

# --- Functions ---

def parse_config_file_to_dict(filepath):
    """Parses a key=value file into a dictionary."""
    config_dict = {}
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        key, value = line.split('=', 1)
                        config_dict[key.strip().upper()] = value.strip()
        except Exception as e:
            print(f"Warning: Error reading or parsing config file {filepath}: {e}")
    return config_dict

def load_config():
    """Loads cluster ID, edge info, static profile, and MAKE_NOISY status."""
    global my_cluster_id, my_edge_str, sensor_static_profile, is_configured_noisy_this_run
    
    cluster_id_read = None
    edge_read = None
    essential_config_ok = False

    # Load Cluster ID (essential)
    if os.path.exists(CLUSTER_ID_FILE):
        try:
            with open(CLUSTER_ID_FILE, 'r') as f:
                cluster_id_read = int(f.readline().strip())
            essential_config_ok = True # Cluster ID is most essential for operation
        except Exception as e:
            print(f"Error reading {CLUSTER_ID_FILE}: {e}")
            essential_config_ok = False
    else:
        print(f"Error: {CLUSTER_ID_FILE} not found.")
        essential_config_ok = False

    # Load Edge Info
    if os.path.exists(EDGE_INFO_FILE):
        edge_config = parse_config_file_to_dict(EDGE_INFO_FILE)
        edge_read = edge_config.get("EDGE", "unknown")

    # Load Static Profile
    if os.path.exists(SENSOR_PROFILE_FILE):
        profile_data = parse_config_file_to_dict(SENSOR_PROFILE_FILE)
        sensor_static_profile["manufacturer"] = profile_data.get("MANUFACTURER", "Unknown")
        sensor_static_profile["software_version"] = profile_data.get("SOFTWARE_VERSION", "Unknown")
        sensor_static_profile["is_signed"] = profile_data.get("IS_SIGNED", "false").lower()
        sensor_static_profile["software_age_years"] = profile_data.get("SOFTWARE_AGE_YEARS", "0.0")
        sensor_static_profile["device_age_years"] = profile_data.get("DEVICE_AGE_YEARS", "0.0")

    # Load Behavior Configuration (MAKE_NOISY)
    make_noisy_behavior = False # Default to not noisy
    if os.path.exists(SENSOR_BEHAVIOR_CONFIG_FILE):
        behavior_config = parse_config_file_to_dict(SENSOR_BEHAVIOR_CONFIG_FILE)
        if behavior_config.get("MAKE_NOISY", "false").lower() == "true":
            make_noisy_behavior = True
            
    with state_lock:
        my_cluster_id = cluster_id_read
        my_edge_str = edge_read if edge_read else "unknown"
        is_configured_noisy_this_run = make_noisy_behavior
        
        if essential_config_ok:
            print(f"Sensor Config Loaded for Cluster ID: {my_cluster_id}")
            print(f"  Edge: {my_edge_str}")
            print(f"  Static Profile: {sensor_static_profile}")
            print(f"  Behavior Config - MAKE_NOISY: {is_configured_noisy_this_run}")
        else:
            print("Error: Essential configuration (Cluster ID) could not be loaded.")

    return essential_config_ok


def query_central_server_loop():
    """Periodically queries the central server for ground truth traffic."""
    global current_ground_truth_traffic, last_query_success, last_query_time
    while True:
        current_id_for_query = None
        with state_lock:
            current_id_for_query = my_cluster_id # Use a local var to minimize lock time
        
        if current_id_for_query is None:
            # print("Sensor Info: No Cluster ID yet, skipping central query.") # Can be verbose
            time.sleep(QUERY_INTERVAL_SECONDS)
            # load_config() # Optionally try to reload config if ID is missing, but load_config is called in main init loop
            continue

        target_url = f"{CENTRAL_SERVER_URL}/traffic/{current_id_for_query}"
        success_flag_this_cycle = False
        new_traffic_value_this_cycle = 0
        try:
            response = requests.get(target_url, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
            traffic_val = data.get('current_traffic_count')
            if isinstance(traffic_val, int):
                new_traffic_value_this_cycle = traffic_val
                success_flag_this_cycle = True
            else:
                print(f"Warning Sensor {current_id_for_query}: Invalid traffic count type from server: {type(traffic_val)}")
        except requests.exceptions.RequestException as e:
            print(f"Warning Sensor {current_id_for_query}: Central query failed: {e}")
        except json.JSONDecodeError:
            print(f"Warning Sensor {current_id_for_query}: Failed to decode JSON response from server.")
        
        with state_lock:
            last_query_success = success_flag_this_cycle
            if success_flag_this_cycle:
                current_ground_truth_traffic = new_traffic_value_this_cycle
                last_query_time = time.time()
            # else: current_ground_truth_traffic remains as is, last_query_success is False
        
        time.sleep(QUERY_INTERVAL_SECONDS)

def handle_light_connection(conn, addr):
    """Handles an incoming connection from a traffic light."""
    # Grab necessary state under lock once
    with state_lock:
        sensor_id_for_log = my_cluster_id if my_cluster_id is not None else "UNKNOWN_ID"
        query_was_successful = last_query_success
        traffic_from_central = current_ground_truth_traffic
        # THIS SENSOR'S BEHAVIOR IS DETERMINED BY is_configured_noisy_this_run
        act_noisy_this_time = is_configured_noisy_this_run

    print(f"Sensor {sensor_id_for_log}: Connection accepted from {addr}")
    try:
        conn.settimeout(5.0)
        request = conn.recv(1024).decode('utf-8').strip()
        print(f"Sensor {sensor_id_for_log}: Received request: '{request}' from {addr}")

        if request == "GET_TRAFFIC":
            traffic_to_report = -1 # Default to error

            if query_was_successful:
                traffic_to_report = traffic_from_central
                if act_noisy_this_time:
                    # Apply simple noise if configured to be noisy
                    noise = random.randint(-DEFAULT_NOISE_MAGNITUDE, DEFAULT_NOISE_MAGNITUDE)
                    reported_value_with_noise = max(0, traffic_to_report + noise)
                    print(f"DEBUG Sensor {sensor_id_for_log}: NOISY MODE. Truth: {traffic_to_report}, Reported: {reported_value_with_noise} (Noise: {noise})")
                    traffic_to_report = reported_value_with_noise
                # else:
                #     print(f"DEBUG Sensor {sensor_id_for_log}: Normal mode. Truth: {traffic_to_report}, Reported: {traffic_to_report}")

            else: # Last query to central server failed
                print(f"Sensor {sensor_id_for_log}: Last central query failed. Reporting error value.")
                traffic_to_report = -1 # Explicitly error

            response_str = f"TRAFFIC={traffic_to_report}\n"
            conn.sendall(response_str.encode('utf-8'))
            print(f"Sensor {sensor_id_for_log}: Sent response: '{response_str.strip()}' to {addr}")
        else:
            print(f"Sensor {sensor_id_for_log}: Unknown request from {addr}: {request}")
            conn.sendall(b"ERROR=UnknownRequest\n")

    except socket.timeout:
        print(f"Sensor {sensor_id_for_log}: Socket timeout with {addr}")
    except Exception as e:
        print(f"Sensor {sensor_id_for_log}: Error handling connection from {addr}: {e}")
    finally:
        conn.close()

def start_socket_server():
    """Starts the TCP socket server, retrying bind if necessary."""
    server_socket = None
    bind_success = False
    sensor_id_for_log_init = my_cluster_id if my_cluster_id is not None else "UNINITIALIZED"

    for attempt in range(SOCKET_BIND_MAX_ATTEMPTS):
        try:
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind((LISTEN_HOST, LISTEN_PORT))
            bind_success = True
            print(f"Sensor {sensor_id_for_log_init}: Socket bound successfully on attempt {attempt + 1}.")
            break
        except OSError as e:
            print(f"Warning Sensor {sensor_id_for_log_init}: Socket bind attempt {attempt + 1}/{SOCKET_BIND_MAX_ATTEMPTS} failed: {e}")
            if server_socket: server_socket.close()
            if attempt < SOCKET_BIND_MAX_ATTEMPTS - 1:
                print(f"Retrying in {SOCKET_BIND_RETRY_DELAY} seconds...")
                time.sleep(SOCKET_BIND_RETRY_DELAY)
            else:
                print(f"FATAL Sensor {sensor_id_for_log_init}: Max bind attempts reached. Server cannot start.")
                return

    if not bind_success or server_socket is None:
        return

    try:
        server_socket.listen(5)
        print(f"Sensor {sensor_id_for_log_init}: Socket server listening on {LISTEN_HOST}:{LISTEN_PORT}")
        while True:
            try:
                conn, addr = server_socket.accept()
                # Pass current cluster_id to thread for logging, in case it changes due to a bug (shouldn't)
                handler_thread = threading.Thread(target=handle_light_connection, args=(conn, addr), daemon=True)
                handler_thread.start()
            except Exception as e:
                # Update sensor_id_for_log in case it was loaded late
                current_sensor_id_log = my_cluster_id if my_cluster_id is not None else "ACCEPT_LOOP_UNINIT"
                print(f"Sensor {current_sensor_id_log}: Error accepting connection: {e}")
    except KeyboardInterrupt:
        current_sensor_id_log = my_cluster_id if my_cluster_id is not None else "KBI_UNINIT"
        print(f"Sensor {current_sensor_id_log}: Socket server received interrupt.")
    finally:
        current_sensor_id_log = my_cluster_id if my_cluster_id is not None else "FINALLY_UNINIT"
        print(f"Sensor {current_sensor_id_log}: Closing socket server.")
        if server_socket: server_socket.close()

# --- Main Execution ---
if __name__ == "__main__":
    print("--- Sensor Server Starting ---")

    # Initial configuration load attempt loop
    print("Waiting for configuration files...")
    start_wait_time = time.time()
    initial_config_loaded_successfully = False
    while time.time() - start_wait_time < CONFIG_WAIT_TIMEOUT_SECONDS:
        if load_config(): # load_config now returns True if essential CLUSTER_ID_FILE is found and read
            initial_config_loaded_successfully = True
            break
        time.sleep(CONFIG_CHECK_INTERVAL_SECONDS)

    if not initial_config_loaded_successfully:
        # Try one last time, perhaps files appeared very late
        if not load_config():
             print(f"FATAL: Essential configuration files (like {CLUSTER_ID_FILE}) not found or unreadable after {CONFIG_WAIT_TIMEOUT_SECONDS} seconds. Exiting.")
             exit(1)
        else:
            print("Configuration loaded successfully on final attempt.")


    # Start the thread that periodically queries the central server
    query_thread = threading.Thread(target=query_central_server_loop, daemon=True)
    query_thread.start()
    print("Central server query thread started.")

    # Start the socket server to listen for requests from traffic lights
    start_socket_server() # This will block until KeyboardInterrupt or error

    print("--- Sensor Server Shutting Down ---")
