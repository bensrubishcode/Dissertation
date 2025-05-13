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
import signal # For graceful shutdown

# --- Configuration ---
TRAFFIC_SERVER_IP_FILE = "/etc/traffic_server_ip"
NODE_ID_FILE = "/etc/node_id"
LIGHT_SENSOR_MAP_FILE = "/shared/light_sensor_map.json"
EVALUATION_INTERVAL_SECONDS = 5.0
SENSOR_QUERY_TIMEOUT_SECONDS = 2.0
CENTRAL_QUERY_TIMEOUT_SECONDS = 3.0
SENSOR_LISTEN_PORT = 5001
CONFIG_WAIT_TIMEOUT_SECONDS = 35
CONFIG_CHECK_INTERVAL_SECONDS = 0.5
CENTRAL_SERVER_PORT = 5000
INITIAL_SERVER_QUERY_DELAY_SECONDS = 7

# NEW: Performance Reporting Config
RESULTS_DIR = "/shared/results" 
SIMULATION_END_SIGNAL_FILE = "/shared/SIMULATION_ENDING_PLEASE_REPORT"
SKIP_INITIAL_CYCLES_FOR_EVAL = 10 
MAX_EVAL_CYCLES_FOR_REPORT = 30  

# Trust & Attribute Configuration & Fuzzy Logic Setup
FALLBACK_ML_INITIAL_TRUST_SCORE = 75.0
TRUST_UPDATE_ALPHA = 0.3
TRUST_DECAY_FAILURE = 5.0
TRUST_DECAY_IMPLAUSIBLE = 3.0
TRUST_DECAY_FUZZY_ERROR = 4.0
TRUST_DECAY_PASSAGE_MISMATCH_SEVERE = 6.0
TRUST_DECAY_PASSAGE_MISMATCH_MODERATE = 3.0
TRUST_FLOOR = 10.0
TRUST_CEILING = 100.0
PRIORITY_SIGNAL_TRUST_THRESHOLD = 45.0
CONGESTION_TRUST_THRESHOLD = 25.0
MAX_PLAUSIBLE_TRAFFIC = 200
FALLBACK_DEVICE_RELIABILITY_MAP = 70.0
FALLBACK_PREDICTED_NOISE_PROP_MAP = 0.15
FALLBACK_DATA_CONSISTENCY_MAP = 0.80

