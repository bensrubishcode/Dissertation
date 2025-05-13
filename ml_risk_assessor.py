#!/usr/bin/env python3
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.metrics import mean_squared_error, accuracy_score, classification_report, r2_score
import joblib
import os
import numpy as np

# --- Model File Paths ---
# Paths for the "old" models (predicting GT reliability and noisiness)
# These might still be trained if ml_training_data.csv is generated and used.
GT_RELIABILITY_MODEL_PATH = 'inherent_reliability_model.joblib' # Predicts GT reliability
GT_NOISY_CONFIG_MODEL_PATH = 'configured_noisy_model.joblib' # Predicts GT noisiness
GT_PREPROCESSOR_PATH = 'ml_preprocessor.joblib' # For the GT models

# NEW: Paths for the "Initial Trust Predictor" model
# This model predicts an initial trust score (0-100) based on static features,
# trained on feedback from traffic light success ratios.
INITIAL_TRUST_MODEL_PATH = 'initial_trust_predictor_model.joblib'
INITIAL_TRUST_PREPROCESSOR_PATH = 'initial_trust_preprocessor.joblib'

# --- Feature Definitions ---
# Static features used as input for all models
STATIC_FEATURES = [
    "manufacturer", "software_version", "is_signed",
    "software_age_years", "device_age_years"
]
CATEGORICAL_FEATURES = ["manufacturer", "software_version"] # "is_signed" is already 0/1

# Target variables for the "old" GT-based models (from ml_training_data.csv)
TARGET_GT_RELIABILITY = "gt_inherent_reliability"
TARGET_GT_IS_NOISY = "gt_is_configured_noisy"

# Target variable for the NEW "Initial Trust Predictor" model (from ml_feedback_training_data.csv)
TARGET_INITIAL_TRUST = "target_initial_trust" # This will be derived from TL success_ratio * 100

def load_data(data_filepath, model_type):
    """Loads data based on model_type."""
    if not os.path.exists(data_filepath) or os.path.getsize(data_filepath) == 0:
        print(f"Warning: Training data file '{data_filepath}' not found or is empty for model type '{model_type}'.")
        return None
    try:
        df = pd.read_csv(data_filepath)
        print(f"Loaded {len(df)} records from {data_filepath} for model type '{model_type}'.")
        
        # Validate columns based on model_type
        if model_type == "InitialTrustPredictor":
            expected_cols = STATIC_FEATURES + [TARGET_INITIAL_TRUST]
        elif model_type in ["GTReliability", "GTNoisy"]: # For old models
            expected_cols = STATIC_FEATURES + [TARGET_GT_RELIABILITY, TARGET_GT_IS_NOISY]
        else:
            print(f"Error: Unknown model_type '{model_type}' for column validation.")
            return None
            
        if not all(col in df.columns for col in expected_cols):
            print(f"Error: Missing expected columns in {data_filepath} for {model_type}. Expected: {expected_cols}, Got: {df.columns.tolist()}")
            return None
        df = df.dropna(subset=expected_cols) # Drop rows where any expected column is NaN
        if df.empty:
            print(f"Warning: DataFrame became empty after dropping NaNs for {model_type}. Check data quality in {data_filepath}.")
            return None
        return df
    except Exception as e:
        print(f"Error loading training data for {model_type} from {data_filepath}: {e}")
        return None

