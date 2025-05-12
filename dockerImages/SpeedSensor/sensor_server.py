#!/usr/bin/env python3
import time
import requests
import json
import os
import threading
import socket
import random

# --- Configuration ---
TRAFFIC_SERVER_IP_FILE = "/etc/traffic_server_ip"
CLUSTER_ID_FILE = "/etc/cluster_id"
EDGE_INFO_FILE = "/etc/edge_info"
SENSOR_PROFILE_FILE = "/etc/sensor_profile"
SENSOR_BEHAVIOR_CONFIG_FILE = "/etc/sensor_config" # For MAKE_NOISY flag

QUERY_INTERVAL_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 3.0
LISTEN_PORT = 5001
LISTEN_HOST = '0.0.0.0'
CENTRAL_SERVER_PORT = 5000 # Port the central server listens on
DEFAULT_NOISE_MAGNITUDE = 5
CONFIG_WAIT_TIMEOUT_SECONDS = 35
CONFIG_CHECK_INTERVAL_SECONDS = 0.5
SOCKET_BIND_RETRY_DELAY = 1.0
SOCKET_BIND_MAX_ATTEMPTS = 10
INITIAL_SERVER_QUERY_DELAY_SECONDS = 5

# NEW: Configuration for noisy sensor false priority reporting
NOISY_SENSOR_FALSE_PRIORITY_CHANCE = 0.05 # 5% chance for a noisy sensor to falsely report priority

# --- Global State ---
state_lock = threading.Lock()
my_cluster_id = None
my_edge_str = "unknown" # For logging, not directly used in logic beyond that
central_server_ip_address = None
central_server_url_global = None
sensor_static_profile = { # Loaded for completeness, not used in current logic beyond logging
    "manufacturer": "Unknown", "software_version": "Unknown",
    "is_signed": "false", "software_age_years": "0.0", "device_age_years": "0.0"
}
is_configured_noisy_this_run = False # From SENSOR_BEHAVIOR_CONFIG_FILE
current_ground_truth_traffic = 0 # From central server
current_priority_on_edge = False # NEW: From central server
last_query_success = False
last_query_time = 0 # Epoch time of the last query attempt

# --- Functions ---

def load_central_server_ip_from_file():
    global central_server_ip_address, central_server_url_global
    log_cid = "Pre-ID-Load"
    with state_lock: 
        if my_cluster_id is not None: log_cid = my_cluster_id
            
    if not os.path.exists(TRAFFIC_SERVER_IP_FILE):
        print(f"Sensor Info (Cluster {log_cid}): {TRAFFIC_SERVER_IP_FILE} not found.")
        return False
    try:
        with open(TRAFFIC_SERVER_IP_FILE, 'r') as f:
            ip = f.readline().strip()
            if ip:
                central_server_ip_address = ip
                central_server_url_global = f"http://{central_server_ip_address}:{CENTRAL_SERVER_PORT}"
                print(f"Sensor Info (Cluster {log_cid}): Central Server URL configured to {central_server_url_global}")
                return True
            else:
                print(f"Sensor Error (Cluster {log_cid}): {TRAFFIC_SERVER_IP_FILE} is empty.")
                return False
    except Exception as e:
        print(f"Sensor Error (Cluster {log_cid}): reading {TRAFFIC_SERVER_IP_FILE}: {e}")
        return False

def parse_config_file_to_dict(filepath):
    config_dict = {}
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    line = line.strip()
                    if '=' in line and not line.startswith('#'): # Ensure not a comment
                        key, value = line.split('=', 1)
                        config_dict[key.strip().upper()] = value.strip()
        except Exception as e:
            print(f"Warning: Error reading or parsing config file {filepath}: {e}")
    return config_dict

