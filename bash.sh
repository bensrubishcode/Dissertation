#!/bin/bash

# --- Configuration ---
# Constants from your provided script
DEFAULT_NUM_NODES=20
DEFAULT_DENSITY_FACTOR=0.3
DEFAULT_GRAPH_SEED="random"
DEFAULT_REFRESH_ML_ON_FIRST_RUN_ONLY=true # This was a default, actual refresh logic below
DEFAULT_LOOP_MODE=false
DEFAULT_MAX_ITERATIONS=5
DEFAULT_SIMULATION_DURATION_SECONDS=180
DEFAULT_MIN_IMPROVEMENT_THRESHOLD=0.01
DEFAULT_NO_IMPROVEMENT_PATIENCE=2

# --- File/Directory Names (Constants) ---
PROJECT_ROOT_DIR=$(pwd) # Assumes script is run from project root

# Names of files/dirs automation.py might create in PROJECT_ROOT_DIR
SNIPPET_DIR_NAME="cmd_snippets" # Created by automation.py
GRAPH_DATA_FILE_NAME="graph_structure.json"
LIGHT_SENSOR_MAP_FILE_NAME="light_sensor_map.json"
CLUSTER_MAP_FILE_NAME="cluster_edge_map.json"
LAB_CONF_U_FILE_NAME="lab.confu" # Created by automation.py in PROJECT_ROOT_DIR

LAB_DIR_NAME="lab" # The actual lab directory, created by tacata.py inside PROJECT_ROOT_DIR
LAB_DIR_ABS_PATH="${PROJECT_ROOT_DIR}/${LAB_DIR_NAME}"

# ML Model related filenames (likely in PROJECT_ROOT_DIR)
INITIAL_TRUST_MODEL_FILENAME="initial_trust_predictor_model.joblib"
INITIAL_TRUST_PREPROCESSOR_FILENAME="initial_trust_preprocessor.joblib"

# Run info and results
RUN_INFO_DIR_NAME="run_info"
RUN_INFO_DIR_ABS_PATH="${PROJECT_ROOT_DIR}/${RUN_INFO_DIR_NAME}"
AUTOMATION_SENSOR_LOG_PREFIX="automation_sensor_features_initial_trust_run"

# Kathara specific paths relative to LAB_DIR_ABS_PATH
KATHARA_SHARED_BASE_REL_PATH="shared"
KATHARA_SHARED_RESULTS_REL_PATH="${KATHARA_SHARED_BASE_REL_PATH}/results"
KATHARA_SIGNAL_FILE_REL_PATH="${KATHARA_SHARED_BASE_REL_PATH}/SIMULATION_ENDING_PLEASE_REPORT"

# Host paths for collecting results
TL_RESULTS_DIR_HOST_RUN_PREFIX="${RUN_INFO_DIR_NAME}/tl_results_run" # Relative to PROJECT_ROOT_DIR
AGGREGATED_ML_TRAINING_DATA_CSV_NAME="ml_feedback_training_data.csv" # In PROJECT_ROOT_DIR

PYTHON_EXECUTABLE="python3"
KATHARA_CLEAN_TIMEOUT_SECONDS=30 # For the timeout command with kathara lclean

# --- State (parsed from args or defaults) ---
NUM_NODES=$DEFAULT_NUM_NODES
DENSITY_FACTOR=$DEFAULT_DENSITY_FACTOR
GRAPH_SEED=$DEFAULT_GRAPH_SEED
LOOP_MODE=$DEFAULT_LOOP_MODE
MAX_ITERATIONS=$DEFAULT_MAX_ITERATIONS
SIMULATION_DURATION_SECONDS=$DEFAULT_SIMULATION_DURATION_SECONDS
REFRESH_ML_MODELS_GLOBALLY_FLAG=false # Set by --refresh-ml
PERSISTENT_MODE=0 # For our -p flag, not in original script but kept for debugging

# --- Logging Function ---
log_message() {
    echo "[$(date --iso-8601=seconds)] [$1] $2"
}

