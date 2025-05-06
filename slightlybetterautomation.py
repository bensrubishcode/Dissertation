import random
import os

def generate_lab_config(num_clusters, min_machines, max_machines, docker_image):
    config_lines = []
    startup_lines = []

    for cluster_id in range(1, num_clusters + 1):
        num_machines = random.randint(min_machines, max_machines)

        for machine_id in range(1, num_machines + 1):
            # Updated naming convention
            machine_name = f"cluster{cluster_id}_machine{machine_id}"
            cluster_name = f"cluster{cluster_id}"
            ip_address = f"10.{cluster_id}.1.{machine_id}/24"
            config_lines.append(f"{machine_name}[0]={cluster_name}     $ip({ip_address});")

            # Generate MAC address
            mac_address = f"02:{cluster_id:02x}:{machine_id:02x}:00:00:00"
            startup_lines.append(f"set_mac('{machine_name}', '{mac_address}');")

            # Establish UDP connections between machines in the same cluster
            for other_machine_id in range(1, num_machines + 1):
                if other_machine_id != machine_id:
                    other_machine_name = f"cluster{cluster_id}_machine{other_machine_id}"
                    startup_lines.append(f"udp.connect('{machine_name}', '{other_machine_name}');")

    # Establish TCP connections between machines in different clusters
    for cluster_id1 in range(1, num_clusters + 1):
        for cluster_id2 in range(cluster_id1 + 1, num_clusters + 1):
            for machine_id1 in range(1, random.randint(min_machines, max_machines) + 1):
                for machine_id2 in range(1, random.randint(min_machines, max_machines) + 1):
                    sensor_name1 = f"cluster{cluster_id1}_machine{machine_id1}"
                    sensor_name2 = f"cluster{cluster_id2}_machine{machine_id2}"
                    startup_lines.append(f"tcp.connect('{sensor_name1}', '{sensor_name2}');")

    return "\n".join(config_lines), "\n".join(startup_lines)

# Configuration parameters
num_clusters = random.randint(1, 3)  # Random number of clusters between 1 and 3
min_machines = 2                      # Minimum number of machines per cluster
max_machines = 5                      # Maximum number of machines per cluster
docker_image = "benistheboss/traffic_sensor"  # Updated Docker image name

# Delete the existing lab.confu file if it exists
if os.path.exists("lab.confu"):
    os.remove("lab.confu")
    print("Existing lab.confu file deleted.")

# Delete the existing startup file if it exists
if os.path.exists("startup"):
    os.remove("startup")
    print("Existing startup file deleted.")

# Generate the lab configuration and startup commands
lab_config, startup_commands = generate_lab_config(num_clusters, min_machines, max_machines, docker_image)

# Write to lab.confu file
with open("lab.confu", "w") as f:
    for line in lab_config.splitlines():
        f.write(line.rstrip() + "\n")  # Remove any trailing spaces

# Write to startup file
with open("startup", "w") as f:
    for line in startup_commands.splitlines():
        f.write(line.rstrip() + "\n")  # Remove any trailing spaces

print("lab.confu and startup files generated.")
