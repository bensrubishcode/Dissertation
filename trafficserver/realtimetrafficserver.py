#!/usr/bin/env python3
import networkx as nx
import json
import os
import random
import time
import threading
from flask import Flask, jsonify, abort, make_response
import math
import copy # For deep copying data structures

# --- Configuration ---
GRAPH_DATA_FILE = '/shared/graph_structure.json'
CLUSTER_MAP_FILE = '/shared/cluster_edge_map.json'
# --- Simulation Parameters ---
SIM_TIME_STEP_SECONDS = 2.0
# GROUP_SPAWN_INTERVAL_SECONDS = 1.5 # This is now implicitly tied to SIM_TIME_STEP_SECONDS
MAX_GROUPS = 50
MIN_GROUP_SIZE = 2
MAX_GROUP_SIZE = 8
PRIORITY_SPAWN_CHANCE = 0.05 # NEW: 5% chance for a new group to be priority
CENTRAL_SERVER_PORT = 5000
# --- Global State ---
state_publish_lock = threading.Lock()

published_state = {
    "G": None,
    "cluster_to_edge": {},
    "groups": {},
    "edge_occupancy": {},
    "passed_through_node_log_current_step": {},
    "graph_loaded_successfully": False,
    "map_loaded_successfully": False,
    "last_updated_timestamp": 0.0
}
next_group_id = 0

# --- Logging Function ---
def log_msg(message):
    """Prints a message with a timestamp."""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] TS: {message}")

# --- Simulation Logic ---

def load_graph_and_map_initial():
    log_msg("Attempting to load graph data and cluster map...")
    global published_state

    temp_G = None
    temp_cluster_to_edge = {}
    graph_ok = False
    map_ok = False

    if not os.path.exists(GRAPH_DATA_FILE):
        log_msg(f"FATAL Error: Graph data file not found at {GRAPH_DATA_FILE}")
        return False
    if not os.path.exists(CLUSTER_MAP_FILE):
        log_msg(f"FATAL Error: Cluster map file not found at {CLUSTER_MAP_FILE}")
        return False
    try:
        with open(GRAPH_DATA_FILE, 'r') as f:
            graph_json = json.load(f)
            if graph_json.get('nodes') and graph_json['nodes'] and isinstance(graph_json['nodes'][0].get('id'), str):
                for link in graph_json.get('links', []):
                    if 'source' in link: link['source'] = int(link['source'])
                    if 'target' in link: link['target'] = int(link['target'])
                for node_data in graph_json.get('nodes',[]):
                    if 'id' in node_data: node_data['id'] = int(node_data['id'])
            temp_G = nx.node_link_graph(graph_json)
            log_msg(f"Successfully loaded graph with {temp_G.number_of_nodes()} nodes and {temp_G.number_of_edges()} edges.")
            graph_ok = True
        
        with open(CLUSTER_MAP_FILE, 'r') as f:
            loaded_map = json.load(f)
            temp_cluster_to_edge = {
                str(cid): tuple(sorted(map(int, data['edge'])))
                for cid, data in loaded_map.items()
                if 'edge' in data and isinstance(data['edge'], list) and len(data['edge']) == 2
            }
            log_msg(f"Successfully loaded cluster map for {len(temp_cluster_to_edge)} clusters.")
            map_ok = True
        
        with state_publish_lock:
            published_state["G"] = temp_G
            published_state["cluster_to_edge"] = temp_cluster_to_edge
            published_state["graph_loaded_successfully"] = graph_ok
            published_state["map_loaded_successfully"] = map_ok
            if temp_G:
                published_state["edge_occupancy"] = {tuple(sorted(map(int,edge))): set() for edge in temp_G.edges()}
        return graph_ok and map_ok
    except Exception as e:
        log_msg(f"Error during initial data load: {e}")
        with state_publish_lock: # Ensure state reflects failure
            published_state["G"] = None
            published_state["cluster_to_edge"] = {}
            published_state["graph_loaded_successfully"] = False
            published_state["map_loaded_successfully"] = False
        return False

def get_total_cars_on_edge_local(edge_key_tuple, local_edge_occupancy, local_groups):
    total_cars = 0
    group_ids_on_edge = local_edge_occupancy.get(edge_key_tuple, set())
    for group_id in list(group_ids_on_edge):
        group = local_groups.get(group_id)
        if group: total_cars += group.get('size', 0)
    return total_cars