# Fuzzy Logic (remains same as traffic_light_controller_no_gt_trust_v3_logging)
device_reliability_universe = np.arange(0, 101, 1); data_consistency_universe = np.arange(0, 1.01, 0.01)
predicted_noise_prop_universe = np.arange(0, 1.01, 0.01); peer_agreement_zscore_universe = np.arange(-3.5, 3.51, 0.1)
passage_deviation_universe = np.arange(0, 31, 1); DEFAULT_PASSAGE_DEVIATION_INPUT = 10.0
trust_update_output_universe = np.arange(0, 101, 1)
device_reliability_ant = ctrl.Antecedent(device_reliability_universe, 'device_reliability')
data_consistency_ant = ctrl.Antecedent(data_consistency_universe, 'data_consistency')
predicted_noise_prop_ant = ctrl.Antecedent(predicted_noise_prop_universe, 'predicted_noise_prop')
peer_agreement_zscore_ant = ctrl.Antecedent(peer_agreement_zscore_universe, 'peer_agreement_zscore')
passage_deviation_ant = ctrl.Antecedent(passage_deviation_universe, 'passage_deviation')
trust_update_cons = ctrl.Consequent(trust_update_output_universe, 'trust_update_output')
device_reliability_ant['low'] = fuzz.trimf(device_reliability_ant.universe, [0, 25, 50]); device_reliability_ant['medium'] = fuzz.trimf(device_reliability_ant.universe, [40, 60, 80]); device_reliability_ant['high'] = fuzz.trimf(device_reliability_ant.universe, [70, 85, 100])
data_consistency_ant['poor'] = fuzz.trimf(data_consistency_ant.universe, [0, 0.25, 0.5]); data_consistency_ant['fair'] = fuzz.trimf(data_consistency_ant.universe, [0.4, 0.6, 0.8]); data_consistency_ant['good'] = fuzz.trimf(data_consistency_ant.universe, [0.7, 0.85, 1.0])
predicted_noise_prop_ant['low'] = fuzz.trimf(predicted_noise_prop_ant.universe, [0, 0.15, 0.3]); predicted_noise_prop_ant['medium'] = fuzz.trimf(predicted_noise_prop_ant.universe, [0.2, 0.4, 0.6]); predicted_noise_prop_ant['high'] = fuzz.trimf(predicted_noise_prop_ant.universe, [0.5, 0.75, 1.0])
peer_agreement_zscore_ant['better_than_peers'] = fuzz.zmf(peer_agreement_zscore_ant.universe, 0.0, 0.5); peer_agreement_zscore_ant['similar_to_peers'] = fuzz.trimf(peer_agreement_zscore_ant.universe, [-0.5, 0.5, 1.5]); peer_agreement_zscore_ant['worse_than_peers'] = fuzz.smf(peer_agreement_zscore_ant.universe, 1.0, 2.0)
passage_deviation_ant['low'] = fuzz.trimf(passage_deviation_ant.universe, [0, 3, 7]); passage_deviation_ant['medium'] = fuzz.trimf(passage_deviation_ant.universe, [5, 10, 15]); passage_deviation_ant['high'] = fuzz.trimf(passage_deviation_ant.universe, [12, 20, 30])
trust_update_cons['very_low'] = fuzz.trimf(trust_update_cons.universe, [0, 10, 25]); trust_update_cons['low'] = fuzz.trimf(trust_update_cons.universe, [20, 35, 50]); trust_update_cons['medium'] = fuzz.trimf(trust_update_cons.universe, [40, 60, 80]); trust_update_cons['high'] = fuzz.trimf(trust_update_cons.universe, [70, 85, 100])
rule_passage_confirmed_good_reliability = ctrl.Rule(passage_deviation_ant['low'] & device_reliability_ant['high'], trust_update_cons['high']); rule_passage_unconfirmed_severe = ctrl.Rule(passage_deviation_ant['high'], trust_update_cons['very_low']); rule_good_peer_agreement_good_reliability = ctrl.Rule(peer_agreement_zscore_ant['similar_to_peers'] & device_reliability_ant['high'], trust_update_cons['high']); rule_better_peer_agreement = ctrl.Rule(peer_agreement_zscore_ant['better_than_peers'] & device_reliability_ant['medium'], trust_update_cons['high']); rule_bad_peer_agreement = ctrl.Rule(peer_agreement_zscore_ant['worse_than_peers'], trust_update_cons['very_low']); rule_low_static_reliability = ctrl.Rule(device_reliability_ant['low'], trust_update_cons['low']); rule_noisy_and_somewhat_worse_peers = ctrl.Rule(predicted_noise_prop_ant['high'] & peer_agreement_zscore_ant['worse_than_peers'], trust_update_cons['low']); rule_poor_static_consistency = ctrl.Rule(data_consistency_ant['poor'], trust_update_cons['low']); rule_passage_medium_dev = ctrl.Rule(passage_deviation_ant['medium'], trust_update_cons['medium']); rule_neutral_inputs_maintain_medium = ctrl.Rule(peer_agreement_zscore_ant['similar_to_peers'] & passage_deviation_ant['medium'] & device_reliability_ant['medium'], trust_update_cons['medium']); rule_neutral_inputs_low_reliability = ctrl.Rule(peer_agreement_zscore_ant['similar_to_peers'] & passage_deviation_ant['medium'] & device_reliability_ant['low'], trust_update_cons['low'])
trust_simulation_instance = None
try:
    rules_to_use = [rule_passage_confirmed_good_reliability, rule_passage_unconfirmed_severe, rule_good_peer_agreement_good_reliability, rule_better_peer_agreement, rule_bad_peer_agreement, rule_low_static_reliability, rule_noisy_and_somewhat_worse_peers, rule_poor_static_consistency, rule_passage_medium_dev, rule_neutral_inputs_maintain_medium, rule_neutral_inputs_low_reliability]; trust_ctrl_system = ctrl.ControlSystem(rules_to_use); trust_simulation_instance = ctrl.ControlSystemSimulation(trust_ctrl_system); print("TL Info: Fuzzy control system initialized.")
except Exception as e: print(f"TL FATAL: Failed to initialize fuzzy control system: {e}")

# --- Global State ---
my_node_id = None
central_server_ip_address = None
central_server_url_global = None
sensor_static_and_ml_profiles_map = {} 
state_lock = threading.Lock()
sensor_data_trust_scores = {} 
sensor_attributes = {} 
priority_edge_given_green_last_cycle = None
expected_traffic_on_priority_edge_last_cycle = 0
total_cycles_run = 0
evaluated_cycles_count = 0
correct_decision_cycles_count = 0
initial_trust_scores_loaded_for_report = {} 
keep_running = True 

