# Define required providers
terraform {
  required_version = ">= 0.14.0"
  required_providers {
    openstack = {
      source  = "terraform-provider-openstack/openstack"
      version = "~> 3.0"
    }
  }
}

# Configure the OpenStack Provider
provider "openstack" {
  cloud = "chameleon"
}

# Variable are used by CLI to insert dynamic values
# Create a Chameleon VM name
variable "name" {
  default     = "aifa-cpu-l"
  description = "Chameleon VM name"
}

variable "reservation_id" {}

# Create the ssh remote access to the chameleon VM
resource "openstack_networking_secgroup_v2" "ssh" {
  name        = "${var.name}-ssh"
  description = "Create ssh remote access"
}

# Create VM ruleset
resource "openstack_networking_secgroup_rule_v2" "ssh" {
  security_group_id = openstack_networking_secgroup_v2.ssh.id
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 22
  port_range_max    = 22
  remote_ip_prefix  = "0.0.0.0/0"
}

# Read the existing shared network (data source: not created or destroyed by Terraform)
data "openstack_networking_network_v2" "network" {
  name = "sharednet1"
}

# Connect Chamelon's VM to the network
resource "openstack_networking_port_v2" "vm" {
  name               = var.name
  network_id         = data.openstack_networking_network_v2.network.id
  security_group_ids = [openstack_networking_secgroup_v2.ssh.id]
}

resource "openstack_compute_instance_v2" "vm" {
  name       = var.name
  image_name = "CC-Ubuntu24.04"
  flavor_id  = var.reservation_id
  key_pair   = "farmaci-h100"

  network {
    port = openstack_networking_port_v2.vm.id
  }
}

resource "openstack_networking_floatingip_v2" "ip" {
  pool        = "public"
  description = var.name
  port_id     = openstack_networking_port_v2.vm.id
}

output "ip" {
  value = openstack_networking_floatingip_v2.ip.address
}