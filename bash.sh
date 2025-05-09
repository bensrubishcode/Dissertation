#!/bin/bash

# Orchestration script for setting up the distributed ITS simulation:
# 1. Cleans previous Kathara lab.
# 2. Runs automation.py to:
#    - Generate network graph, device configurations, and Kathara's lab.confu.
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

# --- Configuration ---
SNIPPET_DIR="cmd_snippets"
GRAPH_DATA_FILE="graph_structure.json"
LIGHT_SENSOR_MAP_FILE="light_sensor_map.json" # Will contain static features + ML predictions
CLUSTER_MAP_FILE="cluster_edge_map.json"
ML_TRAINING_DATA_FILE="ml_training_data.csv"
PYTHON_EXECUTABLE="python3" # Ensure this is your correct python3 command

# --- Main Execution ---

echo ">>> Starting Full Simulation Setup and Orchestration <<<"

# 1. Clean and Delete Previous Lab
echo -e "\n>>> Step 1: Cleaning and deleting previous lab environment..."
if [ -d "lab" ]; then
    cd lab && sudo kathara lclean && cd .. > /dev/null 2>&1
else
    echo "No 'lab' directory found to clean from a previous run."
fi
sudo rm -rf lab
sudo rm -rf "$SNIPPET_DIR"
# To preserve ML data across full bash.sh runs, ML_TRAINING_DATA_FILE is NOT deleted here.
# sudo rm -f "$ML_TRAINING_DATA_FILE"
sudo rm -f "$GRAPH_DATA_FILE" "$LIGHT_SENSOR_MAP_FILE" "$CLUSTER_MAP_FILE"
echo "Previous lab environment artifacts cleaned."

# 2. Generate Configs, Snippets, Maps, and ML Training Data
echo -e "\n>>> Step 2: Running automation.py to generate configs, ML data, and perform initial ML predictions..."
AUTOMATION_OUTPUT=$(sudo $PYTHON_EXECUTABLE automation.py | tee /dev/tty)
AUTOMATION_EXIT_CODE=${PIPESTATUS[0]}
if [ $AUTOMATION_EXIT_CODE -ne 0 ]; then
    echo "[ERROR] automation.py failed with exit code $AUTOMATION_EXIT_CODE." >&2
    exit 1
fi
NUM_ROUTERS=$(echo "$AUTOMATION_OUTPUT" | grep '^ROUTERS_GENERATED=' | cut -d'=' -f2)
if ! [[ "$NUM_ROUTERS" =~ ^[0-9]+$ ]]; then
    echo "[ERROR] Bad router count from automation.py: '$NUM_ROUTERS'. Defaulting to 0 for safety." >&2
    NUM_ROUTERS=0
fi
echo "--- Detected $NUM_ROUTERS routers (or clusters) by automation.py ---"
echo "Configuration, initial ML prediction (if models available), and ML data logging by automation.py complete."

# 3. Train/Retrain ML Model using accumulated data
echo -e "\n>>> Step 3: Training/Retraining ML Model..."
if [ -f "train_ml_model.py" ]; then
    echo "Executing ML training script with sudo to ensure write permissions for model files..."
    sudo $PYTHON_EXECUTABLE train_ml_model.py # Ensure this can write .joblib files
    TRAIN_ML_EXIT_CODE=$?
    if [ $TRAIN_ML_EXIT_CODE -ne 0 ]; then
        echo "[WARNING] train_ml_model.py encountered an issue (exit code $TRAIN_ML_EXIT_CODE). Check logs above."
        echo "[WARNING] Subsequent lab runs might use older or no ML models for initial assessment."
    else
        echo "ML Model training/retraining attempt complete."
        # Verify models were created
        if [ -f "ml_preprocessor.joblib" ] && [ -f "inherent_reliability_model.joblib" ] && [ -f "configured_noisy_model.joblib" ]; then
            echo "ML model files (.joblib) successfully created/updated."
        else
            echo "[WARNING] One or more .joblib model files were NOT created after training."
        fi
    fi
else
    echo "[WARNING] train_ml_model.py not found. Skipping ML model training step."
    echo "[WARNING] Initial sensor assessments will rely on fallbacks defined in automation.py."
fi

# 4. Process lab.confu using Tacata to generate Kathara lab files
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
if [ -f "$GRAPH_DATA_FILE" ]; then sudo mv "$GRAPH_DATA_FILE" "$SHARED_DIR/" || echo "[WARN] Failed to move $GRAPH_DATA_FILE."; else echo "[INFO] $GRAPH_DATA_FILE not found."; fi
if [ -f "$LIGHT_SENSOR_MAP_FILE" ]; then sudo mv "$LIGHT_SENSOR_MAP_FILE" "$SHARED_DIR/" || echo "[WARN] Failed to move $LIGHT_SENSOR_MAP_FILE."; else echo "[INFO] $LIGHT_SENSOR_MAP_FILE not found."; fi
if [ -f "$CLUSTER_MAP_FILE" ]; then sudo mv "$CLUSTER_MAP_FILE" "$SHARED_DIR/" || echo "[WARN] Failed to move $CLUSTER_MAP_FILE."; else echo "[INFO] $CLUSTER_MAP_FILE not found."; fi
echo "Data files moved to lab/shared."

