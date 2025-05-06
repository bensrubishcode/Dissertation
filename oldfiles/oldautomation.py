import random
import os
import subprocess

def generate_lab_config_from_edges(graph, docker_image, max_machines):
    """
    Generate lab configuration and startup commands with sensors on graph edges, limited to max_machines.

    :param graph: The graph structure containing edges.
    :param docker_image: Docker image to use for sensors.
    :param max_machines: Maximum number of machines to generate.
    :return: Lab configuration lines and startup commands.
    """
    config_lines = []
    startup_lines = []
    cluster_id = 0  # Unique ID for clusters based on edges
    machine_count = 0  # Track number of machines created

    # Iterate over all edges in the graph
    for edge in graph.edges(data=True):
        if machine_count >= max_machines:  # Limit the number of machines
            break

        u, v = edge[0], edge[1]
        cluster_id += 1  # Each edge represents a new cluster
        num_sensors = random.randint(1, min(3, max_machines - machine_count))  # Adjust sensors to stay within the limit

        for sensor_id in range(1, num_sensors + 1):
            if machine_count >= max_machines:
                break

            # Generate sensor names
            sensor_name = f"cluster{cluster_id}_sensor{sensor_id}"
            cluster_name = f"cluster{cluster_id}"
            ip_address = f"10.{cluster_id}.1.{sensor_id}/24"

            # Add sensor configuration
            config_lines.append(f"{sensor_name}[0]={cluster_name}     $ip({ip_address});")

            # Add Docker image in startup commands
            startup_lines.append(f"docker.run('{docker_image}', '{sensor_name}');")

            # Verify `nmap` availability
            startup_lines.append(f"docker.exec('{sensor_name}', 'nmap -V');")

            # Generate MAC address for the sensor
            mac_address = f"02:{cluster_id:02x}:{sensor_id:02x}:00:00:00"
            startup_lines.append(f"set_mac('{sensor_name}', '{mac_address}');")

            machine_count += 1

            # Establish UDP connections between sensors on the same edge (cluster)
            for other_sensor_id in range(1, num_sensors + 1):
                if other_sensor_id != sensor_id:
                    other_sensor_name = f"cluster{cluster_id}_sensor{other_sensor_id}"
                    startup_lines.append(f"udp.connect('{sensor_name}', '{other_sensor_name}');")

    return "\n".join(config_lines), "\n".join(startup_lines)


# Configuration for Docker
docker_image = "bensrubishcode/traffic_sensor"  # Corrected Docker image name
max_machines = 6  # Limit the number of machines for debugging

# Example Graph
import networkx as nx
G = nx.complete_graph(5)  # A small graph for debugging

# Delete existing files if present
for file_name in ["lab.confu", "startup"]:
    if os.path.exists(file_name):
        os.remove(file_name)
        print(f"Existing {file_name} file deleted.")

# Generate the lab configuration and startup commands
lab_config, startup_commands = generate_lab_config_from_edges(G, docker_image, max_machines)

# Write to lab.confu file
with open("lab.confu", "w") as f:
    for line in lab_config.splitlines():
        f.write(line.rstrip() + "\n")  # Remove any trailing spaces

# Write to startup file
with open("startup", "w") as f:
    for line in startup_commands.splitlines():
        f.write(line.rstrip() + "\n")  # Remove any trailing spaces

print("lab.confu and startup files generated.")

# Docker verification
try:
    print(f"Pulling Docker image: {docker_image}...")
    subprocess.run(["docker", "pull", docker_image], check=True)

    print("Verifying Docker image contents...")
    container_id = subprocess.check_output([
        "docker", "run", "--rm", docker_image, "nmap", "-V"
    ]).decode().strip()
    print(f"Verification successful. Nmap version: {container_id}")
except subprocess.CalledProcessError as e:
    print(f"Error during Docker operations: {e}")
