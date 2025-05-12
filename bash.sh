#!/bin/bash

# Orchestration script for setting up the distributed ITS simulation:
# 1. Cleans previous Kathara lab.
# 2. Runs automation.py to:
#    - Generate network graph, device configurations, and Kathara's lab.confu using provided or default parameters.
#    - Assign static profiles (manufacturer, software, age) to sensors.
#    - Perform ML predictions (if models exist) for initial sensor attributes
#      and embed them in light_sensor_map.json.
#    - Log sensor static features and their "ground truth" designed characteristics
#      (inherent reliability, configured noisiness) to ml_training_data.csv.
# 3. Runs train_ml_model.py to train/retrain ML models using accumulated data.
# 4. Runs tacata.py to compile lab.confu into detailed Kathara configurations.
# 5. Moves shared data files into the Kathara lab structure.
# 6. Configures routers with FRR, including direct creation of FRR config files.
# 7. Appends device-specific commands from cmd_snippets to startup scripts.
# 8. Starts the Kathara lab.

# --- Default Configuration ---
DEFAULT_NUM_NODES=20
DEFAULT_DENSITY_FACTOR=0.3
DEFAULT_GRAPH_SEED="random" # automation.py will handle "random" by picking an int
DEFAULT_REFRESH_ML=false

# --- File/Directory Names (Constants) ---
SNIPPET_DIR="cmd_snippets"
GRAPH_DATA_FILE="graph_structure.json"
LIGHT_SENSOR_MAP_FILE="light_sensor_map.json"
CLUSTER_MAP_FILE="cluster_edge_map.json"
ML_TRAINING_DATA_FILE="ml_training_data.csv"
ML_PREPROCESSOR_FILE="ml_preprocessor.joblib"
ML_RELIABILITY_MODEL_FILE="inherent_reliability_model.joblib"
ML_NOISY_MODEL_FILE="configured_noisy_model.joblib"

PYTHON_EXECUTABLE="python3" # Ensure this is your correct python3 command

# --- Helper Functions ---
show_help() {
    echo "Usage: $0 [options]"
    echo
    echo "Options:"
    echo "  -n, --nodes <num>         Exact number of nodes for the graph (default: $DEFAULT_NUM_NODES)."
    echo "  -d, --density <float>     Graph density factor (0.0-1.0) (default: $DEFAULT_DENSITY_FACTOR)."
    echo "  -s, --seed <int|random>   Graph generation seed (default: $DEFAULT_GRAPH_SEED)."
    echo "  -r, --refresh-ml          Refresh ML model: delete training data and saved models before run."
    echo "  -h, --help                Show this help message."
    exit 0
}

# --- Parse Command-Line Arguments ---
# Initialize with defaults
NUM_NODES=$DEFAULT_NUM_NODES
DENSITY_FACTOR=$DEFAULT_DENSITY_FACTOR
GRAPH_SEED=$DEFAULT_GRAPH_SEED
REFRESH_ML=$DEFAULT_REFRESH_ML

while [[ "$#" -gt 0 ]]; do
    case $1 in
        -n|--nodes) NUM_NODES="$2"; shift ;;
        -d|--density) DENSITY_FACTOR="$2"; shift ;;
        -s|--seed) GRAPH_SEED="$2"; shift ;;
        -r|--refresh-ml) REFRESH_ML=true ;;
        -h|--help) show_help ;;
        *) echo "Unknown parameter passed: $1"; show_help; exit 1 ;;
    esac
    shift
done

echo ">>> Starting Full Simulation Setup and Orchestration <<<"
echo "Parameters to be used:"
echo "  Graph Nodes: $NUM_NODES"
echo "  Graph Density Factor: $DENSITY_FACTOR"
echo "  Graph Seed: $GRAPH_SEED"
echo "  Refresh ML Data & Models: $REFRESH_ML"

# 1. Clean and Delete Previous Lab & Optionally ML Data
echo -e "\n>>> Step 1: Cleaning environment..."
if [ -d "lab" ]; then
    cd lab && sudo kathara lclean && cd .. > /dev/null 2>&1
else
    echo "No 'lab' directory found to clean from a previous run."
fi
sudo rm -rf lab
sudo rm -rf "$SNIPPET_DIR"
sudo rm -f "$GRAPH_DATA_FILE" "$LIGHT_SENSOR_MAP_FILE" "$CLUSTER_MAP_FILE"

if [ "$REFRESH_ML" = true ]; then
    echo "Refreshing ML data: Deleting training data and saved models..."
    sudo rm -f "$ML_TRAINING_DATA_FILE" "$ML_PREPROCESSOR_FILE" "$ML_RELIABILITY_MODEL_FILE" "$ML_NOISY_MODEL_FILE"
else
    echo "Preserving existing ML training data and models (if any)."
fi
echo "Environment cleaning complete."

# 2. Generate Configs, Snippets, Maps, and ML Training Data via automation.py
echo -e "\n>>> Step 2: Running automation.py..."
AUTOMATION_CMD="sudo $PYTHON_EXECUTABLE automation.py \
    --nodes $NUM_NODES \
    --density $DENSITY_FACTOR \
    --seed $GRAPH_SEED"