def calculate_dynamic_travel_time_local(edge_data, current_total_cars_on_edge):
    speed_limit = edge_data.get('speed_limit', 60)
    capacity = edge_data.get('capacity', 50)
    distance = edge_data.get('distance', 1.0)
    if capacity <= 0: return float('inf')
    congestion_factor = min(1.0, current_total_cars_on_edge / capacity) # Ensure capacity isn't zero
    effective_speed = speed_limit if congestion_factor <= 0.1 else max(1, speed_limit / (2 ** (congestion_factor * 3)))
    if effective_speed <= 0: return float('inf')
    return (distance / effective_speed) * 60

def find_dynamic_route_local(source, destination, local_G, local_edge_occupancy, local_groups):
    if local_G is None: return None
    try:
        def weight_func_local(u, v, data):
            edge_key = tuple(sorted((u, v)))
            current_total_cars = get_total_cars_on_edge_local(edge_key, local_edge_occupancy, local_groups)
            return calculate_dynamic_travel_time_local(data, current_total_cars)
        
        path = nx.shortest_path(local_G, source, destination, weight=weight_func_local)
        return path
    except nx.NetworkXNoPath: return None
    except Exception as e: log_msg(f"Error finding dynamic route from {source} to {destination}: {e}"); return None

def spawn_group_local(local_G, local_groups, local_edge_occupancy, current_next_group_id):
    if local_G is None or len(local_G.nodes()) < 2: return None
    if len(local_groups) >= MAX_GROUPS: return None

    nodes = list(local_G.nodes())
    source = random.choice(nodes)
    destination = random.choice(nodes)
    while destination == source: destination = random.choice(nodes)

    path = find_dynamic_route_local(source, destination, local_G, local_edge_occupancy, local_groups)

    if path and len(path) > 1:
        group_id = current_next_group_id
        group_size = random.randint(MIN_GROUP_SIZE, MAX_GROUP_SIZE)
        start_node = path[0]; next_node_in_path = path[1]
        current_edge_tuple = tuple(sorted((start_node, next_node_in_path)))
        
        # NEW: Determine if this group is a priority vehicle
        is_priority_group = random.random() < PRIORITY_SPAWN_CHANCE
        
        local_groups[group_id] = {
            "id": group_id, "size": group_size,
            "current_edge": current_edge_tuple, "pos_on_edge": 0.0,
            "path": path, "destination": destination, "current_node": start_node,
            "is_priority": is_priority_group # NEW: Store priority status
        }
        local_edge_occupancy.setdefault(current_edge_tuple, set()).add(group_id)
        # if is_priority_group: # Optional: log priority spawns
        #     log_msg(f"SPAWNED PRIORITY Group {group_id} (size {group_size}) on edge {current_edge_tuple} Path: {path}")
        return group_id
    return None