# 6. Configure Routers (FRR Setup) - Enhanced for Debugging Config File Content
echo -e "\n>>> Step 6: Configuring routers with FRR..."
if [[ "$NUM_ROUTERS" -gt 0 ]]; then
    for i in $(seq 1 $NUM_ROUTERS); do
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
echo "Content of /etc/frr/daemons for $ROUTER_NAME:"
cat /etc/frr/daemons
echo "-------------------------------------"

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
echo "Content of /etc/frr/zebra.conf for $ROUTER_NAME:"
cat /etc/frr/zebra.conf
echo "-------------------------------------"

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
echo "Content of /etc/frr/ripd.conf for $ROUTER_NAME:"
cat /etc/frr/ripd.conf
echo "-------------------------------------"

echo "Setting permissions for FRR config files on $ROUTER_NAME..."
chown frr:frr /etc/frr/*.conf
chmod 640 /etc/frr/*.conf

echo "Attempting to enable IP forwarding on $ROUTER_NAME..."
if echo 1 > /proc/sys/net/ipv4/ip_forward; then
    echo "IP forwarding enabled via /proc on $ROUTER_NAME."
else
    echo "WARNING: Failed to enable IP forwarding via /proc on $ROUTER_NAME. Check container privileges."
fi
echo "Current IP forwarding status on $ROUTER_NAME: \$(cat /proc/sys/net/ipv4/ip_forward)"

if [ -x /usr/lib/frr/frrinit.sh ]; then
    echo "Starting FRR service on $ROUTER_NAME (frrinit.sh)..."
    /usr/lib/frr/frrinit.sh start
    sleep 3
    echo "FRR startup initiated for $ROUTER_NAME. Checking processes..."
    ps aux | grep -E 'frr|zebra|ripd' || echo "No FRR processes found with ps on $ROUTER_NAME."
    echo "Checking FRR log directory contents for $ROUTER_NAME..."
    ls -la /var/log/frr/
    echo "Attempting vtysh commands on $ROUTER_NAME..."
    vtysh -c "show version" || echo "vtysh 'show version' failed on $ROUTER_NAME."
    vtysh -c "show ip rip status" || echo "vtysh 'show ip rip status' failed on $ROUTER_NAME."
    vtysh -c "show ip route" || echo "vtysh 'show ip route' failed on $ROUTER_NAME."
else
    echo "[ERROR] FRR init script /usr/lib/frr/frrinit.sh not found or not executable on $ROUTER_NAME." >&2
fi
EOF
            sudo sh -c "cat '$TMP_CMDS' >> '$ROUTER_STARTUP_FILE'"
            rm "$TMP_CMDS"
        else
            echo "[WARN] Startup file not found for $ROUTER_NAME: $ROUTER_STARTUP_FILE. Cannot configure FRR."
        fi
    done
else
    echo "No routers to configure (NUM_ROUTERS=$NUM_ROUTERS)."
fi
echo "Router FRR configuration process attempted."

# 7. Configure Clients AND Traffic Lights from Snippets
echo -e "\n>>> Step 7: Configuring clients/lights using command snippets from $SNIPPET_DIR..."
CMD_FILES_FOUND=$(find "$SNIPPET_DIR" -maxdepth 1 -name '*.cmds' -print)
if [ -z "$CMD_FILES_FOUND" ]; then
    echo "[INFO] No *.cmds files found in $SNIPPET_DIR to append to startup scripts."
else
    for CMD_FILE in $CMD_FILES_FOUND; do
        MACHINE_NAME=$(basename "$CMD_FILE" .cmds)
        TARGET_STARTUP_FILE="lab/${MACHINE_NAME}.startup"
        if [ -f "$TARGET_STARTUP_FILE" ]; then
            echo "Appending $CMD_FILE to $TARGET_STARTUP_FILE..."
            sudo sh -c "cat '$CMD_FILE' >> '$TARGET_STARTUP_FILE'"
            if [ $? -ne 0 ]; then
                echo "[ERROR] Failed appending '$CMD_FILE' to '$TARGET_STARTUP_FILE'."
            fi
        else
            echo "[WARN] Target startup file not found for snippet '$CMD_FILE': $TARGET_STARTUP_FILE."
        fi
    done
fi
echo "Client/Light configuration from snippets attempted."

# 8. Start Lab
echo -e "\n>>> Step 8: Starting Kathara lab..."
if [ ! -d "lab" ]; then
    echo "[ERROR] 'lab' directory not found. tacata.py might have failed to create it." >&2
    exit 1
fi
cd lab || { echo "[ERROR] Failed to cd into 'lab' directory." >&2; exit 1; }

echo "Running 'sudo kathara lstart --noterminals'..."
sudo kathara lstart --noterminals
KATHARA_LSTART_EXIT_CODE=$?
if [ $KATHARA_LSTART_EXIT_CODE -ne 0 ]; then
    echo "[ERROR] 'kathara lstart' failed with exit code $KATHARA_LSTART_EXIT_CODE." >&2
    cd ..
    exit 1
fi
cd ..

# --- Final Message ---
echo -e "\n----------------------------------------------------"
echo "Kathara lab setup and ML training orchestrated."
echo "Lab should be running."
echo "To connect to a device: cd lab && sudo kathara connect <device_name>"
echo "----------------------------------------------------"
exit 0