def build_preprocessor(df_features_for_fitting):
    """
    Builds a ColumnTransformer for preprocessing static features.
    Fits on the provided df_features_for_fitting.
    """
    # Ensure categorical features are treated as strings for robust OHE
    # Create a copy to avoid SettingWithCopyWarning if df_features_for_fitting is a slice
    df_features = df_features_for_fitting.copy()
    for cat_col in CATEGORICAL_FEATURES:
        if cat_col in df_features.columns:
            df_features.loc[:, cat_col] = df_features[cat_col].astype(str)

    numerical_features = [f for f in STATIC_FEATURES if f not in CATEGORICAL_FEATURES and f != "is_signed"]
    
    # Define transformers
    # For OneHotEncoder, handle_unknown='ignore' outputs all zeros for unknown categories during transform.
    # remainder='passthrough' keeps columns not specified in transformers (like 'is_signed').
    # Numerical features ('software_age_years', 'device_age_years') will be scaled.
    preprocessor = ColumnTransformer(
        transformers=[
            ('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False), CATEGORICAL_FEATURES),
            ('num', StandardScaler(), numerical_features) 
        ],
        remainder='passthrough' # 'is_signed' will be passed through
    )
    
    try:
        preprocessor.fit(df_features) # Fit on the provided data
        print("Preprocessor built and fitted successfully.")
        return preprocessor
    except Exception as e:
        print(f"Error building or fitting preprocessor: {e}")
        return None


def train_models(data_filepath, model_type="InitialTrustPredictor"): # Default to new model type
    """
    Trains the specified ML model.
    - "InitialTrustPredictor": Trains a regressor to predict an initial trust score (0-100)
                               based on TL success ratio feedback.
    - "GTReliability": Trains a regressor for gt_inherent_reliability (old model).
    - "GTNoisy": Trains a classifier for gt_is_configured_noisy (old model).
    """
    df = load_data(data_filepath, model_type)
    if df is None or df.empty:
        print(f"Skipping model training for '{model_type}' due to missing or empty data from {data_filepath}.")
        return False

    print(f"--- Starting Model Training for: {model_type} ---")

    X = df[STATIC_FEATURES].copy() # Features are the same for all models

    # --- Preprocessor ---
    # Build and fit preprocessor on the current dataset X
    # This preprocessor will be saved specific to the model_type
    preprocessor = build_preprocessor(X)
    if preprocessor is None:
        print(f"Failed to build preprocessor for {model_type}. Aborting training.")
        return False
    
    X_processed_full = preprocessor.transform(X) # Transform the full X for training

    if model_type == "InitialTrustPredictor":
        y_target = df[TARGET_INITIAL_TRUST]
        if y_target.isnull().any():
            print(f"Warning: Target '{TARGET_INITIAL_TRUST}' contains NaN values. Dropping them.")
            valid_indices = y_target.notnull()
            y_target = y_target[valid_indices]
            X_processed_full = X_processed_full[valid_indices]
            if y_target.empty:
                print("Error: Target variable became empty after dropping NaNs. Cannot train.")
                return False

        # Split for evaluation of InitialTrustPredictor
        X_train, X_test, y_train, y_test = train_test_split(
            X_processed_full, y_target, test_size=0.2, random_state=42
        )
        
        model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1, max_depth=15, min_samples_split=5, min_samples_leaf=2)
        model_path = INITIAL_TRUST_MODEL_PATH
        preprocessor_path = INITIAL_TRUST_PREPROCESSOR_PATH
        
        try:
            model.fit(X_train, y_train)
            joblib.dump(model, model_path)
            joblib.dump(preprocessor, preprocessor_path) # Save the fitted preprocessor for this model
            print(f"{model_type} model trained and saved to {model_path}")
            print(f"{model_type} preprocessor saved to {preprocessor_path}")
            
            y_pred = model.predict(X_test)
            rmse = mean_squared_error(y_test, y_pred, squared=False)
            r2 = r2_score(y_test, y_pred)
            print(f"{model_type} Performance on test set: RMSE={rmse:.2f}, R2={r2:.2f}")
            # Check for near-zero variance in predictions if R2 is very low/negative
            if np.var(y_pred) < 1e-6 and np.var(y_test) > 1e-6 :
                 print(f"Warning: {model_type} predictions have near-zero variance. Model might be predicting a constant value.")

        except Exception as e:
            print(f"Error training or saving {model_type} model: {e}")
            return False

    elif model_type == "GTReliability":
        # Logic for training the old GT_RELIABILITY_MODEL_PATH
        y_target = df[TARGET_GT_RELIABILITY]
        X_train, X_test, y_train, y_test = train_test_split(X_processed_full, y_target, test_size=0.2, random_state=42)
        model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1, max_depth=10, min_samples_split=5)
        model_path = GT_RELIABILITY_MODEL_PATH
        preprocessor_path = GT_PREPROCESSOR_PATH # Can use a shared preprocessor if features are identical
        try:
            model.fit(X_train, y_train)
            joblib.dump(model, model_path)
            joblib.dump(preprocessor, preprocessor_path)
            print(f"{model_type} model trained and saved to {model_path}")
            y_pred = model.predict(X_test); rmse = mean_squared_error(y_test, y_pred, squared=False)
            print(f"{model_type} RMSE on test set: {rmse:.2f}")
        except Exception as e: print(f"Error training {model_type}: {e}"); return False

    elif model_type == "GTNoisy":
        # Logic for training the old GT_NOISY_CONFIG_MODEL_PATH
        y_target = df[TARGET_GT_IS_NOISY]
        if len(y_target.unique()) < 2 :
            print(f"Warning: Only one class ({y_target.unique()}) present for '{TARGET_GT_IS_NOISY}'. Cannot train classifier robustly.")
            return True # Not a failure, just can't train
        X_train, X_test, y_train, y_test = train_test_split(X_processed_full, y_target, test_size=0.2, random_state=42, stratify=y_target)
        model = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1, max_depth=10, min_samples_split=5, class_weight='balanced')
        model_path = GT_NOISY_CONFIG_MODEL_PATH
        preprocessor_path = GT_PREPROCESSOR_PATH # Can use a shared preprocessor
        try:
            model.fit(X_train, y_train)
            joblib.dump(model, model_path)
            # joblib.dump(preprocessor, preprocessor_path) # Only save if it's specific or first time
            print(f"{model_type} model trained and saved to {model_path}")
            y_pred = model.predict(X_test); accuracy = accuracy_score(y_test, y_pred)
            print(f"{model_type} Accuracy on test set: {accuracy:.2f}")
            # print(classification_report(y_test, y_pred, zero_division=0))
        except Exception as e: print(f"Error training {model_type}: {e}"); return False
    else:
        print(f"Error: Unknown model_type '{model_type}' specified for training.")
        return False
            
    print(f"\nModel training process for {model_type} completed.")
    return True