# --- Helper for running commands with sudo and logging ---
# These helpers prepend sudo
run_command_in_dir_with_log() {
    local dir="$1"
    shift
    log_message "CMD" "Executing in ${dir}: sudo $@"
    if (cd "${dir}" && sudo "$@"); then
        log_message "SUCCESS" "Command successful in ${dir}: sudo $@"
        return 0
    else
        log_message "ERROR" "Command failed in ${dir}: sudo $@"
        return 1
    fi
}

# Modified to allow capturing output for specific cases like automation.py
run_python_script_with_log() {
    local capture_output_var_name="$1" # Pass variable name to store output, or "NOCAP"
    shift
    local script_path="$1"
    shift
    local full_command="sudo $PYTHON_EXECUTABLE $script_path $@"

    log_message "CMD" "Executing Python script: $full_command"
    
    # Execute and capture output and error
    output_and_error=$(eval "$full_command" 2>&1)
    local exit_code=$?

    if [ "$capture_output_var_name" != "NOCAP" ]; then
        eval "$capture_output_var_name=\"\$output_and_error\""
    fi
    
    if [ $exit_code -eq 0 ]; then
        log_message "SUCCESS" "Python script successful: $full_command"
        # Optionally print captured output if needed for general logging, even on success
        # if [ "$capture_output_var_name" = "NOCAP" ]; then echo "$output_and_error"; fi
        return 0
    else
        log_message "ERROR" "Python script failed (Code: $exit_code): $full_command"
        log_message "PY_OUTPUT" "$output_and_error" # Always log output on error
        return $exit_code
    fi
}


# --- Argument Parsing (Adapted from your old script) ---
show_help() {
    echo "Usage: $0 [options]"
    echo "Options:"
    echo "  -n, --nodes <num>         Number of nodes for graph generation. Default: $DEFAULT_NUM_NODES"
    echo "  -d, --density <float>     Density factor for graph generation. Default: $DEFAULT_DENSITY_FACTOR"
    echo "  -s, --seed <int|random>   Seed for graph generation. Default: $DEFAULT_GRAPH_SEED"
    echo "  --loop                    Enable iterative ML training loop."
    echo "  --max-iter <num>          Maximum iterations for loop mode. Default: $DEFAULT_MAX_ITERATIONS (if loop)"
    echo "  --sim-duration <secs>     Duration of Kathara lab simulation. Default: $DEFAULT_SIMULATION_DURATION_SECONDS"
    echo "  --refresh-ml              Force refresh of ALL ML models & training data, overriding other logic."
    echo "  -p, --persistent          (Debug) Leave Kathara lab running after simulation."
    echo "  -h, --help                Show this help message."
    exit 0
}

while [[ "$#" -gt 0 ]]; do
    case $1 in
        -n|--nodes) NUM_NODES="$2"; shift ;;
        -d|--density) DENSITY_FACTOR="$2"; shift ;;
        -s|--seed) GRAPH_SEED="$2"; shift ;;
        --loop) LOOP_MODE=true ;;
        --max-iter) MAX_ITERATIONS="$2"; shift ;;
        --sim-duration) SIMULATION_DURATION_SECONDS="$2"; shift ;;
        --refresh-ml) REFRESH_ML_MODELS_GLOBALLY_FLAG=true ;;
        -p|--persistent) PERSISTENT_MODE=1 ;; # Kept for debugging
        -h|--help) show_help ;;
        *) log_message "ERROR" "Unknown parameter: $1"; show_help; exit 1 ;;
    esac
    shift
done

# --- Main Orchestration ---
log_message "INFO" ">>> Initializing Simulation Orchestrator (Project Root: ${PROJECT_ROOT_DIR}) <<<"
log_message "INFO" "Parameters: Nodes=${NUM_NODES}, Density=${DENSITY_FACTOR}, Seed=${GRAPH_SEED}, Loop=${LOOP_MODE}, MaxIter=${MAX_ITERATIONS}, SimDuration=${SIMULATION_DURATION_SECONDS}s"

