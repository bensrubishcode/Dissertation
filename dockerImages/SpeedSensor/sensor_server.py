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
EDGE_INFO_FILE = "/etc/edge_info"
CONFIG_FILE = "/etc/sensor_config"
QUERY_INTERVAL_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 3.0
LISTEN_PORT = 5001
LISTEN_HOST = '0.0.0.0'
NOISE_MAGNITUDE = 2
CONFIG_WAIT_TIMEOUT_SECONDS = 30 # Max time to wait for config files
CONFIG_CHECK_INTERVAL_SECONDS = 0.5 # How often to check for files
# --- NEW: Socket Bind Retry Config ---
SOCKET_BIND_RETRY_DELAY = 1.0 # Seconds between bind attempts
SOCKET_BIND_MAX_ATTEMPTS = 10 # Max attempts to bind socket

# --- Global State ---
state_lock = threading.Lock()
my_cluster_id = None
my_edge_str = "unknown"
is_noisy = False
current_ground_truth_traffic = 0
last_query_success = False
last_query_time = 0

# --- Functions ---

def load_config():
    """Loads cluster ID, edge info, and noisy status. Returns True if essential ID loaded."""
    # (Implementation unchanged)
    global my_cluster_id, my_edge_str, is_noisy
    cluster_id_read = None; edge_read = None; noisy_read = False; config_ok = False
    if not os.path.exists(CLUSTER_ID_FILE): return False
    try:
        with open(CLUSTER_ID_FILE, 'r') as f: cluster_id_read = int(f.readline().strip())
        config_ok = True
    except Exception as e: print(f"Error reading {CLUSTER_ID_FILE}: {e}")
    if os.path.exists(EDGE_INFO_FILE):
        try:
            with open(EDGE_INFO_FILE, 'r') as f: line = f.readline().strip();
            if line.startswith("EDGE="): edge_read = line.split('=', 1)[1]
        except Exception as e: print(f"Error reading {EDGE_INFO_FILE}: {e}")
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f: line = f.readline().strip().lower();
            if line == "noisy=true": noisy_read = True
        except Exception as e: print(f"Error reading {CONFIG_FILE}: {e}")
    with state_lock:
        my_cluster_id = cluster_id_read; my_edge_str = edge_read if edge_read else "unknown"; is_noisy = noisy_read
        if config_ok: print(f"Sensor Config Loaded: Cluster={my_cluster_id}, Edge={my_edge_str}, Noisy={is_noisy}")
    return config_ok

def query_central_server_loop():
    """Periodically queries the central server for ground truth traffic."""
    # (Implementation unchanged)
    global current_ground_truth_traffic, last_query_success, last_query_time
    while True:
        current_id = None
        with state_lock: current_id = my_cluster_id
        if current_id is None: time.sleep(QUERY_INTERVAL_SECONDS); load_config(); continue
        target_url = f"{CENTRAL_SERVER_URL}/traffic/{current_id}"; success_flag = False; new_traffic_value = 0
        try:
            response = requests.get(target_url, timeout=REQUEST_TIMEOUT_SECONDS); response.raise_for_status(); data = response.json()
            traffic_val = data.get('current_traffic_count')
            if isinstance(traffic_val, int): new_traffic_value = traffic_val; success_flag = True
            else: print(f"Warning Sensor {current_id}: Invalid traffic count type: {traffic_val}")
        except Exception as e: print(f"Warning Sensor {current_id}: Central query failed: {e}")
        with state_lock: last_query_success = success_flag;
        if success_flag: current_ground_truth_traffic = new_traffic_value; last_query_time = time.time()
        time.sleep(QUERY_INTERVAL_SECONDS)