def load_sensor_specific_config():
    global my_cluster_id, my_edge_str, sensor_static_profile, is_configured_noisy_this_run
    cluster_id_read = None
    edge_read = None # From EDGE_INFO_FILE
    essential_config_ok = False

    if os.path.exists(CLUSTER_ID_FILE):
        try:
            with open(CLUSTER_ID_FILE, 'r') as f: cluster_id_read = int(f.readline().strip())
            essential_config_ok = True # Cluster ID is essential
        except Exception as e: print(f"Sensor Error: reading {CLUSTER_ID_FILE}: {e}"); essential_config_ok = False
    else: print(f"Sensor Error: {CLUSTER_ID_FILE} not found."); essential_config_ok = False

    if essential_config_ok: # Set global my_cluster_id as soon as it's read successfully
        with state_lock: my_cluster_id = cluster_id_read
    
    current_cid_log = "Unknown" 
    if my_cluster_id is not None: current_cid_log = my_cluster_id

    if os.path.exists(EDGE_INFO_FILE):
        edge_config = parse_config_file_to_dict(EDGE_INFO_FILE)
        edge_read = edge_config.get("EDGE", "unknown_edge_default")
    else: print(f"Sensor Info (Cluster {current_cid_log}): {EDGE_INFO_FILE} not found. Edge will be default.")

    if os.path.exists(SENSOR_PROFILE_FILE): # Load static profile, though not used in core logic currently
        profile_data = parse_config_file_to_dict(SENSOR_PROFILE_FILE)
        sensor_static_profile.update({
            "manufacturer": profile_data.get("MANUFACTURER", "Unknown"),
            "software_version": profile_data.get("SOFTWARE_VERSION", "Unknown"),
            "is_signed": profile_data.get("IS_SIGNED", "false").lower(),
            "software_age_years": profile_data.get("SOFTWARE_AGE_YEARS", "0.0"),
            "device_age_years": profile_data.get("DEVICE_AGE_YEARS", "0.0")
        })
    else: print(f"Sensor Info (Cluster {current_cid_log}): {SENSOR_PROFILE_FILE} not found. Profile will be defaults.")
        
    make_noisy_behavior = False # Default to not noisy
    if os.path.exists(SENSOR_BEHAVIOR_CONFIG_FILE):
        behavior_config = parse_config_file_to_dict(SENSOR_BEHAVIOR_CONFIG_FILE)
        if behavior_config.get("MAKE_NOISY", "false").lower() == "true": make_noisy_behavior = True
    else: print(f"Sensor Info (Cluster {current_cid_log}): {SENSOR_BEHAVIOR_CONFIG_FILE} not found. MAKE_NOISY defaults to false.")
            
    with state_lock: # Update other globals under lock
        my_edge_str = edge_read if edge_read else "unknown_edge_default"
        is_configured_noisy_this_run = make_noisy_behavior
        if essential_config_ok: 
            print(f"Sensor Config Loaded for Cluster ID: {my_cluster_id}")
            print(f"  Edge: {my_edge_str}, MAKE_NOISY (for traffic count): {is_configured_noisy_this_run}")
    return essential_config_ok


def query_central_server_loop():
    global current_ground_truth_traffic, current_priority_on_edge, last_query_success, last_query_time
    
    initial_delay_done = False
    while True: 
        current_id_for_query = None
        current_server_url = None
        with state_lock:
            current_id_for_query = my_cluster_id
            current_server_url = central_server_url_global

        if not (current_id_for_query is not None and current_server_url is not None):
            print(f"Sensor Info (Cluster {current_id_for_query if current_id_for_query else 'Unknown'}): Waiting for full config before starting query loop...")
            time.sleep(QUERY_INTERVAL_SECONDS)
            continue

        if not initial_delay_done:
            print(f"Sensor Info (Cluster {current_id_for_query}): Initial delay of {INITIAL_SERVER_QUERY_DELAY_SECONDS}s before first central server query...")
            time.sleep(INITIAL_SERVER_QUERY_DELAY_SECONDS)
            initial_delay_done = True

        while True: 
            with state_lock: 
                current_id_for_query_inner = my_cluster_id
                current_server_url_inner = central_server_url_global
            
            if not (current_id_for_query_inner is not None and current_server_url_inner is not None):
                print(f"Sensor Info: Configs became unavailable during query loop. Restarting wait.")
                initial_delay_done = False 
                break 

            target_url = f"{current_server_url_inner}/traffic/{current_id_for_query_inner}"
            success_flag_this_cycle = False
            new_traffic_value_this_cycle = 0
            new_priority_value_this_cycle = False # NEW: Default priority to false
            try:
                response = requests.get(target_url, timeout=REQUEST_TIMEOUT_SECONDS)
                response.raise_for_status()
                data = response.json()
                traffic_val = data.get('current_traffic_count')
                priority_val = data.get('priority_detected', False) # NEW: Get priority status

                if isinstance(traffic_val, int):
                    new_traffic_value_this_cycle = traffic_val
                    # Only consider priority valid if traffic is also valid
                    if isinstance(priority_val, bool):
                        new_priority_value_this_cycle = priority_val
                    else:
                        print(f"Warning Sensor (Cluster {current_id_for_query_inner}): Invalid priority_detected type from {target_url}: {type(priority_val)}")
                    success_flag_this_cycle = True
                else:
                    print(f"Warning Sensor (Cluster {current_id_for_query_inner}): Invalid traffic count type from {target_url}: {type(traffic_val)}")
            
            except requests.exceptions.Timeout:
                print(f"Warning Sensor (Cluster {current_id_for_query_inner}): Central query to {target_url} timed out (timeout={REQUEST_TIMEOUT_SECONDS}s).")
            except requests.exceptions.ConnectionError as e:
                print(f"Warning Sensor (Cluster {current_id_for_query_inner}): Central query to {target_url} connection failed. Error: {e}")
            except requests.exceptions.RequestException as e:
                print(f"Warning Sensor (Cluster {current_id_for_query_inner}): Central query to {target_url} request failed: {e}")
            except json.JSONDecodeError:
                print(f"Warning Sensor (Cluster {current_id_for_query_inner}): Failed to decode JSON response from {target_url}.")
            
            with state_lock:
                last_query_success = success_flag_this_cycle
                if success_flag_this_cycle:
                    current_ground_truth_traffic = new_traffic_value_this_cycle
                    current_priority_on_edge = new_priority_value_this_cycle # NEW: Update global priority
                else: # If query failed, reset to safe defaults
                    current_ground_truth_traffic = 0 
                    current_priority_on_edge = False
                last_query_time = time.time()
            
            time.sleep(QUERY_INTERVAL_SECONDS)