# --- Global ML Data Refresh Logic (from your old script) ---
REFRESH_THIS_TIME=false
if [ "$REFRESH_ML_MODELS_GLOBALLY_FLAG" = true ]; then
    REFRESH_THIS_TIME=true
    log_message "INFO" "--refresh-ml flag set. ML models and training data will be refreshed."
elif [ "$LOOP_MODE" = true ] && [ "$DEFAULT_REFRESH_ML_ON_FIRST_RUN_ONLY" = true ]; then
    if [ ! -f "${PROJECT_ROOT_DIR}/${INITIAL_TRUST_MODEL_FILENAME}" ] || [ ! -f "${PROJECT_ROOT_DIR}/${INITIAL_TRUST_PREPROCESSOR_FILENAME}" ]; then
        REFRESH_THIS_TIME=true
        log_message "INFO" "Loop mode on first run (or model missing). ML models and training data will be refreshed."
    fi
fi

if [ "$REFRESH_THIS_TIME" = true ]; then
    log_message "INFO" "Performing refresh of ML models and training data..."
    sudo rm -f \
        "${PROJECT_ROOT_DIR}/${INITIAL_TRUST_MODEL_FILENAME}" \
        "${PROJECT_ROOT_DIR}/${INITIAL_TRUST_PREPROCESSOR_FILENAME}" \
        "${PROJECT_ROOT_DIR}/ml_risk_assessor.pyc" \
        "${PROJECT_ROOT_DIR}/inherent_reliability_model.joblib" \
        "${PROJECT_ROOT_DIR}/configured_noisy_model.joblib" \
        "${PROJECT_ROOT_DIR}/ml_preprocessor.joblib" \
        "${PROJECT_ROOT_DIR}/${AGGREGATED_ML_TRAINING_DATA_CSV_NAME}" \
        "${PROJECT_ROOT_DIR}/ml_training_data.csv" # Old name?
    sudo find "${PROJECT_ROOT_DIR}" -name "ml_risk_assessor.cpython-*.pyc" -print -delete
    sudo find "${PROJECT_ROOT_DIR}" -path "*/__pycache__" -type d -exec rm -rf {} +
    log_message "INFO" "ML data and models refreshed."
else
    log_message "INFO" "Skipping global ML data and model refresh for this run."
fi

sudo mkdir -p "${RUN_INFO_DIR_ABS_PATH}"

# --- Main Loop (Adapted from your old script) ---
current_iteration=1
# Variables for loop mode, not fully implemented here but kept structure
# max_overall_success_ratio_achieved=-1.0
# no_improvement_streak_count=0

