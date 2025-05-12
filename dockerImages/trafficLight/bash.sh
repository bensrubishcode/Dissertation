#!/bin/bash

# Set variables
IMAGE_NAME="bensrubishcode/traffic_light"  # Replace with your Docker Hub username and image name
TAG="latest" # Or a specific version tag
DOCKERFILE_PATH="./Dockerfile" # Path to your Dockerfile
TRAFFIC_LIGHT_PATH="./traffic_light_controller.py"
ML_RISK_ASSESSOR_PATH="../../ml_risk_assessor.py" # Original location of the file
TEMP_DIR="./temp_docker_build" # Temporary directory for build context

# Create a temporary directory to avoid polluting the current directory
mkdir -p "$TEMP_DIR"

# Copy the necessary files into the temporary directory
cp "$ML_RISK_ASSESSOR_PATH" "$TEMP_DIR/"
cp "$DOCKERFILE_PATH" "$TEMP_DIR/"
cp "$TRAFFIC_LIGHT_PATH" "$TEMP_DIR/"

# Navigate into the temporary directory
cd "$TEMP_DIR" || exit 1

# List files in the temporary directory (for debugging)
ls

# Build the Docker image
docker build -t "$IMAGE_NAME:$TAG" .

# Check if the build was successful
if [ $? -ne 0 ]; then
  echo "Docker build failed!"
  exit 1
fi

# Log in to Docker Hub (if not already logged in)
# docker login  # Run this manually beforehand

# Push the Docker image to Docker Hub
docker push "$IMAGE_NAME:$TAG"

# Check if the push was successful
if [ $? -ne 0 ]; then
  echo "Docker push failed!"
  exit 1
fi

echo "Docker image built and pushed successfully!"

# Clean up the temporary directory
cd .. # Navigate back to the original directory
rm -rf "$TEMP_DIR"

exit 0
