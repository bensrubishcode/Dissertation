#!/bin/bash

# Setup for distributed traffic light devices.
# Moves necessary data files for server & lights.
# Injects client/light identity/startup commands from cmd_snippets/.
# Starts Kathara silently.

# --- Configuration ---
SNIPPET_DIR="cmd_snippets"
GRAPH_DATA_FILE="graph_structure.json" # Needed by server
LIGHT_SENSOR_MAP_FILE="light_sensor_map.json" # Needed by lights
CLUSTER_MAP_FILE="cluster_edge_map.json" # Needed by server (if using /traffic endpoint)

# --- Main Execution ---

# 1. Clean and Delete Previous Lab
echo "Cleaning and deleting previous lab environment..."
cd lab && sudo kathara lclean && cd .. > /dev/null 2>&1 || true
sudo rm -rf lab
sudo rm -rf "$SNIPPET_DIR"
sudo rm -f "$GRAPH_DATA_FILE" "$LIGHT_SENSOR_MAP_FILE" "$CLUSTER_MAP_FILE"

# 2. Generate Configs, Snippets, Maps
echo "Running automation.py..."
AUTOMATION_OUTPUT=$(sudo python3 automation.py | tee /dev/tty)
AUTOMATION_EXIT_CODE=${PIPESTATUS[0]}
if [ $AUTOMATION_EXIT_CODE -ne 0 ]; then echo "[ERROR] automation.py failed." >&2; exit 1; fi
NUM_ROUTERS=$(echo "$AUTOMATION_OUTPUT" | grep '^ROUTERS_GENERATED=' | cut -d'=' -f2)
if ! [[ "$NUM_ROUTERS" =~ ^[1-9][0-9]*$ ]]; then echo "[ERROR] Bad router count." >&2; exit 1; fi
echo "--- Detected $NUM_ROUTERS routers ---"
echo "$AUTOMATION_OUTPUT" > automation_output.log
echo "Saved automation script output to automation_output.log"
# 3. Process lab.confu using Tacata
echo "Running tacata.py..."
sudo python3 tacata.py -f -v
if [ $? -ne 0 ]; then echo "[ERROR] tacata.py failed." >&2; exit 1; fi

# 4. Move Data Files to Shared Directory
SHARED_DIR="lab/shared"
echo "Attempting to move data files to $SHARED_DIR..."
sudo mkdir -p "$SHARED_DIR"
# Graph for Server
if [ -f "$GRAPH_DATA_FILE" ]; then sudo mv "$GRAPH_DATA_FILE" "$SHARED_DIR/" || echo "[WARN] Failed mv $GRAPH_DATA_FILE."; else echo "[WARN] $GRAPH_DATA_FILE not found."; fi
# Light map for Lights
if [ -f "$LIGHT_SENSOR_MAP_FILE" ]; then sudo mv "$LIGHT_SENSOR_MAP_FILE" "$SHARED_DIR/" || echo "[WARN] Failed mv $LIGHT_SENSOR_MAP_FILE."; else echo "[WARN] $LIGHT_SENSOR_MAP_FILE not found."; fi
# Cluster map for Server (if needed by server's /traffic endpoint)
if [ -f "$CLUSTER_MAP_FILE" ]; then sudo mv "$CLUSTER_MAP_FILE" "$SHARED_DIR/" || echo "[WARN] Failed mv $CLUSTER_MAP_FILE."; else echo "[WARN] $CLUSTER_MAP_FILE not found."; fi


# 5. Configure Routers
echo "Configuring routers..."
# (Router config loop remains the same)
for i in $(seq 1 $NUM_ROUTERS); do
    ROUTER_STARTUP_FILE="lab/router${i}.startup"; if [ -f "$ROUTER_STARTUP_FILE" ]; then
        TMP_CMDS=$(mktemp); cat << EOF > "$TMP_CMDS"
# Router $i FRR Setup
mkdir -p /etc/quagga; touch /etc/quagga/zebra.conf /etc/quagga/ripd.conf; chown root:frr /etc/quagga; chmod 775 /etc/quagga; chown root:frr /etc/quagga/*.conf; chmod 640 /etc/quagga/*.conf
sed -i 's/^ripd=no/ripd=yes/' /etc/frr/daemons; sed -i 's/^zebra=no/zebra=yes/' /etc/frr/daemons; sed -i 's#^ripd_options=.*#\#&#' /etc/frr/daemons; sed -i 's#^zebra_options=.*#\#&#' /etc/frr/daemons
grep -qxF 'ripd=yes' /etc/frr/daemons || echo 'ripd=yes' >> /etc/frr/daemons; grep -qxF 'zebra=yes' /etc/frr/daemons || echo 'zebra=yes' >> /etc/frr/daemons
grep -qxF 'zebra_options=" --daemon -f /etc/quagga/zebra.conf"' /etc/frr/daemons || echo 'zebra_options=" --daemon -f /etc/quagga/zebra.conf"' >> /etc/frr/daemons
grep -qxF 'ripd_options=" --daemon -f /etc/quagga/ripd.conf"' /etc/frr/daemons || echo 'ripd_options=" --daemon -f /etc/quagga/ripd.conf"' >> /etc/frr/daemons
if [ -x /usr/lib/frr/frrinit.sh ]; then /usr/lib/frr/frrinit.sh start; else echo "[ERROR] FRR init script not found." >&2; fi
EOF
        sudo tee -a "$ROUTER_STARTUP_FILE" < "$TMP_CMDS" > /dev/null; rm "$TMP_CMDS"; fi; done
echo "Router config done."

# 6. Configure Clients AND Traffic Lights from Snippets
echo "Configuring clients/lights from $SNIPPET_DIR..."
# This loop processes *.cmds for both clients and lights
CMD_FILES_FOUND=$(find "$SNIPPET_DIR" -maxdepth 1 -name '*.cmds' -print)
if [ -z "$CMD_FILES_FOUND" ]; then echo "[WARN] No *.cmds files found."; else
for CMD_FILE in $CMD_FILES_FOUND; do MACHINE_NAME=$(basename "$CMD_FILE" .cmds); TARGET_STARTUP_FILE="lab/${MACHINE_NAME}.startup"; if [ -f "$TARGET_STARTUP_FILE" ]; then sudo tee -a "$TARGET_STARTUP_FILE" < "$CMD_FILE" > /dev/null; if [ $? -ne 0 ]; then echo "[ERROR] Failed appending to $TARGET_STARTUP_FILE."; else sudo rm "$CMD_FILE"; fi; else echo "[WARN] Target startup file not found: $TARGET_STARTUP_FILE."; fi; done; fi
if [ -d "$SNIPPET_DIR" ]; then if [ -z "$(ls -A $SNIPPET_DIR)" ]; then sudo rm -r "$SNIPPET_DIR"; else echo "[WARN] $SNIPPET_DIR not empty."; fi; fi
echo "Client/Light config done."

# 7. Start Lab
echo "Changing directory to 'lab'..."; cd lab || exit 1
echo "Running kathara lstart silently..."; sudo kathara lstart --noterminals
if [ $? -ne 0 ]; then echo "[ERROR] 'kathara lstart' failed."; exit 1; fi

# --- Final Message ---
echo "--------------------------------------"
echo "Kathara lab started successfully!"
echo "Connect using 'kathara connect <device>'"
echo "Setup finished."
echo "--------------------------------------"
exit 0
