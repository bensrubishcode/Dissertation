import random
import os

def generate_lab_config(num_clusters, min_machines, max_machines):
    config_lines = []

    for cluster_id in range(1, num_clusters + 1):  # Cluster numbering starts from 1
        num_machines = random.randint(min_machines, max_machines)

        # Generate machine definitions with the new naming convention
        for machine_id in range(1, num_machines + 1):  # Machine numbering starts from 1
            machine_name = f"cluster{cluster_id}_machine{machine_id}"
            # Define the LAN as the cluster name and use a consistent subnet
            lan_name = f"cluster{cluster_id}"
            # Ensure all machines in the same cluster have the same base IP address
            ip_address = f"10.{cluster_id}.1.{machine_id}/24"  # Changed to use 10.{cluster_id}.1.x
            config_lines.append(f"{machine_name}[0]={lan_name}     $ip({ip_address});")

    return "\n".join(config_lines)

# Configuration parameters
num_clusters = random.randint(1, 3)  # Random number of clusters between 1 and 3
min_machines = 2                      # Minimum number of machines per cluster
max_machines = 5                      # Maximum number of machines per cluster

# Delete the existing lab.confu file if it exists
if os.path.exists("lab.confu"):
    os.remove("lab.confu")
    print("Existing lab.confu file deleted.")

# Generate the lab configuration
lab_config = generate_lab_config(num_clusters, min_machines, max_machines)

# Write to lab.confu file
with open("lab.confu", "w") as f:
    f.write(lab_config)

print("lab.confu file generated.")