while true; do # Loop control is at the end
    log_message "INFO" "----------------------------------------------------"
    log_message "INFO" ">>> Starting Iteration: $current_iteration <<<"
    log_message "INFO" "----------------------------------------------------"

    RUN_SPECIFIC_AUTOMATION_LOG_PATH="${RUN_INFO_DIR_ABS_PATH}/${AUTOMATION_SENSOR_LOG_PREFIX}_${current_iteration}.json"
    RUN_SPECIFIC_TL_RESULTS_HOST_DIR_PATH="${PROJECT_ROOT_DIR}/${TL_RESULTS_DIR_HOST_RUN_PREFIX}_${current_iteration}"

    # --- Step 1: Cleaning environment for iteration ---
    log_message "INFO" ">>> Step 1: Cleaning environment for iteration $current_iteration..."
    if [ -d "$LAB_DIR_ABS_PATH" ]; then
        log_message "INFO" "Found existing 'lab' directory at '$LAB_DIR_ABS_PATH'. Attempting to clean..."
        log_message "CMD" "Running 'sudo timeout $KATHARA_CLEAN_TIMEOUT_SECONDS sudo kathara lclean' in $LAB_DIR_ABS_PATH"
        if (cd "$LAB_DIR_ABS_PATH" && sudo timeout "$KATHARA_CLEAN_TIMEOUT_SECONDS" sudo kathara lclean); then
            log_message "INFO" "'kathara lclean' completed."
        else
            LCEAN_EXIT_CODE=$?
            if [ $LCEAN_EXIT_CODE -eq 124 ]; then log_message "WARN" "'kathara lclean' timed out."; else log_message "WARN" "'kathara lclean' exited code $LCEAN_EXIT_CODE."; fi
        fi
    else
        log_message "INFO" "'$LAB_DIR_ABS_PATH' directory not found. Skipping lclean for it."
    fi
    log_message "INFO" "Forcefully removing '$LAB_DIR_ABS_PATH' directory..."
    sudo rm -rf "$LAB_DIR_ABS_PATH"

    log_message "INFO" "Cleaning project root from previous automation/tacata outputs..."
    sudo rm -f "${PROJECT_ROOT_DIR}/${LAB_CONF_U_FILE_NAME}" \
                 "${PROJECT_ROOT_DIR}/${GRAPH_DATA_FILE_NAME}" \
                 "${PROJECT_ROOT_DIR}/${LIGHT_SENSOR_MAP_FILE_NAME}" \
                 "${PROJECT_ROOT_DIR}/${CLUSTER_MAP_FILE_NAME}"
    sudo rm -rf "${PROJECT_ROOT_DIR}/${SNIPPET_DIR_NAME}"

    log_message "INFO" "Pruning unused Docker containers and networks..."
    sudo docker container prune -f >/dev/null 2>&1
    sudo docker network prune -f >/dev/null 2>&1
    log_message "INFO" "Docker prune attempt complete."

    sudo mkdir -p "$RUN_SPECIFIC_TL_RESULTS_HOST_DIR_PATH"

    # --- Step 2: Running automation.py ---
    log_message "INFO" ">>> Step 2: Running automation.py (Iteration $current_iteration)..."
    automation_py_path="${PROJECT_ROOT_DIR}/automation.py"
    AUTOMATION_OUTPUT="" # Initialize variable to capture output
    if ! run_python_script_with_log "AUTOMATION_OUTPUT" "${automation_py_path}" \
        --nodes "$NUM_NODES" --density "$DENSITY_FACTOR" --seed "$GRAPH_SEED" \
        --output-sensor-log "$RUN_SPECIFIC_AUTOMATION_LOG_PATH"; then
        log_message "ERROR" "automation.py failed. Aborting iteration." >&2
        if [ "$LOOP_MODE" = true ]; then current_iteration=$((current_iteration + 1)); continue; else exit 1; fi
    fi
    
    # Parse ROUTERS_GENERATED from automation.py output
    NUM_ROUTERS_OR_CLUSTERS=$(echo "$AUTOMATION_OUTPUT" | grep '^ROUTERS_GENERATED=' | cut -d'=' -f2)
    if ! [[ "$NUM_ROUTERS_OR_CLUSTERS" =~ ^[0-9]+$ ]]; then 
        log_message "WARN" "Could not parse ROUTERS_GENERATED from automation.py output. Assuming 0."
        NUM_ROUTERS_OR_CLUSTERS=0
    else
        log_message "INFO" "automation.py reported ROUTERS_GENERATED=${NUM_ROUTERS_OR_CLUSTERS}"
    fi
    log_message "INFO" "automation.py execution complete."

    log_message "INFO" "Checking for automation log file: $RUN_SPECIFIC_AUTOMATION_LOG_PATH"
    if [ ! -s "$RUN_SPECIFIC_AUTOMATION_LOG_PATH" ]; then
        log_message "ERROR" "Automation log file was NOT created or is empty. Aborting iteration." >&2
        if [ "$LOOP_MODE" = true ]; then current_iteration=$((current_iteration + 1)); continue; else exit 1; fi
    fi
    if [ ! -f "${PROJECT_ROOT_DIR}/${LAB_CONF_U_FILE_NAME}" ]; then
        log_message "ERROR" "${LAB_CONF_U_FILE_NAME} not found in ${PROJECT_ROOT_DIR}. Aborting iteration." >&2
        if [ "$LOOP_MODE" = true ]; then current_iteration=$((current_iteration + 1)); continue; else exit 1; fi
    fi

    # --- Step 3: Running tacata.py ---
    log_message "INFO" ">>> Step 3: Running tacata.py (Iteration $current_iteration)..."
    tacata_py_path="${PROJECT_ROOT_DIR}/tacata.py"
    if ! run_python_script_with_log "NOCAP" "${tacata_py_path}" -f -v --dir "$PROJECT_ROOT_DIR"; then
        log_message "ERROR" "tacata.py failed. Aborting iteration." >&2
        if [ "$LOOP_MODE" = true ]; then current_iteration=$((current_iteration + 1)); continue; else exit 1; fi
    fi
    log_message "INFO" "Tacata processing complete."

    if [ ! -d "$LAB_DIR_ABS_PATH" ] || [ ! -f "${LAB_DIR_ABS_PATH}/lab.conf" ]; then
        log_message "ERROR" "Lab directory or lab.conf not created by tacata.py. Aborting iteration." >&2
        if [ "$LOOP_MODE" = true ]; then current_iteration=$((current_iteration + 1)); continue; else exit 1; fi
    fi

    # --- Step 4: Staging files and augmenting scripts ---
    log_message "INFO" ">>> Step 4: Staging files and augmenting scripts (Iteration $current_iteration)..."
    sudo mkdir -p "${LAB_DIR_ABS_PATH}/${KATHARA_SHARED_BASE_REL_PATH}"
    sudo mkdir -p "${LAB_DIR_ABS_PATH}/${KATHARA_SHARED_RESULTS_REL_PATH}"
    sudo chmod -R 777 "${LAB_DIR_ABS_PATH}/${KATHARA_SHARED_BASE_REL_PATH}"

    move_if_exists_host_to_lab_shared() {
        local host_file_name="$1"
        local host_file_path="${PROJECT_ROOT_DIR}/${host_file_name}"
        local dest_kathara_shared_dir="${LAB_DIR_ABS_PATH}/${KATHARA_SHARED_BASE_REL_PATH}"
        if [ -f "$host_file_path" ]; then
            log_message "INFO" "Moving '$host_file_path' to '$dest_kathara_shared_dir/'"
            sudo mv "$host_file_path" "${dest_kathara_shared_dir}/" || log_message "WARN" "Failed to move $host_file_path"
        fi
    }
    move_if_exists_host_to_lab_shared "$GRAPH_DATA_FILE_NAME"
    move_if_exists_host_to_lab_shared "$LIGHT_SENSOR_MAP_FILE_NAME"
    move_if_exists_host_to_lab_shared "$CLUSTER_MAP_FILE_NAME"

    # FRR Setup for routers (re-integrated from old script)
    if [[ "$NUM_ROUTERS_OR_CLUSTERS" -gt 0 ]]; then
        log_message "INFO" "Augmenting $NUM_ROUTERS_OR_CLUSTERS router startup scripts with FRR configuration..."
        for i in $(seq 1 $NUM_ROUTERS_OR_CLUSTERS); do
            ROUTER_STARTUP_FILE="${LAB_DIR_ABS_PATH}/router${i}.startup"
            ROUTER_NAME="router${i}"
            if [ -f "$ROUTER_STARTUP_FILE" ]; then
                log_message "INFO" "Appending FRR setup to $ROUTER_STARTUP_FILE for $ROUTER_NAME"
                # Using a heredoc for readability
                sudo tee -a "$ROUTER_STARTUP_FILE" > /dev/null << EOF

