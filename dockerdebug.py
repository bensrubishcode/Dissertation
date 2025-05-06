import os
import subprocess

def run_docker_machine(machine_name, docker_image, ip_address, mac_address):
    """
    Create and run a machine using a specified Docker image.
    
    :param machine_name: Name of the machine.
    :param docker_image: Docker image to use for the machine.
    :param ip_address: IP address to assign to the machine.
    :param mac_address: MAC address to assign to the machine.
    """
    try:
        # Pull the Docker image
        print(f"Pulling Docker image: {docker_image}...")
        subprocess.run(["docker", "pull", docker_image], check=True)

        # Run the Docker container
        print(f"Creating Docker container '{machine_name}'...")
        container_id = subprocess.check_output([
            "docker", "run", "-d", 
            "--name", machine_name, 
            "--network", "none",  # Isolated network to manage IP and MAC
            docker_image
        ]).decode().strip()
        print(f"Container '{machine_name}' created with ID: {container_id}")

        # Set the IP and MAC address using `docker network connect`
        bridge_name = "kathara_bridge"
        print(f"Connecting container to network bridge '{bridge_name}'...")
        subprocess.run([
            "docker", "network", "connect",
            "--ip", ip_address,
            "--mac-address", mac_address,
            bridge_name,
            machine_name
        ], check=True)
        print(f"Container '{machine_name}' connected to network bridge.")

        # Show logs for visibility
        print(f"Fetching logs for container '{machine_name}'...")
        logs = subprocess.check_output(["docker", "logs", machine_name]).decode()
        print(f"Logs for '{machine_name}':\n{logs}")

    except subprocess.CalledProcessError as e:
        print(f"Error during Docker operations: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")

    finally:
        # Cleanup for debugging purposes
        if input(f"Do you want to remove the container '{machine_name}'? (y/n): ").strip().lower() == 'y':
            print(f"Removing container '{machine_name}'...")
            subprocess.run(["docker", "rm", "-f", machine_name], check=True)
            print(f"Container '{machine_name}' removed.")

# Configuration
machine_name = "test_machine"
docker_image = "bensrubishcode/traffic_sensor"  # Replace with your Docker image
ip_address = "10.0.0.1"
mac_address = "02:42:ac:11:00:02"

# Create the machine
run_docker_machine(machine_name, docker_image, ip_address, mac_address)
