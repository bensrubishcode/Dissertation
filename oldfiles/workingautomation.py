#!/usr/bin/env python3
import random
import os

# New function to generate config with routers per cluster
def generate_lab_config_with_routers(num_clusters, min_machines, max_machines, client_image, router_image):
    """
    Generates Kathara lab configuration with dedicated routers per cluster.

    Args:
        num_clusters: The number of clusters (and routers) to generate.
        min_machines: Minimum number of client machines per cluster.
        max_machines: Maximum number of client machines per cluster.
        client_image: Docker image name for client machines.
        router_image: Docker image name for router machines.

    Returns:
        A tuple containing two strings:
        1. The content for the lab.confu file.
        2. The content for the global startup file (currently unused).
    """
    config_lines = []
    startup_lines = [] # Kept for structure, but likely empty in this setup

    backbone_lan_name = "backbone0" # Name for the router transit network
    backbone_subnet_prefix = "192.168.254" # Subnet for backbone (don't overlap with 10.x)

    router_details = {} # Store router info for cross-referencing

    print(f"--- Generating {num_clusters} Clusters and Routers ---")

    # --- First Pass: Define Clusters, Machines, Routers, and IPs ---
    for cluster_id in range(1, num_clusters + 1):
        num_machines_in_cluster = random.randint(min_machines, max_machines)
        cluster_lan_name = f"lan{cluster_id}"
        router_name = f"router{cluster_id}"
        # Use a convention for router IPs (e.g., .254 on the LAN side)
        router_ip_on_lan = f"10.{cluster_id}.1.254"
        router_ip_on_backbone = f"{backbone_subnet_prefix}.{cluster_id}" # Unique IP on backbone

        # Store router IPs for setting gateways and RIP config later
        router_details[cluster_id] = {
            "name": router_name,
            "ip_lan": router_ip_on_lan,
            "ip_backbone": router_ip_on_backbone
        }

        # --- Define Router for this Cluster ---
        print(f"  Defining {router_name} (interfaces on {cluster_lan_name} and {backbone_lan_name})")
        # Define router device with its image
        config_lines.append(f"{router_name}[image]={router_image}    $") # Image spec + $
        # --- ADDED LINE ---
        # Add privileged flag to allow sysctl modification inside container
        config_lines.append(f"{router_name}[privileged]=true    $")
        # --- END ADDED LINE ---
        # Router Interface to Cluster LAN (eth0) + IP Command
        config_lines.append(f"{router_name}[0]={cluster_lan_name}    $ip({router_ip_on_lan}/24);")
        # Router Interface to Backbone LAN (eth1) + IP Command (RIP commands added later)
        config_lines.append(f"{router_name}[1]={backbone_lan_name}    $ip({router_ip_on_backbone}/24);")

        # --- Define Client Machines in this Cluster ---
        print(f"  Defining {num_machines_in_cluster} client machines for cluster {cluster_id} on {cluster_lan_name}")
        for machine_id in range(1, num_machines_in_cluster + 1):
            machine_name = f"cluster{cluster_id}_machine{machine_id}"
            ip_address = f"10.{cluster_id}.1.{machine_id}" # Client IP

            # Define client machine with its image
            config_lines.append(f"{machine_name}[image]={client_image}    $") # Image spec + $
            # Client Interface to Cluster LAN (eth0) + IP Command + Default Gateway Command
            config_lines.append(f"{machine_name}[0]={cluster_lan_name}    $ip({ip_address}/24); to(default, {router_ip_on_lan});")

        print(f"  Finished cluster {cluster_id}")

    # --- Second Pass: Add Routing Configuration to Routers (using RIP) ---
    print("\n--- Adding RIP configuration to routers ---")
    for cluster_id in range(1, num_clusters + 1):
        router_name = router_details[cluster_id]["name"]
        # Subnets directly connected to this router
        lan_subnet = f"10.{cluster_id}.1.0/24"
        backbone_subnet = f"{backbone_subnet_prefix}.0/24"

        # Tacata command string to configure RIP on this router
        # Tells ripd which networks it is attached to, and asks it to redistribute
        # its knowledge of directly connected networks ("connected") via RIP.
        rip_command = f"rip({router_name}, {lan_subnet}, connected); rip({router_name}, {backbone_subnet}, connected);"
        print(f"  Configuring RIP for {router_name}...")

        # Find the router's last definition line (assumed to be eth1) and append RIP command
        # This makes sure RIP config is associated with the router device in Tacata processing
        router_eth1_line_start = f"{router_name}[1]={backbone_lan_name}"
        found_line_to_append = False
        for i in range(len(config_lines) - 1, -1, -1):
             line_parts = config_lines[i].split('$', 1)
             definition_part = line_parts[0].strip()

             # Check if this line starts with the router's eth1 definition
             if definition_part.startswith(router_eth1_line_start):
                 existing_commands = line_parts[1].strip() if len(line_parts) > 1 else ""
                 # Append new commands, ensuring semicolon separation if needed
                 if existing_commands and not existing_commands.endswith(';'):
                      existing_commands += ";"
                 # Ensure there's a space if adding to existing commands
                 separator = " " if existing_commands else ""
                 config_lines[i] = f"{definition_part}    ${existing_commands}{separator}{rip_command}"
                 print(f"    Appended RIP config to line {i+1}")
                 found_line_to_append = True
                 break # Stop searching once the correct line is found and updated

        if not found_line_to_append:
            print(f"  Warning: Could not find config line for '{router_eth1_line_start}' to append RIP config.")

    print("--- Configuration Generation Complete ---")
    # Return only config_lines. startup_lines is not populated in this version.
    return "\n".join(config_lines), "\n".join(startup_lines)