def handle_light_connection(conn, addr):
    """Handles an incoming connection from a traffic light."""
    # (Implementation unchanged)
    print(f"Sensor {my_cluster_id}: Connection accepted from {addr}")
    try:
        conn.settimeout(5.0); request = conn.recv(1024).decode('utf-8').strip()
        print(f"Sensor {my_cluster_id}: Received request: '{request}' from {addr}")
        if request == "GET_TRAFFIC":
            traffic_to_report = -1; current_noisy_flag = False
            with state_lock:
                if last_query_success: traffic_to_report = current_ground_truth_traffic; current_noisy_flag = is_noisy
                else: print(f"Sensor {my_cluster_id}: Last query failed, reporting error.")
            if traffic_to_report != -1 and current_noisy_flag:
                noise = random.randint(-NOISE_MAGNITUDE, NOISE_MAGNITUDE); reported_value = max(0, traffic_to_report + noise)
                print(f"DEBUG Sensor {my_cluster_id}: Noisy reporting {reported_value} (Truth: {traffic_to_report})"); traffic_to_report = reported_value
            response_str = f"TRAFFIC={traffic_to_report}\n"; conn.sendall(response_str.encode('utf-8'))
            print(f"Sensor {my_cluster_id}: Sent response: '{response_str.strip()}' to {addr}")
        else: print(f"Sensor {my_cluster_id}: Unknown request from {addr}: {request}"); conn.sendall(b"ERROR=UnknownRequest\n")
    except socket.timeout: print(f"Sensor {my_cluster_id}: Socket timeout with {addr}")
    except Exception as e: print(f"Sensor {my_cluster_id}: Error handling connection from {addr}: {e}")
    finally: conn.close()

# --- MODIFIED: Add retry logic for bind ---
def start_socket_server():
    """Starts the TCP socket server, retrying bind if necessary."""
    server_socket = None
    bind_success = False
    for attempt in range(SOCKET_BIND_MAX_ATTEMPTS):
        try:
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind((LISTEN_HOST, LISTEN_PORT))
            bind_success = True
            print(f"Sensor {my_cluster_id}: Socket bound successfully on attempt {attempt + 1}.")
            break # Exit loop on successful bind
        except OSError as e:
            print(f"Warning Sensor {my_cluster_id}: Socket bind attempt {attempt + 1}/{SOCKET_BIND_MAX_ATTEMPTS} failed: {e}")
            if server_socket: server_socket.close() # Close the failed socket
            if attempt < SOCKET_BIND_MAX_ATTEMPTS - 1:
                print(f"Retrying in {SOCKET_BIND_RETRY_DELAY} seconds...")
                time.sleep(SOCKET_BIND_RETRY_DELAY)
            else:
                print(f"FATAL Sensor {my_cluster_id}: Max bind attempts reached. Server cannot start.")
                return # Exit function if bind fails after max attempts

    if not bind_success or server_socket is None:
        return # Should not happen if loop logic is correct, but safety check

    # Proceed only if bind was successful
    try:
        server_socket.listen(5)
        print(f"Sensor {my_cluster_id}: Socket server listening on {LISTEN_HOST}:{LISTEN_PORT}")
        while True:
            try:
                conn, addr = server_socket.accept()
                handler_thread = threading.Thread(target=handle_light_connection, args=(conn, addr), daemon=True)
                handler_thread.start()
            except Exception as e: print(f"Sensor {my_cluster_id}: Error accepting connection: {e}")
    except KeyboardInterrupt: print(f"Sensor {my_cluster_id}: Socket server received interrupt.")
    finally: print(f"Sensor {my_cluster_id}: Closing socket server."); server_socket.close()
# --- END MODIFIED ---

# --- Main Execution ---
if __name__ == "__main__":
    print("--- Sensor Server Starting (Query Central, Serve Raw Socket) ---")

    # --- Wait loop for configuration ---
    print("Waiting for configuration files...")
    start_wait_time = time.time(); config_loaded = False
    while time.time() - start_wait_time < CONFIG_WAIT_TIMEOUT_SECONDS:
        if load_config(): config_loaded = True; break
        time.sleep(CONFIG_CHECK_INTERVAL_SECONDS)
    if not config_loaded:
        if not load_config(): # Try one last time
             print(f"FATAL: Configuration files not found after {CONFIG_WAIT_TIMEOUT_SECONDS} seconds. Exiting.")
             exit(1)
    # --- END Wait loop ---

    query_thread = threading.Thread(target=query_central_server_loop, daemon=True); query_thread.start()
    print("Central server query thread started.")
    start_socket_server() # Start socket server in main thread
    print("--- Sensor Server Shutting Down ---") # Likely only seen on manual stop