def update_group_positions_local(time_step, local_G, local_groups, local_edge_occupancy, local_passed_through_log):
    if local_G is None: return

    groups_to_remove = []
    groups_to_move_to_new_edge = {}
    
    current_edge_total_cars_snapshot = {
        edge: get_total_cars_on_edge_local(edge, local_edge_occupancy, local_groups)
        for edge in local_edge_occupancy
    }

    for group_id, group_data in list(local_groups.items()):
        current_edge_tuple = group_data["current_edge"]
        if not current_edge_tuple: groups_to_remove.append(group_id); continue
        edge_data = local_G.get_edge_data(*current_edge_tuple)
        if not edge_data: groups_to_remove.append(group_id); continue

        distance_on_edge = edge_data.get('distance', 1.0)
        cars_on_this_edge = current_edge_total_cars_snapshot.get(current_edge_tuple, 0)
        travel_time_minutes = calculate_dynamic_travel_time_local(edge_data, cars_on_this_edge)

        if travel_time_minutes == float('inf') or travel_time_minutes <= 0: continue

        speed_units_per_minute = distance_on_edge / travel_time_minutes
        speed_units_per_second = speed_units_per_minute / 60.0
        distance_moved_this_step = speed_units_per_second * time_step
        fraction_moved_this_step = distance_moved_this_step / distance_on_edge if distance_on_edge > 0 else 1.0
        new_pos_on_edge = group_data["pos_on_edge"] + fraction_moved_this_step

        if new_pos_on_edge >= 1.0:
            start_node_of_completed_edge = group_data["current_node"]
            end_node_of_completed_edge = current_edge_tuple[0] if current_edge_tuple[1] == start_node_of_completed_edge else current_edge_tuple[1]
            try:
                current_path_index = group_data["path"].index(end_node_of_completed_edge)
            except ValueError: groups_to_remove.append(group_id); continue # Should not happen if path is valid
            
            local_passed_through_log[end_node_of_completed_edge] = \
                local_passed_through_log.get(end_node_of_completed_edge, 0) + group_data.get("size", 0)

            if end_node_of_completed_edge == group_data["destination"]:
                groups_to_remove.append(group_id)
            elif current_path_index + 1 < len(group_data["path"]):
                next_node_in_path = group_data["path"][current_path_index + 1]
                new_edge_tuple = tuple(sorted((end_node_of_completed_edge, next_node_in_path)))
                if local_G.has_edge(end_node_of_completed_edge, next_node_in_path):
                    groups_to_move_to_new_edge[group_id] = {
                        "old_edge": current_edge_tuple, "new_edge": new_edge_tuple,
                        "new_start_node": end_node_of_completed_edge, "path": group_data["path"]
                        # is_priority status carries over with the group_data implicitly
                    }
                else: groups_to_remove.append(group_id) # Path broken
            else: groups_to_remove.append(group_id) # End of path but not destination (should be caught above)
        else:
            local_groups[group_id]["pos_on_edge"] = new_pos_on_edge
    
    for group_id_rem in groups_to_remove:
        if group_id_rem in local_groups:
            old_edge_key_rem = local_groups[group_id_rem]["current_edge"]
            if old_edge_key_rem in local_edge_occupancy and group_id_rem in local_edge_occupancy[old_edge_key_rem]:
                local_edge_occupancy[old_edge_key_rem].remove(group_id_rem)
            del local_groups[group_id_rem]

    for group_id_mov, move_data in groups_to_move_to_new_edge.items():
        if group_id_mov in local_groups: # Group might have been removed if path became invalid
            old_edge_mov = move_data["old_edge"]; new_edge_mov = move_data["new_edge"]
            if old_edge_mov in local_edge_occupancy and group_id_mov in local_edge_occupancy[old_edge_mov]:
                local_edge_occupancy[old_edge_mov].remove(group_id_mov)
            
            # Update group's state for the new edge
            local_groups[group_id_mov].update({
                "current_edge": new_edge_mov, 
                "pos_on_edge": 0.0,
                "current_node": move_data["new_start_node"],
                # "path" is already correct, "is_priority" also carries over
            })
            local_edge_occupancy.setdefault(new_edge_mov, set()).add(group_id_mov)


def simulation_step_runner():
    global next_group_id

    local_G = published_state["G"] 
    if not local_G:
        log_msg("SimStepRunner: Graph (local_G) is None, cannot run step.")
        return None 

    with state_publish_lock:
        local_groups = copy.deepcopy(published_state["groups"])
        local_edge_occupancy = copy.deepcopy(published_state["edge_occupancy"])
        for edge in local_G.edges(): # Ensure all graph edges are keys
            u,v = map(int,edge)
            edge_key = tuple(sorted((u,v)))
            if edge_key not in local_edge_occupancy:
                local_edge_occupancy[edge_key] = set()
        current_group_id_for_spawn = next_group_id

    local_passed_through_log = {} 

    spawned_group_id = spawn_group_local(local_G, local_groups, local_edge_occupancy, current_group_id_for_spawn)
    if spawned_group_id is not None:
        with state_publish_lock: # Increment global ID only if spawn was successful
            next_group_id +=1

    update_group_positions_local(SIM_TIME_STEP_SECONDS, local_G, local_groups, local_edge_occupancy, local_passed_through_log)
    
    return local_groups, local_edge_occupancy, local_passed_through_log


def simulation_loop():
    log_msg("Simulation loop started.")
    # last_spawn_attempt_time = time.time() # Not strictly needed if spawning each sim step

    while True:
        loop_start_time = time.time()

        if not published_state["graph_loaded_successfully"]:
            log_msg("SimLoop: Graph not loaded, waiting...")
            time.sleep(SIM_TIME_STEP_SECONDS)
            continue
        
        step_calc_start_time = time.time()
        new_state_tuple = simulation_step_runner()
        
        if new_state_tuple is None:
            log_msg("SimLoop: simulation_step_runner returned None, skipping state update.")
            processing_time = time.time() - step_calc_start_time
            sleep_duration = max(0, SIM_TIME_STEP_SECONDS - processing_time)
            time.sleep(sleep_duration)
            continue
            
        new_groups, new_edge_occupancy, new_passed_through_log = new_state_tuple
        step_calc_duration = time.time() - step_calc_start_time
        # log_msg(f"SimLoop: Step calculation took {step_calc_duration:.4f}s.") # Can be verbose

        publish_start_time = time.time()
        with state_publish_lock:
            published_state["groups"] = new_groups
            published_state["edge_occupancy"] = new_edge_occupancy
            published_state["passed_through_node_log_current_step"] = new_passed_through_log
            published_state["last_updated_timestamp"] = time.time()
        publish_duration = time.time() - publish_start_time
        # log_msg(f"SimLoop: State publish took {publish_duration:.4f}s.") # Can be verbose

        loop_total_time = time.time() - loop_start_time
        sleep_time = max(0, SIM_TIME_STEP_SECONDS - loop_total_time)
        if loop_total_time > SIM_TIME_STEP_SECONDS:
            log_msg(f"Warning: SimLoop total processing for step ({loop_total_time:.4f}s) exceeded SIM_TIME_STEP_SECONDS ({SIM_TIME_STEP_SECONDS:.4f}s). Sleeping for 0s.")
        time.sleep(sleep_time)

