#!/usr/bin/env python3
import networkx as nx
import json
import os
import random
import time
import threading
from flask import Flask, jsonify, abort
import math

# --- Configuration ---
GRAPH_DATA_FILE = '/shared/graph_structure.json'
CLUSTER_MAP_FILE = '/shared/cluster_edge_map.json' # Needed for /traffic endpoint
# --- Simulation Parameters ---
SIM_TIME_STEP_SECONDS = 2.0
GROUP_SPAWN_INTERVAL_SECONDS = 1.5
MAX_GROUPS = 50
MIN_GROUP_SIZE = 2
MAX_GROUP_SIZE = 8

# --- Global State (Protected by Lock) ---
simulation_lock = threading.Lock()
G = None
cluster_to_edge = {} # Map cluster ID (str) -> edge tuple (u, v) - LOADED
groups = {} # group_id -> {size, current_edge, pos_on_edge, path, destination, current_node}
edge_occupancy = {} # Map edge tuple (u, v) -> set of group_ids currently on that edge
next_group_id = 0

# --- Simulation Logic ---

def load_graph_data():
    """Loads graph structure AND cluster map."""
    global G, cluster_to_edge, edge_occupancy
    print("Loading graph data and cluster map...")
    if not os.path.exists(GRAPH_DATA_FILE): print(f"Error: Graph data file not found at {GRAPH_DATA_FILE}"); return False
    if not os.path.exists(CLUSTER_MAP_FILE): print(f"Error: Cluster map file not found at {CLUSTER_MAP_FILE}"); return False
    try:
        with open(GRAPH_DATA_FILE, 'r') as f:
            G = nx.node_link_graph(json.load(f))
            print(f"Successfully loaded graph with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")
            edge_occupancy = {tuple(sorted(edge)): set() for edge in G.edges()}
        with open(CLUSTER_MAP_FILE, 'r') as f:
            loaded_map = json.load(f)
            cluster_to_edge = { str(cid): tuple(sorted(data['edge'])) for cid, data in loaded_map.items() if 'edge' in data and isinstance(data['edge'], list) and len(data['edge']) == 2 }
            print(f"Successfully loaded cluster map for {len(cluster_to_edge)} clusters.")
        return True
    except Exception as e:
        print(f"Error loading data: {e}"); G = None; cluster_to_edge = {}; edge_occupancy = {}; return False

def get_total_cars_on_edge(edge_key):
    """Helper function to get total cars on an edge from groups (needs external lock)."""
    total_cars = 0; group_ids_on_edge = edge_occupancy.get(edge_key, set())
    for group_id in list(group_ids_on_edge):
        group = groups.get(group_id);
        if group: total_cars += group.get('size', 0)
    return total_cars

def calculate_dynamic_travel_time(edge_data, current_total_cars_on_edge):
    """Calculates travel time based on current total car occupancy."""
    speed_limit = edge_data.get('speed_limit', 60); capacity = edge_data.get('capacity', 50); distance = edge_data.get('distance', 1.0)
    if capacity <= 0: return float('inf')
    congestion_factor = min(1.0, current_total_cars_on_edge / capacity)
    effective_speed = speed_limit if congestion_factor <= 0.1 else max(1, speed_limit / (2 ** (congestion_factor * 3)))
    if effective_speed <= 0: return float('inf')
    travel_time_minutes = (distance / effective_speed) * 60; return travel_time_minutes

def find_dynamic_route(source, destination):
    """Finds route based on current dynamic travel times (using total cars)."""
    if G is None: return None
    try:
        def weight_func(u, v, data):
            edge_key = tuple(sorted((u, v)))
            with simulation_lock: current_total_cars = get_total_cars_on_edge(edge_key)
            return calculate_dynamic_travel_time(data, current_total_cars)
        path = nx.shortest_path(G, source, destination, weight=weight_func); return path
    except nx.NetworkXNoPath: return None
    except Exception as e: print(f"Error finding dynamic route from {source} to {destination}: {e}"); return None

