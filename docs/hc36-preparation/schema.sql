-- HC3.6 empty control schema; no seed rows or migration.

CREATE TABLE proxmox_reservations (
	id INTEGER NOT NULL, 
	reservation_id VARCHAR(64) NOT NULL, 
	request_id VARCHAR(64) NOT NULL, 
	idempotency_key VARCHAR(128) NOT NULL, 
	tenant_id VARCHAR(128) NOT NULL, 
	customer_id VARCHAR(128), 
	node_id VARCHAR(64) NOT NULL, 
	storage_pool VARCHAR(64), 
	vcpu INTEGER NOT NULL, 
	ram_gb INTEGER NOT NULL, 
	disk_gb INTEGER NOT NULL, 
	template_id VARCHAR(128), 
	status VARCHAR(32) NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	expires_at DATETIME, 
	released_at DATETIME, 
	consumed_at DATETIME, 
	failed_at DATETIME, 
	expired_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_proxmox_reservation_id UNIQUE (reservation_id), 
	CONSTRAINT uq_proxmox_reservation_idempotency UNIQUE (idempotency_key), 
	CONSTRAINT uq_proxmox_reservation_request UNIQUE (request_id)
);

CREATE INDEX ix_proxmox_reservation_expires ON proxmox_reservations (expires_at);

CREATE INDEX ix_proxmox_reservation_node ON proxmox_reservations (node_id);

CREATE INDEX ix_proxmox_reservation_status ON proxmox_reservations (status);

CREATE INDEX ix_proxmox_reservation_tenant ON proxmox_reservations (tenant_id);

CREATE INDEX ix_proxmox_reservations_expires_at ON proxmox_reservations (expires_at);

CREATE UNIQUE INDEX ix_proxmox_reservations_idempotency_key ON proxmox_reservations (idempotency_key);

CREATE INDEX ix_proxmox_reservations_node_id ON proxmox_reservations (node_id);

CREATE UNIQUE INDEX ix_proxmox_reservations_request_id ON proxmox_reservations (request_id);

CREATE UNIQUE INDEX ix_proxmox_reservations_reservation_id ON proxmox_reservations (reservation_id);

CREATE INDEX ix_proxmox_reservations_status ON proxmox_reservations (status);

CREATE INDEX ix_proxmox_reservations_tenant_id ON proxmox_reservations (tenant_id);

