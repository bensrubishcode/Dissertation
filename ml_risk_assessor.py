# ml_risk_assessor.py

import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.metrics import mean_squared_error, accuracy_score, classification_report
import joblib # For saving and loading models
import os

# Define model file paths
RELIABILITY_MODEL_PATH = 'inherent_reliability_model.joblib'
NOISY_CONFIG_MODEL_PATH = 'configured_noisy_model.joblib'
PREPROCESSOR_PATH = 'ml_preprocessor.joblib' # To save the fitted preprocessor

# Define the features the model expects and categorical ones
# Order matters for the preprocessor if not using column names explicitly in ColumnTransformer later
# These must match the header of your ml_training_data.csv (excluding sensor_id and target vars)
STATIC_FEATURES = [
    "manufacturer", "software_version", "is_signed",
    "software_age_years", "device_age_years"
]
CATEGORICAL_FEATURES = ["manufacturer", "software_version"] # "is_signed" is already 0/1 if logged as int

# Target variables in the CSV
TARGET_RELIABILITY = "gt_inherent_reliability"
TARGET_IS_NOISY = "gt_is_configured_noisy"


def load_training_data(data_filepath="ml_training_data.csv"):
    """Loads the accumulated training data from the CSV file."""
    if not os.path.exists(data_filepath) or os.path.getsize(data_filepath) == 0:
        print(f"Warning: Training data file '{data_filepath}' not found or is empty. Cannot train models.")
        return None
    try:
        df = pd.read_csv(data_filepath)
        print(f"Loaded {len(df)} records from {data_filepath}")
        # Basic validation
        expected_cols = STATIC_FEATURES + [TARGET_RELIABILITY, TARGET_IS_NOISY]
        if not all(col in df.columns for col in expected_cols):
            print(f"Error: Missing expected columns in {data_filepath}. Expected: {expected_cols}, Got: {df.columns.tolist()}")
            return None
        return df
    except Exception as e:
        print(f"Error loading training data: {e}")
        return None

