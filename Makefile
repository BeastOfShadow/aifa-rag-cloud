# Benchmark VM on Chameleon KVM@TACC, end to end:
#   make lease up deploy bench   reserve, create, configure, start the benchmark plan
#   make fetch down              copy results back, destroy VM and lease
# Lease = OpenStack CLI (Blazar is not covered by the Terraform provider), VM = Terraform (infra/).
# Every target except bench/ssh appends its wall-clock time to $(TIMES): time-to-ready = lease + up.
# Each lease has its own Terraform workspace (TF_WORKSPACE = LEASE_NAME), so VMs can run in parallel:
# every command for a VM must carry its LEASE_NAME.
#
# Split configuration (conversion on a CPU VM, embedding on the H100), one VM after the other:
#   make lease up deploy bench CONFIG=split-cpu LEASE_NAME=aifa-split-cpu
#   make fetch fetch-md down CONFIG=split-cpu LEASE_NAME=aifa-split-cpu
#   make lease up deploy push-md bench CONFIG=split-gpu SPLIT_FROM=split-cpu LEASE_NAME=aifa-split-gpu \
#        FLAVOR_ID=<h100 flavor id> IMAGE=CC-Ubuntu24.04-CUDA VOLUME_GB=30
#   make fetch down CONFIG=split-gpu LEASE_NAME=aifa-split-gpu

CLOUD_NAME     = chameleon
LEASE_NAME     = aifa-cpu-l
# m1.xxlarge (16 vCPU, 32 GB), see `openstack flavor list`
FLAVOR_ID      = 6
# Lease duration in hours
LEASE_DURATION = 10
CONFIG         = cloud-cpu-l
PLAN           = bench/plan_$(CONFIG).sh
IMAGE          = CC-Ubuntu24.04
# Extra disk mounted on /mnt/vol, 0 = none
VOLUME_GB      = 0
# Split configuration: config whose fetched Markdown push-md sends to this VM
SPLIT_FROM     = split-cpu
SSH_KEY        = $(HOME)/.ssh/chameleon_farmaci
SAMPLE         = samples/n500.tar.gz
SAMPLE_DIR     = $(SAMPLE:.tar.gz=)
REPO           = aifa-rag-cloud
OPENSTACK      = openstack --os-cloud $(CLOUD_NAME)
TIMES          = results/infra_times.csv
# Throwaway VMs get a new host key every time and floating IPs get reused: skip known_hosts
SSH            = ssh -i $(SSH_KEY) -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10
# Terraform on this lease's workspace
TF             = TF_WORKSPACE=$(LEASE_NAME) terraform -chdir=infra
# Floating IP from the Terraform state, looked up only when a recipe uses it
REMOTE         = cc@$(shell $(TF) output -raw ip 2>/dev/null)
# Appends "<target>,start,end,seconds"; the recipe must set t0 first
LOG_TIME       = t1=$$(date +%s) && { test -f $(TIMES) || echo "target,start,end,seconds" > $(TIMES); } && echo "$@,$$t0,$$t1,$$((t1-t0))" >> $(TIMES)

.PHONY: lease up deploy bench fetch fetch-md push-md down ssh map

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
	{ terraform -chdir=infra workspace list | grep -qw "$(LEASE_NAME)" || \
	  { terraform -chdir=infra workspace new $(LEASE_NAME) && terraform -chdir=infra workspace select default; } > /dev/null; } && \
	$(TF) apply -auto-approve -var reservation_id=$$RES -var name=$(LEASE_NAME) \
	  -var image=$(IMAGE) -var volume_gb=$(VOLUME_GB) && \
	IP=$$($(TF) output -raw ip) && \
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
	  if [ -b /dev/vdb ]; then \
	    { sudo blkid /dev/vdb > /dev/null || sudo mkfs.ext4 -q /dev/vdb; } && sudo mkdir -p /mnt/vol && \
	    { mountpoint -q /mnt/vol || sudo mount /dev/vdb /mnt/vol; } && sudo chown cc: /mnt/vol && \
	    mkdir -p /mnt/vol/samples /mnt/vol/results-$(CONFIG) && \
	    ln -sfn /mnt/vol/samples $(REPO)/samples && ln -sfn /mnt/vol/results-$(CONFIG) $(REPO)/results/$(CONFIG); \
	  fi && \
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

# Split configuration, CPU VM: also the Markdown and the database that s4 needs (~1.2 GB)
fetch-md:
	@t0=$$(date +%s) && \
	rsync -aL -e "$(SSH)" --include='*/' --include='data/md/**' --include='data/farmaci.sqlite' --exclude='*' \
	  $(REMOTE):$(REPO)/results/$(CONFIG)/ results/$(CONFIG)/ && \
	$(LOG_TIME)

# Split configuration, GPU VM: Markdown and database of the latest $(SPLIT_FROM) run -> samples/split.
# pdf/ stays empty but must exist: run.sh links it and config.py fails on a dangling link
push-md:
	@t0=$$(date +%s) && \
	SRC=$$(ls -d results/$(SPLIT_FROM)/*/data/md | tail -1 | xargs dirname) && \
	echo "push $$SRC" && \
	$(SSH) $(REMOTE) 'mkdir -p $(REPO)/samples/split/pdf' && \
	rsync -a -e "$(SSH)" $$SRC/farmaci.sqlite $$SRC/md $(REMOTE):$(REPO)/samples/split/ && \
	$(LOG_TIME)

# Destroy the VM (destroy needs the variables declared; reservation_id is not used), then the lease.
# The floating IP is released by Terraform, unlike the manual procedure.
down:
	@t0=$$(date +%s) && \
	$(TF) destroy -auto-approve -var reservation_id=none -var name=$(LEASE_NAME) && \
	terraform -chdir=infra workspace delete $(LEASE_NAME) > /dev/null && \
	{ $(OPENSTACK) reservation lease delete $(LEASE_NAME) || echo "lease $(LEASE_NAME) already gone"; } && \
	$(LOG_TIME)

ssh:
	$(SSH) $(REMOTE)

# Embedding map with the document texts: serves docs/ on localhost only, with data/md linked as docs/md
# (from file:// the browser cannot read local files). Ctrl-C to stop.
map:
	ln -sfn ../data/md docs/md
	(sleep 1 && python3 -m webbrowser -t http://127.0.0.1:8000/embedding-space.html) &
	python3 -m http.server 8000 --bind 127.0.0.1 -d docs
