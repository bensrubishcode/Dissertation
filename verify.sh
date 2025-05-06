#!/bin/bash

# Script to automatically verify the status and basic functionality
# of the running Kathara traffic simulation lab.
# Run this script from the main project directory (e.g., Dissertation/)
# AFTER ./bash.sh has successfully started the lab.

echo "--- Starting Kathara Lab Verification ---"

# --- Configuration ---
LAB_DIR="lab"
TRAFFIC_SERVER_IP="192.168.254.200"
TRAFFIC_SERVER_PORT="5000"
SENSOR_SERVER_PORT="5001" # Port sensors listen on
EXPECTED_ROUTERS=$(grep '^ROUTERS_GENERATED=' automation_output.log | cut -d'=' -f2 2>/dev/null) # Read from a log file (see note below)
# If reading from log fails, fallback or require argument? For now, let's try a default or skip router count check.
if ! [[ "$EXPECTED_ROUTERS" =~ ^[1-9][0-9]*$ ]]; then
    echo "[WARN] Could not determine expected router count. Skipping count check."
    EXPECTED_ROUTERS=0 # Set to 0 to skip count check effectively
fi
# Similarly, determine expected number of lights/sensors if needed for count checks
# For now, we'll just check if *at least one* of each type is running.

# --- Helper Function ---
check_status() {
    local description="$1"
    local command_output="$2"
    local success_pattern="$3"
    local status="FAIL"

    echo -n "Check: $description ... "
    if [[ "$command_output" == *"$success_pattern"* ]]; then
        status="PASS"
    fi
    echo "$status"
    # Optionally print command output on failure:
    # if [[ "$status" == "FAIL" ]]; then echo "Output: $command_output"; fi
    [[ "$status" == "PASS" ]] # Return success/failure for script logic
}

# --- Verification Steps ---

# 1. Check if Lab Directory Exists
if [ ! -d "$LAB_DIR" ]; then
    echo "[ERROR] Lab directory '$LAB_DIR' not found. Did ./bash.sh run correctly?"
    exit 1
fi
cd "$LAB_DIR" || exit 1 # Enter lab directory for kathara commands

# 2. Check Running Containers
echo -e "\n--- Checking Container Status ---"
RUNNING_CONTAINERS=$(sudo docker ps --format '{{.Names}}')
SERVER_RUNNING=$(echo "$RUNNING_CONTAINERS" | grep -c "traffic_server")
ROUTERS_RUNNING=$(echo "$RUNNING_CONTAINERS" | grep -c "router")
SENSORS_RUNNING=$(echo "$RUNNING_CONTAINERS" | grep -c "cluster") # Assuming sensors are named clusterX_machineY
LIGHTS_RUNNING=$(echo "$RUNNING_CONTAINERS" | grep -c "traffic_light_")

overall_status=0 # 0 = PASS, 1 = FAIL

check_status "Traffic Server Running" "$SERVER_RUNNING" "1" || overall_status=1
# Optional: Check exact router count if EXPECTED_ROUTERS was found
if [ "$EXPECTED_ROUTERS" -gt 0 ]; then
    check_status "Correct Number of Routers Running ($EXPECTED_ROUTERS)" "$ROUTERS_RUNNING" "$EXPECTED_ROUTERS" || overall_status=1
else
     check_status "At least one Router Running" "$ROUTERS_RUNNING" "1" || overall_status=1 # Check if at least 1 router is up
fi
check_status "At least one Sensor Running" "$SENSORS_RUNNING" "1" || overall_status=1 # Check if at least 1 sensor is up
check_status "At least one Traffic Light Running" "$LIGHTS_RUNNING" "1" || overall_status=1 # Check if at least 1 light is up


# 3. Check Traffic Server API Status
echo -e "\n--- Checking Traffic Server API ---"
# Use kathara exec to run curl inside a reliable container (e.g., router1)
# Need to find the actual name Kathara assigned to router1
ROUTER1_NAME=$(echo "$RUNNING_CONTAINERS" | grep "router1" | head -n 1)
if [ -z "$ROUTER1_NAME" ]; then
     echo "Check: Traffic Server API Status ... FAIL (Could not find router1 to run curl from)"
     overall_status=1
else
    # Might need 'apt update && apt install -y curl' in router image if not present
    # Add --fail to curl to make it exit non-zero on HTTP errors
    SERVER_STATUS_OUTPUT=$(sudo kathara exec "$ROUTER1_NAME" -- curl --fail -s --connect-timeout 3 "http://${TRAFFIC_SERVER_IP}:${TRAFFIC_SERVER_PORT}/status" 2>&1)
    # Check for a key part of the expected JSON success response
    check_status "Traffic Server API Status (/status endpoint)" "$SERVER_STATUS_OUTPUT" '"status": "running"' || overall_status=1
    # Optional: Check if graph/map loaded
    check_status "Traffic Server Graph Loaded" "$SERVER_STATUS_OUTPUT" '"graph_loaded": true' || echo "  (Warning: Graph not loaded on server)"
    check_status "Traffic Server Cluster Map Loaded" "$SERVER_STATUS_OUTPUT" '"cluster_map_loaded": true' || echo "  (Warning: Cluster map not loaded on server)"
    check_status "Traffic Server Active Groups > 0 (Wait ~10s after start)" "$SERVER_STATUS_OUTPUT" '"active_groups": [1-9]' || echo "  (Info: No active groups yet, might be normal early on)"