echo "Executing: $AUTOMATION_CMD"
AUTOMATION_OUTPUT=$($AUTOMATION_CMD | tee /dev/tty)
AUTOMATION_EXIT_CODE=${PIPESTATUS[0]}

if [ $AUTOMATION_EXIT_CODE -ne 0 ]; then
    echo "[ERROR] automation.py failed with exit code $AUTOMATION_EXIT_CODE." >&2
    exit 1
fi
NUM_ROUTERS_OR_CLUSTERS=$(echo "$AUTOMATION_OUTPUT" | grep '^ROUTERS_GENERATED=' | cut -d'=' -f2)
if ! [[ "$NUM_ROUTERS_OR_CLUSTERS" =~ ^[0-9]+$ ]]; then
    echo "[ERROR] Bad ROUTERS_GENERATED count from automation.py: '$NUM_ROUTERS_OR_CLUSTERS'. Defaulting to 0." >&2
    NUM_ROUTERS_OR_CLUSTERS=0
fi
echo "--- Detected $NUM_ROUTERS_OR_CLUSTERS sensor clusters/routers by automation.py ---"
echo "automation.py execution complete."

# 3. Train/Retrain ML Model using accumulated data
echo -e "\n>>> Step 3: Training/Retraining ML Model..."
if [ -f "train_ml_model.py" ]; then
    echo "Executing ML training script (with sudo for file writing)..."
    sudo $PYTHON_EXECUTABLE train_ml_model.py
    TRAIN_ML_EXIT_CODE=$?
    if [ $TRAIN_ML_EXIT_CODE -ne 0 ]; then
        echo "[WARNING] train_ml_model.py encountered an issue (exit code $TRAIN_ML_EXIT_CODE). Check logs above."
    else
        echo "ML Model training/retraining attempt complete."
        if [ -f "$ML_PREPROCESSOR_FILE" ] && [ -f "$ML_RELIABILITY_MODEL_FILE" ] && [ -f "$ML_NOISY_MODEL_FILE" ]; then
            echo "ML model files (.joblib) successfully created/updated."
        else
            echo "[WARNING] One or more .joblib model files were NOT created/updated after training."
        fi
    fi
else
    echo "[WARNING] train_ml_model.py not found. Skipping ML model training step."
fi

# 4. Process lab.confu using Tacata
echo -e "\n>>> Step 4: Running tacata.py to compile Kathara lab from lab.confu..."
sudo $PYTHON_EXECUTABLE tacata.py -f -v
TACATA_EXIT_CODE=$?
if [ $TACATA_EXIT_CODE -ne 0 ]; then
    echo "[ERROR] tacata.py failed with exit code $TACATA_EXIT_CODE." >&2
    exit 1
fi
echo "Tacata processing complete. Kathara lab files generated."

# 5. Move Data Files to Shared Directory
echo -e "\n>>> Step 5: Moving data files to lab/shared Kathara directory..."
SHARED_DIR="lab/shared"
sudo mkdir -p "$SHARED_DIR"
move_if_exists() {
    if [ -f "$1" ]; then
        sudo mv "$1" "$2" || echo "[WARN] Failed to move $1 to $2."
    else
        echo "[INFO] File $1 not found (already moved or not generated)."
    fi
}
move_if_exists "$GRAPH_DATA_FILE" "$SHARED_DIR/"
move_if_exists "$LIGHT_SENSOR_MAP_FILE" "$SHARED_DIR/"
move_if_exists "$CLUSTER_MAP_FILE" "$SHARED_DIR/"
echo "Data files moved to lab/shared."

# 6. Configure Routers (FRR Setup)
echo -e "\n>>> Step 6: Configuring routers with FRR..."
if [[ "$NUM_ROUTERS_OR_CLUSTERS" -gt 0 ]]; then
    for i in $(seq 1 $NUM_ROUTERS_OR_CLUSTERS); do
        ROUTER_STARTUP_FILE="lab/router${i}.startup"
        ROUTER_NAME="router${i}"
        LAN_SUBNET="10.${i}.1.0/24"
        BACKBONE_SUBNET="192.168.254.0/24"

        if [ -f "$ROUTER_STARTUP_FILE" ]; then
            TMP_CMDS=$(mktemp)
            cat << EOF > "$TMP_CMDS"
# Router $ROUTER_NAME FRR Service Setup - Appended by bash.sh
echo "Setting up FRR for $ROUTER_NAME..."
mkdir -p /etc/frr /var/run/frr /var/log/frr
chown -R frr:frr /etc/frr /var/run/frr /var/log/frr
chmod -R u+rwx,g+rwx /var/run/frr /var/log/frr
chmod 755 /etc/frr

echo "Creating /etc/frr/daemons for $ROUTER_NAME..."
cat << EOL_DAEMONS > /etc/frr/daemons
zebra=yes
ripd=yes
zebra_options="  --daemon -A 127.0.0.1 -f /etc/frr/zebra.conf"
ripd_options="   --daemon -A 127.0.0.1 -f /etc/frr/ripd.conf"
EOL_DAEMONS
echo "Content of /etc/frr/daemons for $ROUTER_NAME:"; cat /etc/frr/daemons; echo "---"