# --- FRR Setup for $ROUTER_NAME - Appended by bash.sh ---
echo ">>> $ROUTER_NAME: Starting FRR Setup sequence..."
mkdir -p /var/run/frr /var/log/frr /etc/frr
chown -R frr:frr /var/run/frr /var/log/frr /etc/frr
chmod -R u+rwx,g+rwx /var/run/frr /var/log/frr
chmod 755 /etc/frr
echo "Directories created and permissions set for FRR on $ROUTER_NAME."

# Create daemons configuration
cat << EOL_DAEMONS > /etc/frr/daemons
zebra=yes
ripd=yes
bgpd=no
ospfd=no
ospf6d=no
isisd=no
pimd=no
ldpd=no
nhrpd=no
EOL_DAEMONS
echo "/etc/frr/daemons created for $ROUTER_NAME:"
cat /etc/frr/daemons

# Create zebra.conf
cat << EOL_ZEBRA > /etc/frr/zebra.conf
hostname $ROUTER_NAME
password zebra
enable password zebra
log file /var/log/frr/zebra.log debugging
! interface eth0 will be configured by Kathara
! interface eth1 will be configured by Kathara
interface lo
line vty
EOL_ZEBRA
echo "/etc/frr/zebra.conf created for $ROUTER_NAME."