fi

# 4. Check Sample Sensor Status (via its own socket server)
echo -e "\n--- Checking Sample Sensor Status ---"
# Find the name for cluster1_machine1
SENSOR1_NAME=$(echo "$RUNNING_CONTAINERS" | grep "cluster1_machine1" | head -n 1)
if [ -z "$SENSOR1_NAME" ]; then
    echo "Check: Sensor 1 Status ... FAIL (Could not find cluster1_machine1 container)"
    overall_status=1
else
    # Use netcat (nc) to connect and send command, check response
    # Requires 'netcat-openbsd' or similar in the *router* image
    SENSOR_IP="10.1.1.1" # Assuming standard IP for cluster1_machine1
    # Send GET_TRAFFIC, wait 1 sec for reply, check if reply starts with TRAFFIC=
    # Use kathara exec on router1 again
    SENSOR_STATUS_OUTPUT=$(sudo kathara exec "$ROUTER1_NAME" -- sh -c "echo 'GET_TRAFFIC' | nc -w 1 ${SENSOR_IP} ${SENSOR_SERVER_PORT}" 2>&1)
    check_status "Sensor 1 Responding (Port ${SENSOR_SERVER_PORT})" "$SENSOR_STATUS_OUTPUT" "TRAFFIC=" || overall_status=1
fi

# 5. Check Sample Traffic Light Status (via Logs)
echo -e "\n--- Checking Sample Traffic Light Logs ---"
# Find the name for a traffic light (e.g., traffic_light_3 if node 3 had a light)
# Need to know which nodes got lights from automation output or light_sensor_map.json
# Let's assume node 3 had a light for this example
LIGHT3_NAME=$(echo "$RUNNING_CONTAINERS" | grep "traffic_light_3" | head -n 1)
if [ -z "$LIGHT3_NAME" ]; then
    echo "Check: Traffic Light 3 Logs ... SKIP (Could not find traffic_light_3 container or Node 3 had no light)"
else
    # Get recent logs using docker logs
    # Need the *full* docker container name here
    LIGHT3_DOCKER_NAME=$(sudo docker ps --filter "name=traffic_light_3" --format '{{.Names}}' | head -n 1)
    if [ -z "$LIGHT3_DOCKER_NAME" ]; then
        echo "Check: Traffic Light 3 Logs ... FAIL (Could not find Docker container name for $LIGHT3_NAME)"
        overall_status=1
    else
        # Check last 20 lines for an evaluation message
        RECENT_LOGS=$(sudo docker logs --tail 20 "$LIGHT3_DOCKER_NAME" 2>&1)
        check_status "Traffic Light 3 Logging Evaluations" "$RECENT_LOGS" "EVALUATION:" || overall_status=1
    fi
fi

# 6. Basic Connectivity Checks
echo -e "\n--- Checking Basic Connectivity ---"
# Sensor 1 -> Server
if [ -n "$SENSOR1_NAME" ]; then
    PING_S1_TO_SERVER=$(sudo kathara exec "$SENSOR1_NAME" -- ping -c 2 -W 1 "$TRAFFIC_SERVER_IP" 2>&1)
    check_status "Sensor 1 -> Server Ping" "$PING_S1_TO_SERVER" "2 received" || overall_status=1
else
    echo "Check: Sensor 1 -> Server Ping ... SKIP (Sensor 1 not found)"
fi
# Light 3 -> Server
if [ -n "$LIGHT3_NAME" ]; then
    PING_L3_TO_SERVER=$(sudo kathara exec "$LIGHT3_NAME" -- ping -c 2 -W 1 "$TRAFFIC_SERVER_IP" 2>&1)
    check_status "Light 3 -> Server Ping" "$PING_L3_TO_SERVER" "2 received" || overall_status=1
else
     echo "Check: Light 3 -> Server Ping ... SKIP (Light 3 not found)"
fi
# Light 3 -> Sensor 1
if [ -n "$LIGHT3_NAME" ] && [ -n "$SENSOR1_NAME" ]; then
    PING_L3_TO_S1=$(sudo kathara exec "$LIGHT3_NAME" -- ping -c 2 -W 1 "$SENSOR_IP" 2>&1)
    check_status "Light 3 -> Sensor 1 Ping" "$PING_L3_TO_S1" "2 received" || overall_status=1
else
     echo "Check: Light 3 -> Sensor 1 Ping ... SKIP (Light 3 or Sensor 1 not found)"
fi


# --- Summary ---
echo -e "\n--- Verification Summary ---"
if [ $overall_status -eq 0 ]; then
    echo "Overall Status: PASS"
    exit 0
else
    echo "Overall Status: FAIL (See details above)"
    exit 1
fi