def spawn_group():
    """Creates a new group if below max group limit."""
    global next_group_id;
    if G is None or len(G.nodes()) < 2: return
    group_size = random.randint(MIN_GROUP_SIZE, MAX_GROUP_SIZE)
    with simulation_lock:
        if len(groups) >= MAX_GROUPS: return
        nodes = list(G.nodes()); source = random.choice(nodes); destination = random.choice(nodes)
        while destination == source: destination = random.choice(nodes)
    path = find_dynamic_route(source, destination)
    if path and len(path) > 1:
        with simulation_lock:
             if len(groups) >= MAX_GROUPS: return
             group_id = next_group_id; next_group_id += 1
             start_node = path[0]; next_node = path[1]; current_edge = tuple(sorted((start_node, next_node)))
             groups[group_id] = { "id": group_id, "size": group_size, "current_edge": current_edge, "pos_on_edge": 0.0, "path": path, "destination": destination, "current_node": start_node }
             edge_occupancy.setdefault(current_edge, set()).add(group_id)
             # print(f"DEBUG SPAWN: Group {group_id} (size {group_size}) spawned on edge {current_edge}. Total groups: {len(groups)}") # Less verbose

def update_group_positions(time_step):
    """Updates position of each group. No light effects."""
    if G is None: return
    groups_to_remove = []; groups_to_move = {}
    with simulation_lock:
        current_edge_total_cars = { edge: get_total_cars_on_edge(edge) for edge in edge_occupancy }
        groups_snapshot = list(groups.items())
    for group_id, group in groups_snapshot:
        edge = group["current_edge"];
        if not edge: groups_to_remove.append(group_id); continue
        edge_key = tuple(sorted(edge)); edge_data = G.get_edge_data(*edge_key)
        if not edge_data: groups_to_remove.append(group_id); continue
        distance = edge_data.get('distance', 1.0)
        current_total_cars_on_this_edge = current_edge_total_cars.get(edge_key, 0)
        travel_time_minutes = calculate_dynamic_travel_time(edge_data, current_total_cars_on_this_edge)
        if travel_time_minutes == float('inf') or travel_time_minutes <= 0: continue
        speed_units_per_minute = distance / travel_time_minutes; speed_units_per_second = speed_units_per_minute / 60.0
        distance_moved = speed_units_per_second * time_step; fraction_moved = distance_moved / distance if distance > 0 else 0
        new_pos_on_edge = group["pos_on_edge"] + fraction_moved
        if new_pos_on_edge >= 1.0:
            start_node_of_edge = group["current_node"]
            end_node_of_edge = edge_key[1] if edge_key[0] == start_node_of_edge else edge_key[0]
            try: current_path_index = group["path"].index(end_node_of_edge)
            except ValueError: groups_to_remove.append(group_id); continue
            if end_node_of_edge == group["destination"]: groups_to_remove.append(group_id)
            elif current_path_index + 1 < len(group["path"]):
                next_node = group["path"][current_path_index + 1]; new_edge = tuple(sorted((end_node_of_edge, next_node)))
                if G.has_edge(end_node_of_edge, next_node): groups_to_move[group_id] = {"old_edge": edge_key, "new_edge": new_edge, "new_start_node": end_node_of_edge, "path": group["path"]}
                else: print(f"Warning: Path group {group_id} -> non-existent edge ({end_node_of_edge}-{next_node}). Removing."); groups_to_remove.append(group_id)
            else: print(f"DEBUG: Group {group_id} end of path at {end_node_of_edge}. Removing."); groups_to_remove.append(group_id)
        else:
             if group_id in groups: groups[group_id]["pos_on_edge"] = new_pos_on_edge
    with simulation_lock:
        for group_id in groups_to_remove:
            if group_id in groups:
                edge_key = tuple(sorted(groups[group_id]["current_edge"]))
                if edge_key in edge_occupancy and group_id in edge_occupancy[edge_key]: edge_occupancy[edge_key].remove(group_id)
                del groups[group_id]
        for group_id, move_data in groups_to_move.items():
            if group_id in groups:
                old_edge = move_data["old_edge"]; new_edge = move_data["new_edge"]
                if old_edge in edge_occupancy and group_id in edge_occupancy[old_edge]: edge_occupancy[old_edge].remove(group_id)
                groups[group_id].update({"current_edge": new_edge, "pos_on_edge": 0.0, "current_node": move_data["new_start_node"], "path": move_data["path"]})
                edge_occupancy.setdefault(new_edge, set()).add(group_id)
                # print(f"DEBUG MOVE: Group {group_id} moved from {old_edge} to {new_edge}.")