def predict_initial_attributes(static_features_dict):
    """
    Predicts attributes using loaded models.
    Primarily uses the InitialTrustPredictor for 'predicted_initial_trust'.
    Can also return other predictions if those models are loaded.
    """
    predictions = {
        "predicted_initial_trust": FALLBACK_ML_INITIAL_TRUST_SCORE, # Default for the new primary output
        "predicted_inherent_reliability": random.uniform(*FALLBACK_DEVICE_RELIABILITY_RANGE_AUTO), # Fallback for old key
        "predicted_is_noisy_probability": random.uniform(*FALLBACK_PREDICTED_NOISE_PROB_RANGE_AUTO)  # Fallback for old key
    }
    
    # --- Predict Initial Trust using InitialTrustPredictor ---
    initial_trust_model = None
    initial_trust_preprocessor = None

    if os.path.exists(INITIAL_TRUST_MODEL_PATH) and os.path.exists(INITIAL_TRUST_PREPROCESSOR_PATH):
        try:
            initial_trust_model = joblib.load(INITIAL_TRUST_MODEL_PATH)
            initial_trust_preprocessor = joblib.load(INITIAL_TRUST_PREPROCESSOR_PATH)
        except Exception as e:
            print(f"Error loading InitialTrustPredictor model/preprocessor: {e}. Using fallback for initial trust.")
    else:
        print(f"Warning: InitialTrustPredictor model ('{INITIAL_TRUST_MODEL_PATH}') or preprocessor ('{INITIAL_TRUST_PREPROCESSOR_PATH}') not found. Using fallback for initial trust.")

    if initial_trust_model and initial_trust_preprocessor:
        try:
            input_df = pd.DataFrame([static_features_dict], columns=STATIC_FEATURES)
            # Ensure categorical features are strings for preprocessor consistency
            input_df_copy = input_df.copy()
            for cat_col in CATEGORICAL_FEATURES:
                if cat_col in input_df_copy.columns:
                    input_df_copy.loc[:, cat_col] = input_df_copy[cat_col].astype(str)
            
            processed_features = initial_trust_preprocessor.transform(input_df_copy)
            pred_initial_trust = initial_trust_model.predict(processed_features)[0]
            predictions["predicted_initial_trust"] = round(max(0, min(100, pred_initial_trust)), 1) # Ensure 0-100
        except Exception as e:
            print(f"Error during InitialTrustPredictor prediction: {e}. Using fallback for initial trust.")
            # predictions["predicted_initial_trust"] remains fallback

    # --- Optionally, load and predict with old GT-based models if needed for other features ---
    # This part is kept if 'predicted_inherent_reliability' or 'predicted_is_noisy_probability'
    # are still used as inputs to the fuzzy logic system or for other comparisons.
    # If they are NOT used anymore, this block can be removed.
    gt_preprocessor = None
    if os.path.exists(GT_PREPROCESSOR_PATH):
        try: gt_preprocessor = joblib.load(GT_PREPROCESSOR_PATH)
        except Exception as e: print(f"Error loading GT preprocessor: {e}")

    if gt_preprocessor: # Only proceed if GT preprocessor loaded
        try:
            input_df_gt = pd.DataFrame([static_features_dict], columns=STATIC_FEATURES)
            input_df_gt_copy = input_df_gt.copy()
            for cat_col in CATEGORICAL_FEATURES:
                if cat_col in input_df_gt_copy.columns:
                    input_df_gt_copy.loc[:, cat_col] = input_df_gt_copy[cat_col].astype(str)
            processed_features_gt = gt_preprocessor.transform(input_df_gt_copy)

            if os.path.exists(GT_RELIABILITY_MODEL_PATH):
                try:
                    gt_reliability_model = joblib.load(GT_RELIABILITY_MODEL_PATH)
                    pred_rel = gt_reliability_model.predict(processed_features_gt)[0]
                    predictions["predicted_inherent_reliability"] = round(max(0, min(100, pred_rel)), 1)
                except Exception as e: print(f"Error during GT reliability prediction: {e}")
            
            if os.path.exists(GT_NOISY_CONFIG_MODEL_PATH):
                try:
                    gt_noisy_model = joblib.load(GT_NOISY_CONFIG_MODEL_PATH)
                    pred_noi_proba = gt_noisy_model.predict_proba(processed_features_gt)[0][1]
                    predictions["predicted_is_noisy_probability"] = round(pred_noi_proba, 3)
                except Exception as e: print(f"Error during GT noisy config probability prediction: {e}")
        except Exception as e:
            print(f"Error during GT model predictions (after loading GT preprocessor): {e}")
            
    return predictions


if __name__ == '__main__':
    print("ML Risk Assessor Module")
    # Example usage:
    # 1. Create dummy ml_feedback_training_data.csv
    #    (manufacturer,software_version,is_signed,software_age_years,device_age_years,target_initial_trust)
    #    GoodSensorCorp,v2.1.0-signed,1,0.5,0.2,85.0
    #    ShadySensorsLtd,v1.0.0,0,4.0,4.5,30.0
    #
    # 2. Train the InitialTrustPredictor
    #    train_models(data_filepath="ml_feedback_training_data.csv", model_type="InitialTrustPredictor")
    #
    # 3. Make a prediction
    # if os.path.exists(INITIAL_TRUST_MODEL_PATH):
    #     sample_features = {
    #         "manufacturer": "GoodSensorCorp", "software_version": "v2.1.0-signed",
    #         "is_signed": 1, "software_age_years": 0.5, "device_age_years": 0.2
    #     }
    #     preds = predict_initial_attributes(sample_features)
    #     print(f"\nPrediction for Initial Trust (sample_features): {preds}")
    # else:
    #     print("\nTrain InitialTrustPredictor model first to see example prediction.")