def handle_light_connection(conn, addr):
    # Safely get current state for this connection handler
    with state_lock:
        sensor_id_for_log = my_cluster_id if my_cluster_id is not None else "UNKNOWN_ID"
        query_was_successful = last_query_success
        traffic_from_central = current_ground_truth_traffic
        actual_priority_from_central = current_priority_on_edge # NEW: Get actual priority
        act_noisy_traffic_this_time = is_configured_noisy_this_run # For traffic count noise
    
    try:
        conn.settimeout(5.0) # Timeout for this specific connection
        request = conn.recv(1024).decode('utf-8').strip()

        if request == "GET_TRAFFIC":
            traffic_to_report = -1 
            priority_to_report = False # Default priority to report

            if query_was_successful:
                traffic_to_report = traffic_from_central
                if act_noisy_traffic_this_time: # Apply noise to traffic count if sensor is noisy
                    noise = random.randint(-DEFAULT_NOISE_MAGNITUDE, DEFAULT_NOISE_MAGNITUDE)
                    traffic_to_report = max(0, traffic_to_report + noise)
                
                # Determine priority to report
                priority_to_report = actual_priority_from_central # Start with actual priority
                
                # NEW: Noisy sensor false priority reporting logic
                if act_noisy_traffic_this_time and not actual_priority_from_central: # If sensor is noisy AND no actual priority
                    if random.random() < NOISY_SENSOR_FALSE_PRIORITY_CHANCE:
                        priority_to_report = True # Falsely report priority
                        # print(f"Sensor {sensor_id_for_log}: Noisy sensor Falsely reporting PRIORITY=true (Actual was false)") # Optional: for debugging
            else:
                # If query to central server failed, report error traffic and no priority
                print(f"Sensor {sensor_id_for_log}: Last central query failed. Reporting error value (-1) and PRIORITY=false to {addr}.")
                traffic_to_report = -1
                priority_to_report = False # Ensure priority is false on query failure

            # Format response string with both traffic and priority
            response_str = f"TRAFFIC={traffic_to_report};PRIORITY={str(priority_to_report).lower()}\n"
            conn.sendall(response_str.encode('utf-8'))
        else:
            print(f"Sensor {sensor_id_for_log}: Unknown request from {addr}: {request}")
            conn.sendall(b"ERROR=UnknownRequest\n") # Keep simple error for unknown

    except socket.timeout: print(f"Sensor {sensor_id_for_log}: Socket timeout with {addr}")
    except Exception as e: print(f"Sensor {sensor_id_for_log}: Error handling connection from {addr}: {e}")
    finally: conn.close()