echo "Creating /etc/frr/zebra.conf for $ROUTER_NAME..."
cat << EOL_ZEBRA > /etc/frr/zebra.conf
! Zebra configuration for $ROUTER_NAME
hostname $ROUTER_NAME
password zebra
enable password zebra
log file /var/log/frr/zebra.log debugging
!
interface eth0
 description LAN interface for $LAN_SUBNET
!
interface eth1
 description Backbone interface for $BACKBONE_SUBNET
!
interface lo
!
line vty
!
EOL_ZEBRA
echo "Content of /etc/frr/zebra.conf for $ROUTER_NAME:"; cat /etc/frr/zebra.conf; echo "---"

echo "Creating /etc/frr/ripd.conf for $ROUTER_NAME..."
cat << EOL_RIPD > /etc/frr/ripd.conf
! RIPd configuration for $ROUTER_NAME
hostname $ROUTER_NAME
password zebra
enable password zebra
!
router rip
 network $LAN_SUBNET
 network $BACKBONE_SUBNET
 redistribute connected
!
log file /var/log/frr/ripd.log debugging
!
line vty
!
EOL_RIPD
echo "Content of /etc/frr/ripd.conf for $ROUTER_NAME:"; cat /etc/frr/ripd.conf; echo "---"

chown frr:frr /etc/frr/*.conf; chmod 640 /etc/frr/*.conf
echo "Attempting to enable IP forwarding on $ROUTER_NAME..."
if echo 1 > /proc/sys/net/ipv4/ip_forward; then echo "IP forwarding enabled via /proc."; else echo "WARNING: Failed to enable IP forwarding via /proc."; fi
echo "Current IP forwarding status: \$(cat /proc/sys/net/ipv4/ip_forward)"
if [ -x /usr/lib/frr/frrinit.sh ]; then
    echo "Starting FRR service on $ROUTER_NAME..."
    /usr/lib/frr/frrinit.sh start; sleep 3
    echo "FRR processes for $ROUTER_NAME:"; ps aux | grep -E 'frr|zebra|ripd' || echo "No FRR processes."
    echo "FRR logs for $ROUTER_NAME:"; ls -la /var/log/frr/
    vtysh -c "show version" || echo "vtysh 'show version' failed."
else
    echo "[ERROR] FRR init script not found on $ROUTER_NAME." >&2
fi
EOF
            sudo sh -c "cat '$TMP_CMDS' >> '$ROUTER_STARTUP_FILE'"
            rm "$TMP_CMDS"
        else
            echo "[WARN] Startup file not found for $ROUTER_NAME."
        fi
    done
else
    echo "No routers to configure (NUM_ROUTERS_OR_CLUSTERS=$NUM_ROUTERS_OR_CLUSTERS)."
fi
echo "Router FRR configuration process attempted."

# 7. Configure Clients AND Traffic Lights from Snippets
echo -e "\n>>> Step 7: Configuring clients/lights using command snippets..."
CMD_FILES_FOUND=$(find "$SNIPPET_DIR" -maxdepth 1 -name '*.cmds' -print)
if [ -z "$CMD_FILES_FOUND" ]; then
    echo "[INFO] No *.cmds files found in $SNIPPET_DIR."
else
    for CMD_FILE in $CMD_FILES_FOUND; do
        MACHINE_NAME=$(basename "$CMD_FILE" .cmds)
        TARGET_STARTUP_FILE="lab/${MACHINE_NAME}.startup"
        if [ -f "$TARGET_STARTUP_FILE" ]; then
            echo "Appending $CMD_FILE to $TARGET_STARTUP_FILE..."
            sudo sh -c "cat '$CMD_FILE' >> '$TARGET_STARTUP_FILE'"
            if [ $? -ne 0 ]; then echo "[ERROR] Failed appending '$CMD_FILE'."; fi
        else echo "[WARN] Target startup file not found for '$CMD_FILE': $TARGET_STARTUP_FILE."; fi
    done
fi
echo "Client/Light configuration from snippets attempted."

# 8. Start Lab
echo -e "\n>>> Step 8: Starting Kathara lab..."
if [ ! -d "lab" ]; then echo "[ERROR] 'lab' directory not found." >&2; exit 1; fi
cd lab || { echo "[ERROR] Failed to cd into 'lab' directory." >&2; exit 1; }
echo "Running 'sudo kathara lstart --noterminals'..."
sudo kathara lstart --noterminals
KATHARA_LSTART_EXIT_CODE=$?
if [ $KATHARA_LSTART_EXIT_CODE -ne 0 ]; then
    echo "[ERROR] 'kathara lstart' failed with exit code $KATHARA_LSTART_EXIT_CODE." >&2
    cd ..; exit 1
fi
cd ..
echo -e "\n----------------------------------------------------"
echo "Kathara lab setup and ML training orchestrated."
echo "Lab should be running."
echo "To connect to a device: cd lab && sudo kathara connect <device_name>"
echo "----------------------------------------------------"
exit 0
