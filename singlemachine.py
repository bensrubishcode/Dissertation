import os
import subprocess

def generate_single_machine_config(machine_name, docker_image):
    """
    Generate a basic configuration and startup command for a single Docker machine.

    :param machine_name: Name of the machine.
    :param docker_image: Docker image to use for the machine.
    :return: Configuration and startup command strings.
    """
    config_line = f"{machine_name}[0]=single_cluster     $ip(10.0.0.1/24);"
    
    startup_commands = [
        f"docker.run('{docker_image}', '{machine_name}');",
        f"docker.exec('{machine_name}', 'nmap -V');",
        "set_mac('{machine_name}', '02:00:00:00:00:01');"
    ]

    return config_line, "\n".join(startup_commands)

# Configuration for Docker
docker_image = "bensrubishcode/traffic_sensor"  # Docker image name
machine_name = "traffickyboi2"

# Delete existing files if present
for file_name in ["lab.confu", "startup"]:
    if os.path.exists(file_name):
        os.remove(file_name)
        print(f"Existing {file_name} file deleted.")

# Generate the single machine configuration and startup command
lab_config, startup_commands = generate_single_machine_config(machine_name, docker_image)

# Write to lab.confu file
with open("lab.confu", "w") as f:
    f.write(lab_config + "\n")

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