def start_socket_server():
    server_socket = None
    bind_success = False
    
    log_cid_socket = "UNINITIALIZED_SOCKET" 
    with state_lock: 
        if my_cluster_id is not None: log_cid_socket = my_cluster_id

    for attempt in range(SOCKET_BIND_MAX_ATTEMPTS):
        try:
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind((LISTEN_HOST, LISTEN_PORT))
            bind_success = True
            print(f"Sensor (Cluster {log_cid_socket}): Socket bound successfully on attempt {attempt + 1}.")
            break
        except OSError as e:
            with state_lock: 
                if my_cluster_id is not None: log_cid_socket = my_cluster_id
            print(f"Warning Sensor (Cluster {log_cid_socket}): Socket bind attempt {attempt + 1}/{SOCKET_BIND_MAX_ATTEMPTS} failed: {e}")
            if server_socket: server_socket.close() 
            server_socket = None 
            if attempt < SOCKET_BIND_MAX_ATTEMPTS - 1:
                time.sleep(SOCKET_BIND_RETRY_DELAY)
            else:
                print(f"FATAL Sensor (Cluster {log_cid_socket}): Max bind attempts reached. Server cannot start.")
                return 
    
    if not bind_success or server_socket is None: 
        return # Exit if cannot bind

    try:
        server_socket.listen(5) # Listen for incoming connections
        with state_lock: 
                if my_cluster_id is not None: log_cid_socket = my_cluster_id
        print(f"Sensor (Cluster {log_cid_socket}): Socket server listening on {LISTEN_HOST}:{LISTEN_PORT}")
        
        while True: # Main accept loop
            try:
                conn, addr = server_socket.accept()
                # print(f"Sensor (Cluster {log_cid_socket}): Accepted connection from {addr}") # Optional: for debugging
                handler_thread = threading.Thread(target=handle_light_connection, args=(conn, addr), daemon=True)
                handler_thread.start()
            except Exception as e: # Catch errors in accept loop
                log_cid_accept_err = "ACCEPT_LOOP_UNINIT"
                with state_lock: 
                    if my_cluster_id is not None: log_cid_accept_err = my_cluster_id
                print(f"Sensor (Cluster {log_cid_accept_err}): Error accepting connection: {e}")
    except KeyboardInterrupt:
        log_cid_kbi_err = "KBI_UNINIT"
        with state_lock:
            if my_cluster_id is not None: log_cid_kbi_err = my_cluster_id
        print(f"Sensor (Cluster {log_cid_kbi_err}): Socket server received interrupt. Shutting down.")
    except Exception as e: 
        log_cid_listen_main_err = "LISTEN_ERR_UNINIT"
        with state_lock:
            if my_cluster_id is not None: log_cid_listen_main_err = my_cluster_id
        print(f"Sensor (Cluster {log_cid_listen_main_err}): Critical error in socket server listen loop: {e}")
    finally:
        log_cid_final_close = "FINALLY_UNINIT"
        with state_lock:
            if my_cluster_id is not None: log_cid_final_close = my_cluster_id
        print(f"Sensor (Cluster {log_cid_final_close}): Closing socket server.")
        if server_socket:
            server_socket.close()

if __name__ == "__main__":
    print("--- Sensor Server Starting ---")
    print("Waiting for configuration files...")
    start_wait_time = time.time()
    sensor_specific_config_loaded = False
    central_server_ip_config_loaded = False

    # Configuration loading loop
    while time.time() - start_wait_time < CONFIG_WAIT_TIMEOUT_SECONDS:
        if not sensor_specific_config_loaded:
            sensor_specific_config_loaded = load_sensor_specific_config()
        if not central_server_ip_config_loaded: # Try to load after sensor_specific for better logging with cluster_id
            central_server_ip_config_loaded = load_central_server_ip_from_file()
        
        if sensor_specific_config_loaded and central_server_ip_config_loaded:
            # Log with my_cluster_id which should be set if sensor_specific_config_loaded is true
            log_cid_main_loaded = "Unknown"
            with state_lock: # Safely access my_cluster_id
                if my_cluster_id is not None: log_cid_main_loaded = my_cluster_id
            print(f"Sensor Info (Cluster {log_cid_main_loaded}): All essential configurations loaded.")
            break
        time.sleep(CONFIG_CHECK_INTERVAL_SECONDS)

    # Check if configurations were successfully loaded
    log_cid_fatal_check = "Unknown" # For logging before my_cluster_id might be set
    with state_lock:
        if my_cluster_id is not None: log_cid_fatal_check = my_cluster_id

    if not sensor_specific_config_loaded:
        print(f"FATAL Sensor (Cluster {log_cid_fatal_check}): Essential sensor-specific config not loaded after {CONFIG_WAIT_TIMEOUT_SECONDS}s. Exiting.")
        exit(1)
    if not central_server_ip_config_loaded:
        print(f"FATAL Sensor (Cluster {log_cid_fatal_check}): Central Server IP config not loaded after {CONFIG_WAIT_TIMEOUT_SECONDS}s. Exiting.")
        exit(1)

    # Start the thread that queries the central server
    query_thread = threading.Thread(target=query_central_server_loop, daemon=True)
    query_thread.start()
    log_cid_thread_start = "Unknown"
    with state_lock:
        if my_cluster_id is not None: log_cid_thread_start = my_cluster_id
    print(f"Sensor Info (Cluster {log_cid_thread_start}): Central server query thread started.")
    
    # Start the socket server to listen for traffic light connections
    start_socket_server() # This is a blocking call and will run until interrupted or error

    log_cid_shutdown = "Unknown"
    with state_lock:
        if my_cluster_id is not None: log_cid_shutdown = my_cluster_id
    print(f"--- Sensor Server (Cluster {log_cid_shutdown}) Shutting Down ---")

