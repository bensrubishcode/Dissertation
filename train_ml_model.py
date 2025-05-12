# train_ml_model.py

import ml_risk_assessor # Import the module we just created
import os

# Define the path to the training data CSV - should match automation.py
DATA_FILEPATH = "ml_training_data.csv"

def main():
    print("--- Starting ML Model Training Process ---")

    if not os.path.exists(DATA_FILEPATH) or os.path.getsize(DATA_FILEPATH) == 0:
        print(f"Training data file '{DATA_FILEPATH}' not found or is empty.")
        print("Please run the main simulation (automation.py and potentially the Kathara lab via bash.sh)")
        print("at least once to generate training data.")
        return

    print(f"Attempting to train models using data from: {DATA_FILEPATH}")
    success = ml_risk_assessor.train_models(data_filepath=DATA_FILEPATH)

    if success:
        print("\n--- ML Model Training Process Completed Successfully ---")
        print(f"Models saved to:")
        print(f"  - Reliability Model: {ml_risk_assessor.RELIABILITY_MODEL_PATH}")
        print(f"  - Noisy Config Model: {ml_risk_assessor.NOISY_CONFIG_MODEL_PATH}")
        print(f"  - Preprocessor: {ml_risk_assessor.PREPROCESSOR_PATH}")
    else:
        print("\n--- ML Model Training Process Encountered Errors ---")
        print("Please check the output from 'ml_risk_assessor.train_models' for details.")

if __name__ == "__main__":
    main()