CREATE TABLE proxmox_provisioning_jobs (
	id INTEGER NOT NULL, 
	job_id VARCHAR(64) NOT NULL, 
	request_id VARCHAR(64) NOT NULL, 
	reservation_id VARCHAR(64) NOT NULL, 
	idempotency_key VARCHAR(128) NOT NULL, 
	tenant_id VARCHAR(128) NOT NULL, 
	customer_id VARCHAR(128), 
	provider VARCHAR(32) NOT NULL, 
	node_id VARCHAR(64) NOT NULL, 
	storage_pool VARCHAR(64), 
	template_id VARCHAR(128), 
	hostname VARCHAR(128), 
	vcpu INTEGER NOT NULL, 
	ram_gb INTEGER NOT NULL, 
	disk_gb INTEGER NOT NULL, 
	state VARCHAR(32) NOT NULL, 
	attempt_count INTEGER NOT NULL, 
	max_attempts INTEGER NOT NULL, 
	last_error_code VARCHAR(64), 
	last_error_message VARCHAR(512), 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	started_at DATETIME, 
	completed_at DATETIME, 
	failed_at DATETIME, 
	next_retry_at DATETIME, 
	version INTEGER NOT NULL, 
	claimed_by VARCHAR(128), 
	claimed_at DATETIME, 
	fake_resource_id VARCHAR(128), 
	has_partial_resource BOOLEAN NOT NULL, 
	provider_mode VARCHAR(32), 
	plan_fingerprint VARCHAR(128), 
	plan_schema_version INTEGER, 
	target_vmid INTEGER, 
	provider_task_id VARCHAR(128), 
	worker_lease_expires_at DATETIME, 
	last_reconciled_at DATETIME, 
	dry_run_result_json TEXT, 
	ownership_fingerprint VARCHAR(128), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_proxmox_job_id UNIQUE (job_id), 
	CONSTRAINT uq_proxmox_job_request UNIQUE (request_id), 
	CONSTRAINT uq_proxmox_job_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX ix_proxmox_job_next_retry ON proxmox_provisioning_jobs (next_retry_at);

CREATE INDEX ix_proxmox_job_node ON proxmox_provisioning_jobs (node_id);

CREATE INDEX ix_proxmox_job_reservation ON proxmox_provisioning_jobs (reservation_id);

CREATE INDEX ix_proxmox_job_state ON proxmox_provisioning_jobs (state);

CREATE INDEX ix_proxmox_job_tenant ON proxmox_provisioning_jobs (tenant_id);

CREATE UNIQUE INDEX ix_proxmox_provisioning_jobs_idempotency_key ON proxmox_provisioning_jobs (idempotency_key);

CREATE UNIQUE INDEX ix_proxmox_provisioning_jobs_job_id ON proxmox_provisioning_jobs (job_id);

CREATE INDEX ix_proxmox_provisioning_jobs_next_retry_at ON proxmox_provisioning_jobs (next_retry_at);

CREATE INDEX ix_proxmox_provisioning_jobs_node_id ON proxmox_provisioning_jobs (node_id);

CREATE INDEX ix_proxmox_provisioning_jobs_plan_fingerprint ON proxmox_provisioning_jobs (plan_fingerprint);

CREATE UNIQUE INDEX ix_proxmox_provisioning_jobs_request_id ON proxmox_provisioning_jobs (request_id);

CREATE INDEX ix_proxmox_provisioning_jobs_reservation_id ON proxmox_provisioning_jobs (reservation_id);

CREATE INDEX ix_proxmox_provisioning_jobs_state ON proxmox_provisioning_jobs (state);

CREATE INDEX ix_proxmox_provisioning_jobs_target_vmid ON proxmox_provisioning_jobs (target_vmid);

CREATE INDEX ix_proxmox_provisioning_jobs_tenant_id ON proxmox_provisioning_jobs (tenant_id);

CREATE TABLE proxmox_vmid_leases (
	id INTEGER NOT NULL, 
	vmid INTEGER NOT NULL, 
	cluster_fingerprint VARCHAR(128) NOT NULL, 
	job_id VARCHAR(64) NOT NULL, 
	request_id VARCHAR(64), 
	state VARCHAR(32) NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	consumed_at DATETIME, 
	released_at DATETIME, 
	conflict_reason VARCHAR(256), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_proxmox_vmid_cluster_vmid UNIQUE (cluster_fingerprint, vmid)
);

CREATE INDEX ix_proxmox_vmid_cluster ON proxmox_vmid_leases (cluster_fingerprint);

CREATE INDEX ix_proxmox_vmid_job ON proxmox_vmid_leases (job_id);

CREATE INDEX ix_proxmox_vmid_leases_job_id ON proxmox_vmid_leases (job_id);

CREATE INDEX ix_proxmox_vmid_leases_state ON proxmox_vmid_leases (state);

CREATE INDEX ix_proxmox_vmid_state ON proxmox_vmid_leases (state);

CREATE TABLE proxmox_provisioning_audit_events (
	id INTEGER NOT NULL, 
	event_id VARCHAR(64) NOT NULL, 
	job_id VARCHAR(64), 
	reservation_id VARCHAR(64), 
	request_id VARCHAR(64), 
	tenant_id VARCHAR(128), 
	event_type VARCHAR(64) NOT NULL, 
	operation_key VARCHAR(128), 
	from_state VARCHAR(32), 
	to_state VARCHAR(32), 
	attempt INTEGER NOT NULL, 
	provider VARCHAR(32), 
	provider_mode VARCHAR(32), 
	cluster_fingerprint VARCHAR(128), 
	node_id VARCHAR(64), 
	target_vmid INTEGER, 
	plan_fingerprint VARCHAR(128), 
	provider_task_id VARCHAR(128), 
	outcome_code VARCHAR(64), 
	message VARCHAR(512), 
	actor_type VARCHAR(32), 
	actor_id VARCHAR(128), 
	meta_json TEXT, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_proxmox_audit_event_type ON proxmox_provisioning_audit_events (event_type);

CREATE INDEX ix_proxmox_audit_job ON proxmox_provisioning_audit_events (job_id);

CREATE UNIQUE INDEX ix_proxmox_provisioning_audit_events_event_id ON proxmox_provisioning_audit_events (event_id);

CREATE INDEX ix_proxmox_provisioning_audit_events_event_type ON proxmox_provisioning_audit_events (event_type);

CREATE INDEX ix_proxmox_provisioning_audit_events_job_id ON proxmox_provisioning_audit_events (job_id);

CREATE TABLE proxmox_clone_approvals (
	approval_id VARCHAR(64) NOT NULL, 
	binding VARCHAR(64) NOT NULL, 
	action VARCHAR(32) NOT NULL, 
	operator_id VARCHAR(128) NOT NULL, 
	expires_at DATETIME NOT NULL, 
	consumed BOOLEAN NOT NULL, 
	PRIMARY KEY (approval_id)
);

CREATE TABLE proxmox_clone_intents (
	job_id VARCHAR(64) NOT NULL, 
	binding VARCHAR(64) NOT NULL, 
	approval_id VARCHAR(64) NOT NULL, 
	phase VARCHAR(40) NOT NULL, 
	upid VARCHAR(128), 
	PRIMARY KEY (job_id)
);

CREATE TABLE proxmox_mutation_controls (
	control_id INTEGER NOT NULL, 
	kill_switch BOOLEAN NOT NULL, 
	owner_job_id VARCHAR(64), 
	PRIMARY KEY (control_id)
);