# --- Functions ---
def get_node_id_from_file(): 
    if not os.path.exists(NODE_ID_FILE): print(f"TL Error: {NODE_ID_FILE} not found"); return None
    try:
        with open(NODE_ID_FILE, 'r') as f: return int(f.readline().strip())
    except Exception as e: print(f"TL Error reading {NODE_ID_FILE}: {e}"); return None

def load_central_server_ip_from_file(): 
    global central_server_ip_address, central_server_url_global
    log_nid = my_node_id if my_node_id is not None else "Pre-ID-Load"
    if not os.path.exists(TRAFFIC_SERVER_IP_FILE): print(f"TL Error (Node {log_nid}): {TRAFFIC_SERVER_IP_FILE} not found."); return False
    try:
        with open(TRAFFIC_SERVER_IP_FILE, 'r') as f: ip = f.readline().strip()
        if ip:
            central_server_ip_address = ip
            central_server_url_global = f"http://{central_server_ip_address}:{CENTRAL_SERVER_PORT}"
            print(f"TL Info (Node {log_nid}): Central Server URL configured: {central_server_url_global}")
            return True
        else: print(f"TL Error (Node {log_nid}): {TRAFFIC_SERVER_IP_FILE} is empty."); return False
    except Exception as e: print(f"TL Error (Node {log_nid}): reading {TRAFFIC_SERVER_IP_FILE}: {e}"); return False

def load_sensor_map_and_attributes(node_id_val): 
    global sensor_static_and_ml_profiles_map, sensor_data_trust_scores, sensor_attributes, initial_trust_scores_loaded_for_report
    if node_id_val is None: return False
    if not os.path.exists(LIGHT_SENSOR_MAP_FILE): print(f"TL Error (Node {node_id_val}): {LIGHT_SENSOR_MAP_FILE} not found."); return False
    try:
        with open(LIGHT_SENSOR_MAP_FILE, 'r') as f: full_map_data = json.load(f)
        node_id_str = str(node_id_val)
        if node_id_str in full_map_data:
            current_node_map_data = full_map_data[node_id_str]
            with state_lock:
                sensor_static_and_ml_profiles_map = current_node_map_data
                sensor_data_trust_scores.clear(); sensor_attributes.clear(); initial_trust_scores_loaded_for_report.clear()
                for edge_str, sensor_profiles_on_edge in current_node_map_data.items():
                    for sensor_profile in sensor_profiles_on_edge:
                        sensor_ip = sensor_profile.get("ip")
                        if not sensor_ip: print(f"TL Warning (Node {node_id_val}): Sensor IP missing on edge {edge_str}."); continue
                        ml_initial_trust = sensor_profile.get('ml_initial_trust_score', FALLBACK_ML_INITIAL_TRUST_SCORE)
                        sensor_data_trust_scores[sensor_ip] = float(ml_initial_trust)
                        initial_trust_scores_loaded_for_report[sensor_ip] = float(ml_initial_trust) 
                        sensor_attributes[sensor_ip] = {
                            'ml_initial_trust_score': float(ml_initial_trust),
                            'device_reliability': float(sensor_profile.get('ml_predicted_reliability', FALLBACK_DEVICE_RELIABILITY_MAP)),
                            'predicted_noise_propensity': float(sensor_profile.get('ml_predicted_noise_propensity', FALLBACK_PREDICTED_NOISE_PROP_MAP)),
                            'data_consistency': float(sensor_profile.get('ml_initial_data_consistency', FALLBACK_DATA_CONSISTENCY_MAP)),
                            'edge_it_monitors': edge_str
                        }
            print(f"TL Info (Node {node_id_val}): Sensor map and attributes loaded. Initial trust scores set from ML predictions (or fallback).")
            return True
        else:
            print(f"TL Warning (Node {node_id_val}): ID {node_id_str} not in {LIGHT_SENSOR_MAP_FILE}. No sensors configured.");
            sensor_static_and_ml_profiles_map = {}; return False
    except Exception as e:
        print(f"TL Error (Node {node_id_val}): loading {LIGHT_SENSOR_MAP_FILE}: {e}");
        sensor_static_and_ml_profiles_map = {}; return False

