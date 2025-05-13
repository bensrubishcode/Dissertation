#!/usr/bin/env python3
import ml_risk_assessor # Uses the updated ml_risk_assessor.py
import os
import argparse

def main(args):
    print(f"--- Starting ML Model Training Process for Model Type: {args.model_type} ---")

    if not args.data_file:
        print("Error: No data file specified for training.")
        return

    if not os.path.exists(args.data_file) or os.path.getsize(args.data_file) == 0:
        print(f"Training data file '{args.data_file}' not found or is empty.")
        if args.model_type == "InitialTrustPredictor":
            print("This is expected on the very first run if AGGREGATED_ML_TRAINING_DATA_CSV is new.")
            print("An empty model/preprocessor might be created, or training might be skipped by ml_risk_assessor.")
        # For other model types, this might be a more critical error.
        # Let ml_risk_assessor.train_models handle the empty data case.
        # return # Don't return here, let train_models decide if it can proceed

    print(f"Attempting to train '{args.model_type}' model using data from: {args.data_file}")
    
    # Call the centralized training function in ml_risk_assessor
    success = ml_risk_assessor.train_models(
        data_filepath=args.data_file,
        model_type=args.model_type
    )

    if success:
        print(f"\n--- ML Model Training Process for '{args.model_type}' Completed Successfully (or handled gracefully) ---")
        # Specific model paths are now internal to ml_risk_assessor
        if args.model_type == "InitialTrustPredictor":
            if hasattr(ml_risk_assessor, 'INITIAL_TRUST_MODEL_PATH') and os.path.exists(ml_risk_assessor.INITIAL_TRUST_MODEL_PATH):
                print(f"  - Initial Trust Predictor Model: {ml_risk_assessor.INITIAL_TRUST_MODEL_PATH}")
                print(f"  - Initial Trust Predictor Preprocessor: {ml_risk_assessor.INITIAL_TRUST_PREPROCESSOR_PATH}")
            else:
                print(f"  Note: Initial Trust Predictor model/preprocessor files might not have been created if data was insufficient.")
        # Add similar checks for other model types if needed
    else:
        print(f"\n--- ML Model Training Process for '{args.model_type}' Encountered Errors or Was Skipped ---")
        print("Please check the output from 'ml_risk_assessor.train_models' for details.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train ML models for the ITS simulation.")
    parser.add_argument(
        "--data-file", 
        required=True,
        help="Path to the CSV data file for training."
    )
    parser.add_argument(
        "--model-type", 
        required=True, 
        choices=["InitialTrustPredictor", "GTReliability", "GTNoisy"], # Add other types if any
        help="Type of model to train."
    )
    
    parsed_args = parser.parse_args()
    main(parsed_args)