# --- Flask API ---
app = Flask(__name__)

@app.errorhandler(404)
def resource_not_found(e):
    return jsonify(error=str(e)), 404

@app.errorhandler(503)
def service_unavailable(e):
    return jsonify(error=str(e)), 503

@app.route('/traffic/<int:cluster_id>', methods=['GET'])
def get_traffic_for_sensor_api(cluster_id):
    # api_call_received_time = time.time() # For detailed timing
    # log_msg(f"API /traffic/{cluster_id}: Request received.")

    with state_publish_lock:
        if not published_state["graph_loaded_successfully"] or not published_state["map_loaded_successfully"]:
            # log_msg(f"API /traffic/{cluster_id}: Aborting 503, data not loaded.")
            abort(503, description="Graph or cluster map not loaded by server.")
        
        local_G = published_state["G"]
        local_cluster_to_edge = published_state["cluster_to_edge"]
        local_edge_occupancy = published_state["edge_occupancy"]
        local_groups = published_state["groups"]
        # last_update_ts = published_state["last_updated_timestamp"] # For logging if needed
    
    # log_msg(f"API /traffic/{cluster_id}: Reading from state updated at {last_update_ts:.2f}.")

    cluster_id_str = str(cluster_id)
    monitored_edge = local_cluster_to_edge.get(cluster_id_str)
    if not monitored_edge:
        # log_msg(f"API /traffic/{cluster_id}: Aborting 404, no edge for cluster.")
        abort(404, description=f"No edge mapped for Cluster ID: {cluster_id_str}")

    total_car_count = get_total_cars_on_edge_local(monitored_edge, local_edge_occupancy, local_groups)
    
    # NEW: Check for priority vehicles on this edge
    has_priority_vehicle_on_edge = False
    group_ids_on_monitored_edge = local_edge_occupancy.get(monitored_edge, set())
    for group_id_on_edge in group_ids_on_monitored_edge: # Iterate over a copy if concerned about modification
        group_detail = local_groups.get(group_id_on_edge)
        if group_detail and group_detail.get("is_priority", False):
            has_priority_vehicle_on_edge = True
            break
            
    response_data = {
        "cluster_id": cluster_id, 
        "edge_u": monitored_edge[0], 
        "edge_v": monitored_edge[1], 
        "current_traffic_count": total_car_count,
        "priority_detected": has_priority_vehicle_on_edge # NEW field
    }
    # log_msg(f"API /traffic/{cluster_id}: Responding. Total time: {time.time() - api_call_received_time:.4f}s")
    return jsonify(response_data)


@app.route('/approaching_traffic/<int:node_id>', methods=['GET'])
def get_approaching_traffic_api(node_id):
    # api_call_received_time = time.time()
    # log_msg(f"API /approaching_traffic/{node_id}: Request received.")

    with state_publish_lock:
        if not published_state["graph_loaded_successfully"]:
            # log_msg(f"API /approaching_traffic/{node_id}: Aborting 503, graph not loaded.")
            abort(503, description="Graph not loaded by server.")
        local_G = published_state["G"]
        local_edge_occupancy = published_state["edge_occupancy"]
        local_groups = published_state["groups"]
        # last_update_ts = published_state["last_updated_timestamp"]

    # log_msg(f"API /approaching_traffic/{node_id}: Reading from state updated at {last_update_ts:.2f}.")

    if node_id not in local_G.nodes():
        # log_msg(f"API /approaching_traffic/{node_id}: Aborting 404, node not in graph.")
        abort(404, description=f"Node {node_id} not found in graph.")
    
    approaching_traffic = {}
    for neighbor in local_G.neighbors(node_id):
        edge_key_tuple = tuple(sorted((node_id, neighbor)))
        count = 0
        # NEW: Check for priority on approach
        has_priority_on_approach = False
        group_ids_on_edge = local_edge_occupancy.get(edge_key_tuple, set())
        for group_id in list(group_ids_on_edge):
             group = local_groups.get(group_id)
             if group and group.get("current_node") == neighbor: # Approaching node_id from neighbor
                 count += group.get("size", 0)
                 if group.get("is_priority", False):
                     has_priority_on_approach = True # No break, count all cars
        
        # Edge name indicates direction TOWARDS node_id
        approaching_traffic[f"{neighbor}-{node_id}"] = {
            "traffic": count,
            "priority_detected": has_priority_on_approach # NEW
        }
    
    response_data = { "node_id": node_id, "traffic_per_approach": approaching_traffic }
    # log_msg(f"API /approaching_traffic/{node_id}: Responding. Total time: {time.time() - api_call_received_time:.4f}s")
    return jsonify(response_data)