def query_sensor_raw(sensor_ip, port): 
    try:
        with socket.create_connection((sensor_ip, port), timeout=SENSOR_QUERY_TIMEOUT_SECONDS) as sock:
            sock.sendall(b"GET_TRAFFIC\n"); response_bytes = sock.recv(1024)
            response_str = response_bytes.decode('utf-8').strip(); parts = response_str.split(';')
            traffic_val = None; priority_val = False
            for part in parts:
                part = part.strip()
                if part.startswith("TRAFFIC="):
                    try: traffic_val = int(part.split('=', 1)[1])
                    except: pass
                elif part.startswith("PRIORITY="):
                    try: priority_val = part.split('=', 1)[1].lower() == 'true'
                    except: pass
            if traffic_val is not None: return {"traffic": traffic_val, "priority": priority_val}
            else: print(f"TL Error: Malformed/missing TRAFFIC from sensor {sensor_ip}. Resp: '{response_str}'"); return None
    except socket.timeout: print(f"TL Warning: Timeout sensor {sensor_ip}:{port}"); return None
    except socket.error as e: print(f"TL Warning: Socket error sensor {sensor_ip}:{port} - {e}"); return None
    except Exception as e: print(f"TL Warning: Unexpected error sensor {sensor_ip}:{port} - {e}"); return None

def get_local_sensor_readings(): 
    if not sensor_attributes: return {}
    sensor_ip_to_reading_map = {}; sensors_to_query = []
    with state_lock: sensors_to_query = list(sensor_attributes.keys())
    if not sensors_to_query: return {}
    for sensor_ip in sensors_to_query:
        sensor_ip_to_reading_map[sensor_ip] = query_sensor_raw(sensor_ip, SENSOR_LISTEN_PORT)
    if all(v is None for v in sensor_ip_to_reading_map.values()) and sensor_ip_to_reading_map:
        print(f"TL Warning (Node {my_node_id}): Failed to get valid readings from ANY local sensor this cycle.")
    return sensor_ip_to_reading_map

def get_ground_truth_traffic_per_edge(node_id_val): 
    if node_id_val is None or central_server_url_global is None: return None
    target_url = f"{central_server_url_global}/approaching_traffic/{node_id_val}"
    try:
        response = requests.get(target_url, timeout=CENTRAL_QUERY_TIMEOUT_SECONDS); response.raise_for_status()
        data = response.json(); return data.get("traffic_per_approach", {})
    except Exception: return None

def get_confirmed_node_passage(node_id_val): 
    if node_id_val is None or central_server_url_global is None: return None
    target_url = f"{central_server_url_global}/passed_through_node_count/{node_id_val}"
    try:
        response = requests.get(target_url, timeout=CENTRAL_QUERY_TIMEOUT_SECONDS); response.raise_for_status()
        data = response.json(); return data.get("cars_passed_through_last_step")
    except Exception: return None

