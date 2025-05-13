#!/usr/bin/env python3
import json
import os
import csv
import argparse
import glob
import numpy as np

# Define the headers for the output CSV file
# These should match the STATIC_FEATURES used by your InitialTrustPredictor ML model
# plus the new target variable.
# Example: manufacturer,software_version,is_signed,software_age_years,device_age_years,target_initial_trust
OUTPUT_CSV_HEADER = [
    "manufacturer", "software_version", "is_signed",
    "software_age_years", "device_age_years",
    "target_initial_trust" # This will be derived from TL success ratio
]

# These must match the keys in the 'static_features' dictionary logged by automation.py
# and also the order in OUTPUT_CSV_HEADER (excluding the target)
ORDERED_STATIC_FEATURES = [
    "manufacturer", "software_version", "is_signed",
    "software_age_years", "device_age_years"
]


def main(args):
    print("--- Starting ML Feedback Data Preparation ---")
    automation_log_data = []
    if os.path.exists(args.automation_log):
        try:
            with open(args.automation_log, 'r') as f:
                automation_log_data = json.load(f) # List of sensor dicts
            print(f"Loaded {len(automation_log_data)} sensor records from automation log: {args.automation_log}")
        except json.JSONDecodeError:
            print(f"Error: Could not decode JSON from automation log: {args.automation_log}")
            return
        except IOError:
            print(f"Error: Could not read automation log: {args.automation_log}")
            return
    else:
        print(f"Warning: Automation sensor log not found: {args.automation_log}. Cannot map static features.")
        return # Exit if this crucial file is missing

    # Create a lookup map from sensor_ip to its static_features and assigned_initial_trust
    sensor_ip_to_details_map = {}
    for sensor_record in automation_log_data:
        ip = sensor_record.get("ip")
        features = sensor_record.get("static_features")
        assigned_trust = sensor_record.get("assigned_initial_trust")
        if ip and features is not None and assigned_trust is not None:
            sensor_ip_to_details_map[ip] = {
                "static_features": features,
                "assigned_initial_trust_this_run": assigned_trust # Trust given by ML in *this* run
            }

    if not sensor_ip_to_details_map:
        print("No sensor details loaded from automation log. Cannot proceed.")
        return

    new_training_rows = []
    tl_result_files = glob.glob(os.path.join(args.tl_results_dir, "tl_*.json"))
    print(f"Found {len(tl_result_files)} traffic light result files in {args.tl_results_dir}")

    if not tl_result_files:
        print("No traffic light result files found. No new training data will be generated.")
        # Optionally, still write header if output_csv is new
        if not os.path.exists(args.output_csv) or os.path.getsize(args.output_csv) == 0:
             with open(args.output_csv, 'w', newline='') as f_out:
                writer = csv.writer(f_out)
                writer.writerow(OUTPUT_CSV_HEADER)
        return

    for tl_file_path in tl_result_files:
        try:
            with open(tl_file_path, 'r') as f_tl:
                tl_data = json.load(f_tl)
            
            tl_node_id = tl_data.get("node_id")
            tl_success_ratio = tl_data.get("success_ratio")
            # Initial trust scores used by this TL for its sensors in this run
            initial_trusts_this_tl_used = tl_data.get("initial_trust_scores_used", {}) 

            if tl_node_id is None or tl_success_ratio is None:
                print(f"Warning: Skipping TL result file {tl_file_path} due to missing node_id or success_ratio.")
                continue

            # For each sensor this TL used, create a training instance
            for sensor_ip, initial_trust_val_used_by_tl in initial_trusts_this_tl_used.items():
                sensor_details_from_automation = sensor_ip_to_details_map.get(sensor_ip)
                
                if sensor_details_from_automation:
                    static_features = sensor_details_from_automation["static_features"]
                    
                    # The "target" for the ML model is what initial trust score it *should* have predicted
                    # to achieve this TL's success_ratio.
                    # A simple approach: target = success_ratio * 100
                    # More complex: if success_ratio is high, the initial_trust_val_used_by_tl was good.
                    # If success_ratio is low, the initial_trust_val_used_by_tl was perhaps too high or too low.
                    # For now, let's use the success ratio directly as the basis for the target.
                    target_initial_trust = round(tl_success_ratio * 100.0, 1) # Scale to 0-100

                    # Construct the row in the correct order of static features
                    row = [static_features.get(feat_name, None) for feat_name in ORDERED_STATIC_FEATURES]
                    row.append(target_initial_trust)
                    
                    # Basic validation that all features were found
                    if any(val is None for val in row[:-1]): # Check features, not target
                        print(f"Warning: Missing some static features for sensor {sensor_ip}. Row: {row}. Skipping.")
                        continue
                        
                    new_training_rows.append(row)
                else:
                    print(f"Warning: Static features for sensor IP {sensor_ip} (from TL {tl_node_id}) not found in automation log. Skipping.")

        except json.JSONDecodeError:
            print(f"Error: Could not decode JSON from TL result file: {tl_file_path}")
        except IOError:
            print(f"Error: Could not read TL result file: {tl_file_path}")

    # Append to the aggregated CSV file
    file_exists = os.path.exists(args.output_csv)
    try:
        with open(args.output_csv, 'a', newline='') as f_out:
            writer = csv.writer(f_out)
            if not file_exists or os.path.getsize(args.output_csv) == 0:
                writer.writerow(OUTPUT_CSV_HEADER) # Write header if new file
            writer.writerows(new_training_rows)
        print(f"Appended {len(new_training_rows)} new training instances to {args.output_csv}")
    except IOError:
        print(f"Error: Could not write to output CSV: {args.output_csv}")

    print("--- ML Feedback Data Preparation Finished ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare ML training data based on simulation run performance.")
    parser.add_argument("--automation-log", required=True, help="Path to the JSON log from automation.py (sensor features & initial trust).")
    parser.add_argument("--tl-results-dir", required=True, help="Path to the directory containing TL performance JSON files.")
    parser.add_argument("--output-csv", required=True, help="Path to the CSV file to append new training data to.")
    
    parsed_args = parser.parse_args()
    main(parsed_args)