@app.route('/passed_through_node_count/<int:node_id>', methods=['GET'])
def get_passed_through_node_count_api(node_id):
    # api_call_received_time = time.time()
    # log_msg(f"API /passed_through_node_count/{node_id}: Request received.")
    count = 0
    with state_publish_lock:
        if not published_state["graph_loaded_successfully"]:
            # log_msg(f"API /passed_through_node_count/{node_id}: Aborting 503, graph not loaded.")
            abort(503, description="Graph not loaded by server.")
        local_G = published_state["G"]
        local_passed_through_log = published_state["passed_through_node_log_current_step"]
        # last_update_ts = published_state["last_updated_timestamp"]

    # log_msg(f"API /passed_through_node_count/{node_id}: Reading from state updated at {last_update_ts:.2f}.")

    if node_id not in local_G.nodes():
        # log_msg(f"API /passed_through_node_count/{node_id}: Aborting 404, node not in graph.")
        abort(404, description=f"Node {node_id} not found in graph.")
    
    count = local_passed_through_log.get(node_id, 0)
    
    response_data = {"node_id": node_id, "cars_passed_through_last_step": count}
    # log_msg(f"API /passed_through_node_count/{node_id}: Responding. Total time: {time.time() - api_call_received_time:.4f}s")
    return jsonify(response_data)

@app.route('/status', methods=['GET'])
def status_api():
    # api_call_received_time = time.time()
    # log_msg(f"API /status: Request received.")
    with state_publish_lock:
        num_groups = len(published_state["groups"])
        total_cars_in_sim = sum(g.get('size', 0) for g in published_state["groups"].values())
        num_priority_groups = sum(1 for g in published_state["groups"].values() if g.get("is_priority"))
        num_edges_occupied = sum(1 for groups_on_edge in published_state["edge_occupancy"].values() if groups_on_edge)
        graph_loaded = published_state["graph_loaded_successfully"]
        map_loaded = published_state["map_loaded_successfully"]
        last_update_ts = published_state["last_updated_timestamp"]

    response_data = {
        "status": "running", "graph_loaded": graph_loaded,
        "cluster_map_loaded": map_loaded, "active_groups": num_groups,
        "active_cars_total": total_cars_in_sim, 
        "active_priority_groups": num_priority_groups, # NEW
        "edges_occupied": num_edges_occupied,
        "last_sim_step_timestamp": last_update_ts
    }
    # log_msg(f"API /status: Responding. Total time: {time.time() - api_call_received_time:.4f}s")
    return jsonify(response_data)

# --- Main Execution ---
if __name__ == '__main__':
    log_msg("--- Real-time Traffic Server Starting ---")
    if not load_graph_and_map_initial():
        log_msg("FATAL Error: Failed to load initial graph/map data. Exiting.")
        exit(1)
        
    sim_thread = threading.Thread(target=simulation_loop, daemon=True)
    sim_thread.start()
    log_msg("Simulation thread started.")
    log_msg(f"Starting Flask server on 0.0.0.0:{CENTRAL_SERVER_PORT}...")
    try:
        # Make sure to use threaded=True for Flask app in a multi-threaded environment
        app.run(host='0.0.0.0', port=CENTRAL_SERVER_PORT, debug=False, use_reloader=False, threaded=True)
    except OSError as e:
        log_msg(f"\n!!! Flask server failed to start (port {CENTRAL_SERVER_PORT} likely in use): {e} !!!")
    except Exception as e:
        log_msg(f"\n!!! An unexpected error occurred starting Flask server: {e} !!!")
    finally:
        log_msg("--- Traffic Server Shutting Down ---")