def update_trust_scores(local_sensor_readings_map, confirmed_passage_at_node): 
    global sensor_data_trust_scores, sensor_attributes, trust_simulation_instance, priority_edge_given_green_last_cycle, expected_traffic_on_priority_edge_last_cycle
    if not sensor_attributes: return
    if trust_simulation_instance is None: print(f"TL Warning (Node {my_node_id}): Fuzzy system not available."); return
    trusted_peer_readings_traffic = []; sensor_traffic_reports = {}
    with state_lock: current_trust_map_for_peer_calc = sensor_data_trust_scores.copy()
    for sensor_ip, reading_dict in local_sensor_readings_map.items():
        if reading_dict and reading_dict.get("traffic") is not None and 0 <= reading_dict["traffic"] <= MAX_PLAUSIBLE_TRAFFIC:
            sensor_traffic_reports[sensor_ip] = reading_dict["traffic"]
            if current_trust_map_for_peer_calc.get(sensor_ip, 0) >= CONGESTION_TRUST_THRESHOLD: trusted_peer_readings_traffic.append(reading_dict["traffic"])
    mean_trusted_peer_traffic = np.mean(trusted_peer_readings_traffic) if trusted_peer_readings_traffic else None
    std_trusted_peer_traffic = np.std(trusted_peer_readings_traffic) if len(trusted_peer_readings_traffic) > 1 else 0.0
    with state_lock:
        for sensor_ip, attrs in sensor_attributes.items():
            current_data_trust = sensor_data_trust_scores.get(sensor_ip, FALLBACK_ML_INITIAL_TRUST_SCORE); new_data_trust = current_data_trust
            static_device_reliability = attrs.get('device_reliability', FALLBACK_DEVICE_RELIABILITY_MAP); static_data_consistency = attrs.get('data_consistency', FALLBACK_DATA_CONSISTENCY_MAP); static_predicted_noise_prop = attrs.get('predicted_noise_propensity', FALLBACK_PREDICTED_NOISE_PROP_MAP)
            peer_agreement_zscore_val = 0.0; sensor_reported_traffic_this_cycle = sensor_traffic_reports.get(sensor_ip)
            if sensor_reported_traffic_this_cycle is not None and mean_trusted_peer_traffic is not None:
                deviation_from_peer_mean = abs(sensor_reported_traffic_this_cycle - mean_trusted_peer_traffic)
                if std_trusted_peer_traffic > 0.001: peer_agreement_zscore_val = deviation_from_peer_mean / std_trusted_peer_traffic
                elif deviation_from_peer_mean > 0: peer_agreement_zscore_val = 3.0 
                peer_agreement_zscore_val = np.clip(peer_agreement_zscore_val, peer_agreement_zscore_ant.universe[0], peer_agreement_zscore_ant.universe[-1])
            current_passage_deviation_val_for_fuzzy = None; monitored_edge_for_this_sensor = attrs.get('edge_it_monitors')
            if confirmed_passage_at_node is not None and priority_edge_given_green_last_cycle == monitored_edge_for_this_sensor and expected_traffic_on_priority_edge_last_cycle is not None:
                passage_dev = abs(confirmed_passage_at_node - expected_traffic_on_priority_edge_last_cycle); current_passage_deviation_val_for_fuzzy = min(passage_dev, passage_deviation_ant.universe[-1])
            input_val_device_reliability = static_device_reliability; input_val_data_consistency = static_data_consistency; input_val_predicted_noise_prop = static_predicted_noise_prop; input_val_peer_agreement_zscore = peer_agreement_zscore_val; input_val_passage_deviation = current_passage_deviation_val_for_fuzzy if current_passage_deviation_val_for_fuzzy is not None else DEFAULT_PASSAGE_DEVIATION_INPUT
            # print(f"    TL DEBUG Fuzzy Inputs for {sensor_ip}: dev_rel={input_val_device_reliability:.2f}, data_cons={input_val_data_consistency:.2f}, noise_prop={input_val_predicted_noise_prop:.2f}, peer_zscore={input_val_peer_agreement_zscore:.2f}, pass_dev={input_val_passage_deviation:.2f}")
            fuzzy_trust_output = None
            if sensor_reported_traffic_this_cycle is not None or current_passage_deviation_val_for_fuzzy is not None:
                try:
                    trust_simulation_instance.input['device_reliability'] = input_val_device_reliability; trust_simulation_instance.input['data_consistency'] = input_val_data_consistency; trust_simulation_instance.input['predicted_noise_prop'] = input_val_predicted_noise_prop; trust_simulation_instance.input['peer_agreement_zscore'] = input_val_peer_agreement_zscore; trust_simulation_instance.input['passage_deviation'] = input_val_passage_deviation
                    trust_simulation_instance.compute()
                    if 'trust_update_output' in trust_simulation_instance.output and trust_simulation_instance.output['trust_update_output'] is not None:
                        fuzzy_trust_output = trust_simulation_instance.output['trust_update_output']
                        # print(f"    TL DEBUG Fuzzy Output for {sensor_ip}: {fuzzy_trust_output:.2f}")
                        new_data_trust = (1 - TRUST_UPDATE_ALPHA) * current_data_trust + TRUST_UPDATE_ALPHA * fuzzy_trust_output
                    else: print(f"    TL WARNING FUZZY for {sensor_ip}: No rules fired or output is None. Output dict: {trust_simulation_instance.output}. Penalizing."); new_data_trust -= TRUST_DECAY_FUZZY_ERROR 
                except Exception as e: print(f"    TL ERROR FUZZY compute for {sensor_ip}: {e}. Penalizing."); new_data_trust -= TRUST_DECAY_FUZZY_ERROR
            current_reading_dict = local_sensor_readings_map.get(sensor_ip)
            if current_reading_dict is None: new_data_trust -= TRUST_DECAY_FAILURE
            elif current_reading_dict.get("traffic") is None: new_data_trust -= TRUST_DECAY_FAILURE * 0.5 
            elif not (0 <= current_reading_dict.get("traffic", -1) <= MAX_PLAUSIBLE_TRAFFIC): new_data_trust -= TRUST_DECAY_IMPLAUSIBLE
            sensor_data_trust_scores[sensor_ip] = max(TRUST_FLOOR, min(TRUST_CEILING, new_data_trust))