def build_preprocessor(df_features):
    """
    Builds a ColumnTransformer for preprocessing static features.
    Handles unseen categories in categorical features during transform.
    """
    # Ensure categorical features are treated as strings initially for robust OHE
    for cat_col in CATEGORICAL_FEATURES:
        if cat_col in df_features.columns:
            df_features[cat_col] = df_features[cat_col].astype(str)

    # For OneHotEncoder, handle_unknown='ignore' will output all zeros for unknown categories
    # during transform (prediction time).
    # remainder='passthrough' ensures numerical features are kept as they are.
    preprocessor = ColumnTransformer(
        transformers=[
            ('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False), CATEGORICAL_FEATURES)
        ],
        remainder='passthrough' # Keeps other columns (is_signed, software_age, device_age)
    )
    return preprocessor

def train_models(data_filepath="ml_training_data.csv"):
    """Trains the reliability regressor and noisy configuration classifier."""
    df = load_training_data(data_filepath)
    if df is None or df.empty:
        print("Skipping model training due to missing or empty data.")
        return False

    print("Starting model training...")

    X = df[STATIC_FEATURES].copy() # Features
    y_reliability = df[TARGET_RELIABILITY]
    y_is_noisy = df[TARGET_IS_NOISY]

    # --- Preprocessor ---
    # It's important to fit the preprocessor on the *entire* available feature set (X)
    # to learn all categories, then transform train/test sets.
    preprocessor = build_preprocessor(X.copy()) # Pass a copy to avoid modifying X here
    
    try:
        X_processed_full = preprocessor.fit_transform(X) # Fit and transform the full X
        joblib.dump(preprocessor, PREPROCESSOR_PATH) # Save the fitted preprocessor
        print(f"Preprocessor fitted and saved to {PREPROCESSOR_PATH}")
    except Exception as e:
        print(f"Error fitting or saving preprocessor: {e}")
        return False

    # Split data for reliability model
    # We use the already processed full X to split, or re-process splits if preferred.
    # For simplicity here, let's just train on the full processed data available for now.
    # Proper train/test split is crucial for robust evaluation, but for iterative learning
    # across simulations, we often train on all available historical data.
    # For demonstration of metrics, we can do a split.
    
    X_train_rel, X_test_rel, y_train_rel, y_test_rel = train_test_split(
        X_processed_full, y_reliability, test_size=0.2, random_state=42
    )
    X_train_noi, X_test_noi, y_train_noi, y_test_noi = train_test_split(
        X_processed_full, y_is_noisy, test_size=0.2, random_state=42, stratify=y_is_noisy if len(y_is_noisy.unique()) > 1 else None
    )


    # --- 1. Train Inherent Reliability Model (Regressor) ---
    print("\nTraining Inherent Reliability Model...")
    reliability_model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1, max_depth=10, min_samples_split=5)
    try:
        reliability_model.fit(X_train_rel, y_train_rel)
        joblib.dump(reliability_model, RELIABILITY_MODEL_PATH)
        print(f"Reliability model trained and saved to {RELIABILITY_MODEL_PATH}")
        # Evaluation
        y_pred_rel = reliability_model.predict(X_test_rel)
        rmse = mean_squared_error(y_test_rel, y_pred_rel, squared=False)
        print(f"Reliability Model RMSE on test set: {rmse:.2f}")
    except Exception as e:
        print(f"Error training or saving reliability model: {e}")
        return False

    # --- 2. Train "Is Configured Noisy" Model (Classifier) ---
    print("\nTraining 'Is Configured Noisy' Model...")
    # Check if there's more than one class in the target for noisy model
    if len(y_is_noisy.unique()) < 2 :
        print(f"Warning: Only one class ({y_is_noisy.unique()}) present for 'is_noisy' target. Cannot train classifier robustly.")
        # Optionally, save a dummy model or skip saving
        # For now, we'll just print a warning and not save if only one class.
        # If you want to handle this by always predicting the majority class, you can implement a dummy classifier.
    else:
        noisy_config_model = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1, max_depth=10, min_samples_split=5, class_weight='balanced')
        try:
            noisy_config_model.fit(X_train_noi, y_train_noi)
            joblib.dump(noisy_config_model, NOISY_CONFIG_MODEL_PATH)
            print(f"Noisy configuration model trained and saved to {NOISY_CONFIG_MODEL_PATH}")
            # Evaluation
            y_pred_noi = noisy_config_model.predict(X_test_noi)
            accuracy = accuracy_score(y_test_noi, y_pred_noi)
            print(f"Noisy Config Model Accuracy on test set: {accuracy:.2f}")
            print("Classification Report for Noisy Config Model:")
            print(classification_report(y_test_noi, y_pred_noi, zero_division=0))
        except Exception as e:
            print(f"Error training or saving noisy configuration model: {e}")
            return False
            
    print("\nModel training process completed.")
    return True