# ======================================================
# Main Execution Part
# ======================================================

if __name__ == "__main__":

    # --- Configuration Parameters ---
    # Randomly choose number of clusters (ensure >= 2 for routing)
    num_clusters = random.randint(2, 3)
    min_machines = 1  # Min client machines per cluster
    max_machines = 2  # Max client machines per cluster

    # Docker images to use (MAKE SURE THESE EXIST LOCALLY OR ON DOCKER HUB)
    client_image = "bensrubishcode/traffic_sensor" # Your existing client image
    router_image = "bensrubishcode/my_router_image"  # The name you used when building the router image

    print("+++ Starting Lab Generation +++")
    print(f"Target Clusters: {num_clusters}")
    print(f"Client Image: {client_image}")
    print(f"Router Image: {router_image}")

    # --- Delete existing configuration files ---
    confu_file = "lab.confu"
    startup_file = "startup" # Global startup, likely unused now

    if os.path.exists(confu_file):
        try:
            os.remove(confu_file)
            print(f"Deleted existing {confu_file}")
        except OSError as e:
            print(f"Error deleting {confu_file}: {e}")
    if os.path.exists(startup_file):
         try:
             os.remove(startup_file)
             print(f"Deleted existing {startup_file}")
         except OSError as e:
            print(f"Error deleting {startup_file}: {e}")


    # --- Generate the configurations using the new function ---
    lab_config_str, startup_commands_str = generate_lab_config_with_routers(
        num_clusters, min_machines, max_machines, client_image, router_image
    )

    # --- Write lab.confu file ---
    try:
        with open(confu_file, "w") as f:
            # Write line by line to avoid potential extra newlines issues
            lines = lab_config_str.splitlines()
            for i, line in enumerate(lines):
                 f.write(line.rstrip()) # Use rstrip to remove only trailing whitespace
                 if i < len(lines) - 1: # Add newline except for the last line
                     f.write("\n")
        print(f"Successfully generated {confu_file}")
    except IOError as e:
        print(f"Error writing {confu_file}: {e}")


    # --- Write global startup file (if needed) ---
    if startup_commands_str: # Only write if there's content
        try:
            with open(startup_file, "w") as f:
                # Write line by line
                lines = startup_commands_str.splitlines()
                for i, line in enumerate(lines):
                    f.write(line.rstrip())
                    if i < len(lines) - 1:
                        f.write("\n")
            print(f"Successfully generated {startup_file}")
        except IOError as e:
            print(f"Error writing {startup_file}: {e}")
    else:
        print(f"No content for global {startup_file}, file not created.")

    print("+++ Lab Generation Script Finished +++")
    
    # --- ADD THIS LINE TO OUTPUT THE NUMBER OF ROUTERS ---
    print(f"ROUTERS_GENERATED={num_clusters}")