def predict_priority_edge(local_sensor_readings_map): 
    if not local_sensor_readings_map: return None
    trusted_priority_alerts = []
    with state_lock: current_trust_map = sensor_data_trust_scores.copy(); current_attributes_map = sensor_attributes.copy()
    for sensor_ip, reading_dict in local_sensor_readings_map.items():
        if reading_dict is None: continue
        reported_priority = reading_dict.get("priority", False); reported_traffic = reading_dict.get("traffic", 0) 
        if reported_priority:
            trust_score = current_trust_map.get(sensor_ip, 0)
            if trust_score >= PRIORITY_SIGNAL_TRUST_THRESHOLD: 
                attrs = current_attributes_map.get(sensor_ip)
                if attrs and 'edge_it_monitors' in attrs:
                    edge_str = attrs['edge_it_monitors']; traffic_for_sort = reported_traffic if reported_traffic is not None else -1
                    trusted_priority_alerts.append({'trust': trust_score, 'traffic': traffic_for_sort, 'edge': edge_str, 'sensor_ip': sensor_ip})
    if trusted_priority_alerts:
        trusted_priority_alerts.sort(key=lambda x: (x['trust'], x['traffic']), reverse=True)
        return trusted_priority_alerts[0]['edge']
    edge_to_trusted_sum = {}; edge_to_trusted_sensor_count = {}
    for sensor_ip, reading_dict in local_sensor_readings_map.items():
        if reading_dict is None or reading_dict.get("traffic") is None or reading_dict.get("traffic", -1) < 0: continue
        traffic_count = reading_dict["traffic"]; trust_score = current_trust_map.get(sensor_ip, 0)
        if trust_score >= CONGESTION_TRUST_THRESHOLD: 
            attrs = current_attributes_map.get(sensor_ip)
            if attrs and 'edge_it_monitors' in attrs:
                edge_str = attrs['edge_it_monitors']
                edge_to_trusted_sum[edge_str] = edge_to_trusted_sum.get(edge_str, 0) + traffic_count
                edge_to_trusted_sensor_count[edge_str] = edge_to_trusted_sensor_count.get(edge_str, 0) + 1
    if not edge_to_trusted_sum: return None
    edge_to_avg_trusted_reading = { edge: total_sum / edge_to_trusted_sensor_count[edge] for edge, total_sum in edge_to_trusted_sum.items() if edge_to_trusted_sensor_count.get(edge, 0) > 0 }
    if not edge_to_avg_trusted_reading: return None
    try:
        max_avg_reading = -1.0 
        for avg_val in edge_to_avg_trusted_reading.values():
            if avg_val > max_avg_reading: max_avg_reading = avg_val
        if max_avg_reading < 0 : return None 
        candidate_priority_edges = [edge for edge, avg_val in edge_to_avg_trusted_reading.items() if abs(avg_val - max_avg_reading) < 1e-9] 
        if not candidate_priority_edges: return None
        return sorted(candidate_priority_edges)[0]
    except ValueError: return None

def write_performance_report():
    global my_node_id, evaluated_cycles_count, correct_decision_cycles_count, initial_trust_scores_loaded_for_report
    if my_node_id is None: print("TL Report Error: Node ID is None, cannot write report."); return
    success_ratio = 0.0
    if evaluated_cycles_count > 0: success_ratio = correct_decision_cycles_count / evaluated_cycles_count
    report_data = {
        "node_id": my_node_id, "total_evaluated_cycles": evaluated_cycles_count,
        "correct_decision_cycles": correct_decision_cycles_count, "success_ratio": round(success_ratio, 4),
        "initial_trust_scores_used": initial_trust_scores_loaded_for_report
    }
    if not os.path.exists(RESULTS_DIR):
        try: os.makedirs(RESULTS_DIR); print(f"TL Info (Node {my_node_id}): Created results directory {RESULTS_DIR}")
        except OSError as e: print(f"TL Report Error (Node {my_node_id}): Could not create results directory {RESULTS_DIR}: {e}"); return
    report_filepath = os.path.join(RESULTS_DIR, f"tl_{my_node_id}_results.json")
    try:
        with open(report_filepath, 'w') as f: json.dump(report_data, f, indent=4)
        print(f"TL Info (Node {my_node_id}): Performance report written to {report_filepath}")
    except IOError as e: print(f"TL Report Error (Node {my_node_id}): Could not write report to {report_filepath}: {e}")