def simulation_loop():
    """Main loop to run the simulation steps ONLY."""
    print("Simulation loop started.")
    last_spawn_time = time.time()
    while True:
        start_step_time = time.time();
        if G is None: time.sleep(1); continue
        current_time = time.time()
        if current_time - last_spawn_time >= GROUP_SPAWN_INTERVAL_SECONDS: spawn_group(); last_spawn_time = current_time
        update_group_positions(SIM_TIME_STEP_SECONDS)
        end_step_time = time.time(); time_taken = end_step_time - start_step_time
        sleep_time = max(0, SIM_TIME_STEP_SECONDS - time_taken); time.sleep(sleep_time)

# --- Flask API ---
app = Flask(__name__)

@app.route('/traffic/<int:cluster_id>', methods=['GET'])
def get_traffic_for_sensor(cluster_id):
    """API endpoint for SENSORS to get current TOTAL CAR count for their monitored edge."""
    cluster_id_str = str(cluster_id)
    if not cluster_to_edge: abort(503, description="Cluster map not loaded by server.")
    monitored_edge = cluster_to_edge.get(cluster_id_str)
    if not monitored_edge: abort(404, description=f"No edge mapped for Cluster ID: {cluster_id}")
    edge_key = tuple(sorted(monitored_edge))
    total_car_count = 0
    with simulation_lock: total_car_count = get_total_cars_on_edge(edge_key)
    # print(f"DEBUG API /traffic: Cluster {cluster_id_str}, Edge {edge_key}, Count {total_car_count}")
    return jsonify({"cluster_id": cluster_id, "edge_u": edge_key[0], "edge_v": edge_key[1], "current_traffic_count": total_car_count})

@app.route('/approaching_traffic/<int:node_id>', methods=['GET'])
def get_approaching_traffic(node_id):
    """API endpoint for LIGHTS to get traffic heading towards an intersection."""
    if G is None: abort(503, description="Graph not loaded.")
    if node_id not in G: abort(404, description=f"Node {node_id} not found.")
    approaching_traffic = {}
    with simulation_lock:
        for neighbor in G.neighbors(node_id):
            edge_key = tuple(sorted((node_id, neighbor))); count = 0
            group_ids = edge_occupancy.get(edge_key, set())
            for group_id in list(group_ids):
                 group = groups.get(group_id)
                 if group and group.get("current_node") == neighbor: count += group.get("size", 0)
            approaching_traffic[f"{edge_key[0]}-{edge_key[1]}"] = count
    # print(f"DEBUG API /approaching: Node {node_id}, Traffic: {approaching_traffic}")
    return jsonify({ "node_id": node_id, "traffic_per_approach": approaching_traffic })

@app.route('/status', methods=['GET'])
def status():
    """Simple status endpoint."""
    with simulation_lock: num_groups = len(groups); total_cars_in_sim = sum(g.get('size', 0) for g in groups.values()); num_edges_occupied = sum(1 for groups in edge_occupancy.values() if groups)
    return jsonify({"status": "running", "graph_loaded": G is not None, "cluster_map_loaded": bool(cluster_to_edge), "active_groups": num_groups, "active_cars_total": total_cars_in_sim, "edges_occupied": num_edges_occupied})

# --- Main Execution ---
if __name__ == '__main__':
    print("--- Real-time Traffic Server Starting (Group Simulation ONLY) ---")
    if not load_graph_data(): print("Error: Failed to load initial graph/map data.")
    sim_thread = threading.Thread(target=simulation_loop, daemon=True)
    sim_thread.start()
    print("Simulation thread started.")
    print("Starting Flask server on 0.0.0.0:5000...")
    try: app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    except OSError as e: print(f"\n!!! Flask server failed to start: {e} !!!")
    finally: print("--- Traffic Server Shutting Down ---")

