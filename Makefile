# Benchmark VM on Chameleon KVM@TACC, end to end:
#   make lease up deploy bench   reserve, create, configure, start the benchmark plan
#   make fetch down              copy results back, destroy VM and lease
# Lease = OpenStack CLI (Blazar is not covered by the Terraform provider), VM = Terraform (infra/).
# Every target except bench/ssh appends its wall-clock time to $(TIMES): time-to-ready = lease + up.

CLOUD_NAME     = chameleon
LEASE_NAME     = aifa-cpu-l
# m1.xxlarge (16 vCPU, 32 GB), see `openstack flavor list`
FLAVOR_ID      = 6
# Lease duration in hours
LEASE_DURATION = 10
CONFIG         = cloud-cpu-l
PLAN           = bench/plan_$(CONFIG).sh
SSH_KEY        = $(HOME)/.ssh/chameleon_farmaci
SAMPLE         = samples/n500.tar.gz
SAMPLE_DIR     = $(SAMPLE:.tar.gz=)
REPO           = aifa-rag-cloud
OPENSTACK      = openstack --os-cloud $(CLOUD_NAME)
TIMES          = results/infra_times.csv
# Throwaway VMs get a new host key every time and floating IPs get reused: skip known_hosts
SSH            = ssh -i $(SSH_KEY) -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10
# Floating IP from the Terraform state, looked up only when a recipe uses it
REMOTE         = cc@$(shell terraform -chdir=infra output -raw ip 2>/dev/null)
# Appends "<target>,start,end,seconds"; the recipe must set t0 first
LOG_TIME       = t1=$$(date +%s) && { test -f $(TIMES) || echo "target,start,end,seconds" > $(TIMES); } && echo "$@,$$t0,$$t1,$$((t1-t0))" >> $(TIMES)

.PHONY: lease up deploy bench fetch down ssh

# Reserve the flavor and wait until the lease is ACTIVE.
# date -v is BSD/macOS syntax (GNU/Linux: date -d "+N hours")
lease:
	@t0=$$(date +%s) && \
	END=$$(date -u -v+$(LEASE_DURATION)H "+%Y-%m-%d %H:%M") && \
	$(OPENSTACK) reservation lease create --reservation resource_type=flavor:instance,flavor_id=$(FLAVOR_ID),amount=1 --end-date "$$END" $(LEASE_NAME) && \
	until S=$$($(OPENSTACK) reservation lease show $(LEASE_NAME) -c status -f value) && \
	      [ "$$S" = ACTIVE ]; do \
	  [ "$$S" = ERROR ] && { echo "lease in ERROR"; exit 1; }; \
	  echo "lease: $$S"; sleep 10; \
	done && \
	$(LOG_TIME)

# Create the VM on the reserved flavor (on KVM its id is the reservation id) and wait for SSH.
# The reservations field is a JSON string inside the JSON, hence fromjson.
up:
	@t0=$$(date +%s) && \
	RES=$$($(OPENSTACK) reservation lease show $(LEASE_NAME) -f json -c reservations | jq -r '.reservations | fromjson | .id') && \
	{ test -n "$$RES" || { echo "no lease $(LEASE_NAME): run make lease first"; exit 1; }; } && \
	terraform -chdir=infra init -input=false > /dev/null && \
	terraform -chdir=infra apply -auto-approve -var reservation_id=$$RES -var name=$(LEASE_NAME) && \
	IP=$$(terraform -chdir=infra output -raw ip) && \
	until $(SSH) cc@$$IP true 2>/dev/null; do \
	  echo "waiting for ssh on $$IP"; sleep 10; \
	done && \
	$(LOG_TIME) && \
	echo "VM ready: make ssh"

# Code as a git bundle (exact commit, no GitHub key on the VM), sample as a tar stream,
# then system packages + venv (bootstrap.sh) and the embedding model download,
# so that the first s4 run does not include it. Re-running updates the existing clone.
deploy:
	@t0=$$(date +%s) && \
	echo "deploy $$(git log -1 --format='%h %s')" && \
	{ git cat-file -e HEAD:$(PLAN) 2>/dev/null || { echo "$(PLAN) is not committed: the bundle only carries commits"; exit 1; }; } && \
	{ git diff --quiet HEAD || echo "WARNING: uncommitted changes are not deployed"; } && \
	git bundle create - HEAD | $(SSH) $(REMOTE) 'cat > $(REPO).bundle' && \
	$(SSH) $(REMOTE) 'cloud-init status --wait > /dev/null; \
	  command -v git > /dev/null || { sudo apt-get update -qq && sudo apt-get install -y -qq git; }; \
	  if [ -d $(REPO) ]; then git -C $(REPO) fetch -q ../$(REPO).bundle && git -C $(REPO) reset -q --hard FETCH_HEAD; \
	  else git clone -q $(REPO).bundle $(REPO); fi && \
	  mkdir -p $(REPO)/$(SAMPLE_DIR)' && \
	$(SSH) $(REMOTE) 'tar -xzf - -C $(REPO)/$(SAMPLE_DIR)' < $(SAMPLE) && \
	$(SSH) $(REMOTE) 'bash $(REPO)/chameleon/bootstrap.sh && \
	  $(REPO)/.venv/bin/python -c "from sentence_transformers import SentenceTransformer as M; M(\"BAAI/bge-m3\")"' && \
	$(LOG_TIME)

# Start the benchmark plan in tmux (survives SSH disconnects); log in ~/bench.log, last line BENCH_DONE
bench:
	@$(SSH) $(REMOTE) 'tmux new -d -s bench "bash $(REPO)/$(PLAN) > bench.log 2>&1"' && \
	echo "started $(PLAN): make ssh, then tail -f bench.log"

# Results without the per-run data dirs, then only the sample link and sqlite that collect.py reads
fetch:
	@t0=$$(date +%s) && \
	rsync -a -e "$(SSH)" --exclude='data/' $(REMOTE):$(REPO)/results/$(CONFIG)/ results/$(CONFIG)/ && \
	rsync -a -e "$(SSH)" --include='*/' --include='data/pdf' --include='data/farmaci.sqlite' --exclude='data/**' \
	  $(REMOTE):$(REPO)/results/$(CONFIG)/ results/$(CONFIG)/ && \
	$(LOG_TIME)

# Destroy the VM (destroy needs the variables declared; reservation_id is not used), then the lease.
# The floating IP is released by Terraform, unlike the manual procedure.
down:
	@t0=$$(date +%s) && \
	terraform -chdir=infra destroy -auto-approve -var reservation_id=none -var name=$(LEASE_NAME) && \
	{ $(OPENSTACK) reservation lease delete $(LEASE_NAME) || echo "lease $(LEASE_NAME) already gone"; } && \
	$(LOG_TIME)

ssh:
	$(SSH) $(REMOTE)