def signal_handler(signum, frame):
    global keep_running
    print(f"TL Info (Node {my_node_id}): Received signal {signum}, preparing to write report and shut down...")
    keep_running = False

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("--- Traffic Light Controller Starting ---")
    start_wait_time = time.time()
    node_id_loaded = False; central_server_ip_loaded = False; map_and_attributes_loaded = False
    print(f"TL (PID {os.getpid()}): Waiting for configuration files...")

    while time.time() - start_wait_time < CONFIG_WAIT_TIMEOUT_SECONDS:
        if not node_id_loaded:
            my_node_id = get_node_id_from_file();
            if my_node_id is not None: node_id_loaded = True
        if node_id_loaded and not central_server_ip_loaded:
            central_server_ip_loaded = load_central_server_ip_from_file()
        if node_id_loaded and central_server_ip_loaded and not map_and_attributes_loaded:
            map_and_attributes_loaded = load_sensor_map_and_attributes(my_node_id) 
        if node_id_loaded and central_server_ip_loaded and map_and_attributes_loaded:
            print(f"TL Info (Node {my_node_id}): All essential configurations loaded.")
            break
        time.sleep(CONFIG_CHECK_INTERVAL_SECONDS)

    current_log_node_id = my_node_id if my_node_id is not None else "UnknownNode"
    if not node_id_loaded: exit(f"TL FATAL (PID {os.getpid()}): Could not determine Node ID. Exiting.")
    if not central_server_ip_loaded: exit(f"TL FATAL (Node {current_log_node_id}): Could not determine Central Server IP. Exiting.")
    if not map_and_attributes_loaded: print(f"TL Warning (Node {current_log_node_id}): Sensor map/attributes not fully loaded or no sensors for this node.")
    if trust_simulation_instance is None: print(f"TL CRITICAL WARNING (Node {current_log_node_id}): Fuzzy logic system failed to initialize.")
    if not sensor_attributes and map_and_attributes_loaded : print(f"TL CRITICAL (Node {current_log_node_id}): No sensors mapped via attributes. Prediction impossible.")
    
    if node_id_loaded and central_server_ip_loaded:
        print(f"TL Info (Node {current_log_node_id}): Initial delay of {INITIAL_SERVER_QUERY_DELAY_SECONDS}s before starting evaluation loop...")
        time.sleep(INITIAL_SERVER_QUERY_DELAY_SECONDS)

    print(f"TL Controller active for Node ID: {current_log_node_id}. Central Server: {central_server_url_global if central_server_url_global else 'NOT SET'}")

    try: 
        while keep_running: 
            total_cycles_run += 1
            loop_start_time = time.time(); current_time_str = time.strftime('%Y-%m-%d %H:%M:%S')
            eval_node_id_log = my_node_id if my_node_id is not None else "UNKNOWN_IN_LOOP"
            print(f"\n[{current_time_str}] TL Node {eval_node_id_log}: Evaluating Cycle {total_cycles_run}...")

            actual_cars_passed_node_last_step = None
            if priority_edge_given_green_last_cycle and my_node_id is not None and expected_traffic_on_priority_edge_last_cycle is not None:
                actual_cars_passed_node_last_step = get_confirmed_node_passage(my_node_id)
            
            current_local_sensor_readings = get_local_sensor_readings()
            update_trust_scores(current_local_sensor_readings, actual_cars_passed_node_last_step)
            
            priority_edge_given_green_last_cycle = None 
            expected_traffic_on_priority_edge_last_cycle = 0

            predicted_edge_to_prioritize = predict_priority_edge(current_local_sensor_readings)

            if predicted_edge_to_prioritize:
                priority_edge_given_green_last_cycle = predicted_edge_to_prioritize
                temp_expected_traffic = 0
                # *** CORRECTED SYNTAX: 'with' statement on a new line ***
                with state_lock: 
                    current_attributes_map_for_exp = sensor_attributes.copy()
                    current_trust_map_for_exp = sensor_data_trust_scores.copy()
                # *** END CORRECTION ***
                for s_ip, attrs_exp in current_attributes_map_for_exp.items():
                    if attrs_exp.get('edge_it_monitors') == predicted_edge_to_prioritize:
                        reading_dict = current_local_sensor_readings.get(s_ip); 
                        traffic_value = reading_dict.get("traffic") if reading_dict else None
                        if traffic_value is not None and \
                           current_trust_map_for_exp.get(s_ip, 0) >= CONGESTION_TRUST_THRESHOLD: 
                           temp_expected_traffic += traffic_value
                expected_traffic_on_priority_edge_last_cycle = temp_expected_traffic
            
            eval_result_str = "INIT_OR_ERROR" 
            # is_correct_decision_this_cycle = False # Removed as not used

            if total_cycles_run > SKIP_INITIAL_CYCLES_FOR_EVAL and \
               evaluated_cycles_count < MAX_EVAL_CYCLES_FOR_REPORT :
                ground_truth_data_per_approach_for_eval = get_ground_truth_traffic_per_edge(my_node_id)
                actual_priority_edge_gt_for_eval = None
                if ground_truth_data_per_approach_for_eval:
                    gt_priority_candidates = []; 
                    for edge_name, data in ground_truth_data_per_approach_for_eval.items():
                        if data.get("priority_detected", False): gt_priority_candidates.append( (data.get("traffic",0), edge_name) )
                    if gt_priority_candidates: 
                        gt_priority_candidates.sort(key=lambda x: x[0], reverse=True)
                        actual_priority_edge_gt_for_eval = gt_priority_candidates[0][1]
                    else: 
                        max_gt_val = -1.0; gt_traffic_candidates_edges = [] 
                        for edge_name, data_val in ground_truth_data_per_approach_for_eval.items(): 
                            traffic = data_val.get("traffic")
                            if traffic is not None and traffic > max_gt_val: max_gt_val = traffic
                        if max_gt_val >= 0: 
                            gt_traffic_candidates_edges = [
                                e_name for e_name, d_val in ground_truth_data_per_approach_for_eval.items() 
                                if d_val.get("traffic") is not None and abs(d_val.get("traffic") - max_gt_val) < 1e-9
                            ]
                            if gt_traffic_candidates_edges: 
                                actual_priority_edge_gt_for_eval = sorted(gt_traffic_candidates_edges)[0]
                
                predicted_edge_str_sorted = predicted_edge_to_prioritize if predicted_edge_to_prioritize else None
                actual_priority_edge_gt_str_sorted = None
                if actual_priority_edge_gt_for_eval:
                    try: 
                        nodes = list(map(int, actual_priority_edge_gt_for_eval.split('-')))
                        actual_priority_edge_gt_str_sorted = "-".join(map(str, sorted(nodes)))
                    except: pass 

                if ground_truth_data_per_approach_for_eval is not None:
                    evaluated_cycles_count += 1 
                    if predicted_edge_str_sorted == actual_priority_edge_gt_str_sorted:
                        eval_result_str = "CORRECT"; correct_decision_cycles_count += 1
                    elif predicted_edge_str_sorted is None and actual_priority_edge_gt_str_sorted is None:
                        eval_result_str = "CORRECT (Both None)"; correct_decision_cycles_count += 1
                    else:
                        eval_result_str = f"INCORRECT (Pr: {predicted_edge_str_sorted}, GT: {actual_priority_edge_gt_str_sorted})"
                else:
                    eval_result_str = "INCONCLUSIVE (GT for Eval Missing)"
                
                print(f"  Prediction (Trusted Local Logic): Priority Edge -> {predicted_edge_str_sorted if predicted_edge_str_sorted else 'None (No Action)'}")
                print(f"  Ground Truth Correct Priority Edge (for EVAL ONLY): -> {actual_priority_edge_gt_str_sorted if actual_priority_edge_gt_str_sorted else 'None (No GT Priority/Traffic)'}")
                print(f"  CYCLE EVALUATION (Not used in model): {eval_result_str}")

            else: 
                if total_cycles_run <= SKIP_INITIAL_CYCLES_FOR_EVAL:
                    print(f"  Cycle {total_cycles_run}/{SKIP_INITIAL_CYCLES_FOR_EVAL} (Skipping for stabilization before performance eval)")
                else:
                    print(f"  Max evaluation cycles ({MAX_EVAL_CYCLES_FOR_REPORT}) reached. Not evaluating further for report.")
                with state_lock: trust_scores_str = {ip: f'{score:.1f}' for ip, score in sensor_data_trust_scores.items()}; print(f"  Current Data Trust Scores: { trust_scores_str if trust_scores_str else 'None' }")
                print(f"  Prediction (Trusted Local Logic): Priority Edge -> {predicted_edge_to_prioritize if predicted_edge_to_prioritize else 'None (No Action)'}")

            if os.path.exists(SIMULATION_END_SIGNAL_FILE):
                print(f"TL Info (Node {eval_node_id_log}): End signal file detected. Writing final report and exiting.")
                keep_running = False 

            if not keep_running: 
                break 

            end_time = time.time(); elapsed_this_cycle = end_time - loop_start_time
            sleep_time = max(0, EVALUATION_INTERVAL_SECONDS - elapsed_this_cycle)
            time.sleep(sleep_time)
            
    finally: 
        print(f"TL Info (Node {current_log_node_id}): Exiting main loop. Writing performance report...")
        write_performance_report()
        print(f"--- Traffic Light Controller (Node {current_log_node_id}) Shutting Down ---")