def predict_initial_attributes(static_features_dict):
    """
    Predicts initial device attributes using loaded models.
    static_features_dict should contain keys matching STATIC_FEATURES.
    Returns a dictionary with predicted attributes.
    """
    predictions = {
        "predicted_inherent_reliability": 75.0, # Default fallback
        "predicted_is_noisy_probability": 0.1  # Default fallback
    }

    # Load preprocessor
    try:
        preprocessor = joblib.load(PREPROCESSOR_PATH)
    except FileNotFoundError:
        print(f"Warning: Preprocessor file '{PREPROCESSOR_PATH}' not found. Using default predictions.")
        return predictions
    except Exception as e:
        print(f"Error loading preprocessor: {e}. Using default predictions.")
        return predictions

    # Load reliability model
    try:
        reliability_model = joblib.load(RELIABILITY_MODEL_PATH)
    except FileNotFoundError:
        print(f"Warning: Reliability model '{RELIABILITY_MODEL_PATH}' not found. Using default for reliability.")
        reliability_model = None # Flag that model is not available
    except Exception as e:
        print(f"Error loading reliability model: {e}. Using default for reliability.")
        reliability_model = None

    # Load noisy configuration model
    try:
        noisy_config_model = joblib.load(NOISY_CONFIG_MODEL_PATH)
    except FileNotFoundError:
        print(f"Warning: Noisy config model '{NOISY_CONFIG_MODEL_PATH}' not found. Using default for noise probability.")
        noisy_config_model = None # Flag that model is not available
    except Exception as e:
        print(f"Error loading noisy config model: {e}. Using default for noise probability.")
        noisy_config_model = None

    # Prepare input DataFrame for preprocessing
    # Ensure the order of columns matches STATIC_FEATURES for the preprocessor
    try:
        input_df = pd.DataFrame([static_features_dict], columns=STATIC_FEATURES)
        # Ensure categorical features are strings for preprocessor
        for cat_col in CATEGORICAL_FEATURES:
            if cat_col in input_df.columns:
                input_df[cat_col] = input_df[cat_col].astype(str)
        
        processed_features = preprocessor.transform(input_df)
    except Exception as e:
        print(f"Error preprocessing input features for prediction: {e}. Using default predictions.")
        return predictions

    if reliability_model:
        try:
            pred_rel = reliability_model.predict(processed_features)[0]
            predictions["predicted_inherent_reliability"] = round(max(0, min(100, pred_rel)), 1)
        except Exception as e:
            print(f"Error during reliability prediction: {e}. Using default.")

    if noisy_config_model:
        try:
            # Predict probability of being noisy (class 1)
            pred_noi_proba = noisy_config_model.predict_proba(processed_features)[0][1] # Probability of class 1
            predictions["predicted_is_noisy_probability"] = round(pred_noi_proba, 3)
        except Exception as e:
            print(f"Error during noisy config probability prediction: {e}. Using default.")
            # If predict_proba fails (e.g. model not suited), could try predict() and map
            try:
                pred_noi_class = noisy_config_model.predict(processed_features)[0]
                predictions["predicted_is_noisy_probability"] = 0.75 if pred_noi_class == 1 else 0.25 # Map from class
            except:
                pass # Stick to default if fallback also fails

    return predictions

if __name__ == '__main__':
    print("ML Risk Assessor Module")
    # Example: train models if data exists
    # In a real workflow, training would be a separate step.
    # train_models() # Uncomment to test training if ml_training_data.csv exists

    # Example prediction:
    # Ensure a preprocessor and models are trained and saved first by running train_models()
    if os.path.exists(PREPROCESSOR_PATH) and \
       os.path.exists(RELIABILITY_MODEL_PATH) and \
       os.path.exists(NOISY_CONFIG_MODEL_PATH):
        print("\n--- Example Prediction ---")
        sample_features_good = {
            "manufacturer": "GoodSensorCorp",
            "software_version": "v2.1.0-signed",
            "is_signed": 1, # True
            "software_age_years": 0.5,
            "device_age_years": 0.2
        }
        sample_features_bad = {
            "manufacturer": "ShadySensorsLtd",
            "software_version": "v1.0.0", # This is unsigned
            "is_signed": 0, # False
            "software_age_years": 4.0,
            "device_age_years": 4.5
        }
        preds_good = predict_initial_attributes(sample_features_good)
        print(f"Predictions for GoodSensor (static: {sample_features_good}): {preds_good}")
        preds_bad = predict_initial_attributes(sample_features_bad)
        print(f"Predictions for ShadySensor (static: {sample_features_bad}): {preds_bad}")
    else:
        print("\nRun training first to generate models for example prediction.")
        print("You can uncomment 'train_models()' call above and run this script directly,")
        print("assuming 'ml_training_data.csv' exists from automation.py.")