# Create ripd.conf (Networks will be advertised by Tacata's rip() command in lab.conf)
cat << EOL_RIPD > /etc/frr/ripd.conf
hostname $ROUTER_NAME
password zebra
enable password zebra
router rip
  redistribute connected
  ! network statements are typically added by Kathara/Tacata based on lab.conf
log file /var/log/frr/ripd.log debugging
line vty
EOL_RIPD
echo "/etc/frr/ripd.conf created for $ROUTER_NAME."

chown frr:frr /etc/frr/*.conf
chmod 640 /etc/frr/*.conf
echo "FRR config file permissions set for $ROUTER_NAME."

echo "Enabling IP forwarding on $ROUTER_NAME..."
echo 1 > /proc/sys/net/ipv4/ip_forward
echo "IP forwarding status: \$(cat /proc/sys/net/ipv4/ip_forward)"

echo "Starting FRR daemons on $ROUTER_NAME..."
if [ -x /usr/lib/frr/frrinit.sh ]; then
    /usr/lib/frr/frrinit.sh start
    FRR_START_STATUS=\$?
    echo "FRR init script executed with status: \$FRR_START_STATUS for $ROUTER_NAME."
    sleep 2 
    ps aux | grep -E 'frr|zebra|ripd' | grep -v grep || echo "No FRR processes found for $ROUTER_NAME after start attempt."
    echo "Checking FRR status with vtysh for $ROUTER_NAME..."
    vtysh -c "show version" || echo "vtysh 'show version' failed for $ROUTER_NAME."
    vtysh -c "show ip rip status" || echo "vtysh 'show ip rip status' failed for $ROUTER_NAME."
else
    echo "[ERROR] FRR init script /usr/lib/frr/frrinit.sh not found on $ROUTER_NAME."
fi
echo ">>> $ROUTER_NAME: FRR Setup sequence finished."
# --- End FRR Setup for $ROUTER_NAME ---
EOF
            else
                 log_message "WARN" "Router startup file $ROUTER_STARTUP_FILE not found for FRR setup."
            fi
        done
    else
        log_message "INFO" "NUM_ROUTERS_OR_CLUSTERS is 0 or not set. Skipping FRR setup loop."
    fi

    # Append .cmds files to .startup files
    snippet_dir_abs_path="${PROJECT_ROOT_DIR}/${SNIPPET_DIR_NAME}"
    if [ -d "$snippet_dir_abs_path" ]; then
        CMD_FILES_FOUND=$(find "$snippet_dir_abs_path" -maxdepth 1 -name '*.cmds' -print)
        if [ -n "$CMD_FILES_FOUND" ]; then
            for CMD_FILE in $CMD_FILES_FOUND; do
                MACHINE_NAME=$(basename "$CMD_FILE" .cmds)
                TARGET_STARTUP_FILE="${LAB_DIR_ABS_PATH}/${MACHINE_NAME}.startup"
                if [ -f "$TARGET_STARTUP_FILE" ]; then
                    log_message "INFO" "Appending '$CMD_FILE' to '$TARGET_STARTUP_FILE'"
                    sudo sh -c "cat '$CMD_FILE' >> '$TARGET_STARTUP_FILE'"
                else
                    log_message "WARN" "Target startup file '$TARGET_STARTUP_FILE' not found for snippet '$CMD_FILE'."
                fi
            done
        fi
    else
        log_message "WARN" "Snippet directory '$snippet_dir_abs_path' not found. Skipping snippet augmentation."
    fi
    log_message "INFO" "File staging and script augmentation complete."

    # --- Step 5: Starting Kathara lab ---
    log_message "INFO" ">>> Step 5: Starting Kathara lab for $SIMULATION_DURATION_SECONDS seconds (Iteration $current_iteration)..."
    log_message "CMD" "Running 'sudo timeout $SIMULATION_DURATION_SECONDS sudo kathara lstart --noterminals' in $LAB_DIR_ABS_PATH"
    if (cd "$LAB_DIR_ABS_PATH" && sudo timeout "$SIMULATION_DURATION_SECONDS" sudo kathara lstart --noterminals); then
        log_message "INFO" "Kathara lab completed."
    else
        KATHARA_LSTART_EXIT_CODE=$?
        if [ $KATHARA_LSTART_EXIT_CODE -eq 124 ]; then log_message "INFO" "Kathara lab timed out (as expected)."; else log_message "WARN" "'kathara lstart' exited code $KATHARA_LSTART_EXIT_CODE."; fi
    fi

    # --- Step 6: Signaling Traffic Lights and Cleaning Kathara lab ---
    log_message "INFO" ">>> Step 6: Signaling Traffic Lights and Cleaning Kathara lab (Iteration $current_iteration)..."
    SIGNAL_FILE_IN_LAB_ABS_PATH="${LAB_DIR_ABS_PATH}/${KATHARA_SIGNAL_FILE_REL_PATH}"
    if [ -d "${LAB_DIR_ABS_PATH}/${KATHARA_SHARED_BASE_REL_PATH}" ]; then
        sudo touch "$SIGNAL_FILE_IN_LAB_ABS_PATH"
        if [ $? -eq 0 ]; then log_message "INFO" "Signal file created. Waiting 5s for TLs..."; sleep 5; else log_message "ERROR" "Failed to create signal file."; fi
    else
        log_message "WARN" "Shared directory for signal file not found. Skipping signal."
    fi

    if [ "$PERSISTENT_MODE" -eq 1 ]; then
        log_message "INFO" "Persistent mode enabled. SKIPPING Kathara lab clean."
    else
        log_message "INFO" "Cleaning Kathara lab (CWD: $LAB_DIR_ABS_PATH)..."
        if (cd "$LAB_DIR_ABS_PATH" && sudo kathara lclean); then
             log_message "INFO" "Kathara lab clean complete."
        else
             log_message "WARN" "kathara lclean failed or lab dir was not proper."
        fi
    fi
    if [ -f "$SIGNAL_FILE_IN_LAB_ABS_PATH" ]; then sudo rm "$SIGNAL_FILE_IN_LAB_ABS_PATH"; fi

    # --- Step 7: Collecting TL performance data ---
    log_message "INFO" ">>> Step 7: Collecting TL performance data (Iteration $current_iteration)..."
    SOURCE_TL_RESULTS_ABS_PATH="${LAB_DIR_ABS_PATH}/${KATHARA_SHARED_RESULTS_REL_PATH}"
    if [ -d "$SOURCE_TL_RESULTS_ABS_PATH" ]; then
        if sudo find "$SOURCE_TL_RESULTS_ABS_PATH" -maxdepth 1 -name 'tl_*.json' -print -quit | grep -q .; then
            log_message "INFO" "TL result files found. Copying..."
            sudo cp -r "${SOURCE_TL_RESULTS_ABS_PATH}/"* "$RUN_SPECIFIC_TL_RESULTS_HOST_DIR_PATH/"
            sudo chown -R "$(whoami):$(whoami)" "$RUN_SPECIFIC_TL_RESULTS_HOST_DIR_PATH"
            log_message "INFO" "TL performance files copied."
            ls -l "$RUN_SPECIFIC_TL_RESULTS_HOST_DIR_PATH"
        else
            log_message "INFO" "No 'tl_*.json' files found in $SOURCE_TL_RESULTS_ABS_PATH."
        fi
    else
        log_message "WARN" "Source TL results directory '$SOURCE_TL_RESULTS_ABS_PATH' not found."
    fi

    # --- Step 8: Preparing ML feedback training data ---
    log_message "INFO" ">>> Step 8: Preparing ML feedback training data (Iteration $current_iteration)..."
    prepare_ml_feedback_py_path="${PROJECT_ROOT_DIR}/prepare_ml_feedback.py"
    if [ ! -f "$prepare_ml_feedback_py_path" ]; then
        log_message "ERROR" "prepare_ml_feedback.py not found. Aborting." >&2
        if [ "$LOOP_MODE" = true ]; then current_iteration=$((current_iteration + 1)); continue; else exit 1; fi
    fi
    if [ ! -s "$RUN_SPECIFIC_AUTOMATION_LOG_PATH" ]; then
         log_message "ERROR" "Automation sensor log is empty or missing. Cannot prepare ML feedback." >&2
    else
        if ! run_python_script_with_log "NOCAP" "$prepare_ml_feedback_py_path" \
            --automation-log "$RUN_SPECIFIC_AUTOMATION_LOG_PATH" \
            --tl-results-dir "$RUN_SPECIFIC_TL_RESULTS_HOST_DIR_PATH" \
            --output-csv "${PROJECT_ROOT_DIR}/${AGGREGATED_ML_TRAINING_DATA_CSV_NAME}"; then
            log_message "ERROR" "prepare_ml_feedback.py failed. Aborting iteration." >&2
            if [ "$LOOP_MODE" = true ]; then current_iteration=$((current_iteration + 1)); continue; else exit 1; fi
        fi
        log_message "INFO" "ML feedback training data preparation complete."
    fi

    # --- Step 9: Retraining Initial Trust Predictor ML Model ---
    log_message "INFO" ">>> Step 9: Retraining Initial Trust Predictor ML Model (Iteration $current_iteration)..."
    train_ml_model_py_path="${PROJECT_ROOT_DIR}/train_ml_model.py"
    if [ -f "$train_ml_model_py_path" ]; then
        if [ ! -s "${PROJECT_ROOT_DIR}/${AGGREGATED_ML_TRAINING_DATA_CSV_NAME}" ]; then
            log_message "WARN" "Aggregated ML training data CSV is empty or missing. Skipping model training."
        else
            if run_python_script_with_log "NOCAP" "$train_ml_model_py_path" \
                --data-file "${PROJECT_ROOT_DIR}/${AGGREGATED_ML_TRAINING_DATA_CSV_NAME}" \
                --model-type "InitialTrustPredictor"; then
                log_message "INFO" "InitialTrustPredictor ML Model retraining complete."
                if [ -f "${PROJECT_ROOT_DIR}/${INITIAL_TRUST_MODEL_FILENAME}" ] && [ -f "${PROJECT_ROOT_DIR}/${INITIAL_TRUST_PREPROCESSOR_FILENAME}" ]; then
                    log_message "INFO" "Model files created/updated."
                else
                    log_message "WARN" "Model files NOT created/updated after training script."
                fi
            else
                log_message "WARN" "train_ml_model.py (InitialTrustPredictor) failed."
            fi
        fi
    else
        log_message "WARN" "train_ml_model.py not found. Skipping retraining."
    fi

    # --- Step 10: Check loop termination ---
    if [ "$LOOP_MODE" = true ]; then
        log_message "INFO" "Loop mode enabled, but termination logic is placeholder. Breaking after one iter for now."
        break 
    else
        log_message "INFO" "Loop mode not enabled. Ending after one iteration."
        break 
    fi
    current_iteration=$((current_iteration + 1))
done

log_message "INFO" "----------------------------------------------------"
log_message "INFO" "Orchestration Complete."
log_message "INFO" "----------------------------------------------------"
exit 0
