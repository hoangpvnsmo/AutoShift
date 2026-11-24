-- AutoShift 5.0. Apply using Alembic; trigger definitions are in autoshift5/db.py.

PRAGMA foreign_keys=ON;

PRAGMA journal_mode=WAL;

PRAGMA busy_timeout=15000;


CREATE TABLE annual_plans (
	id INTEGER NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	year INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	assumptions TEXT NOT NULL, 
	baseline_frozen BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (year BETWEEN 1900 AND 2200)
)

;


CREATE TABLE audit_logs (
	id INTEGER NOT NULL, 
	action VARCHAR(60) NOT NULL, 
	entity_id VARCHAR(80) NOT NULL, 
	payload JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
)

;


CREATE TABLE employees (
	id INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	"group" VARCHAR(8) NOT NULL, 
	is_leader BOOLEAN NOT NULL, 
	active_from DATE NOT NULL, 
	active_until DATE, 
	PRIMARY KEY (id), 
	CHECK (id>0 AND length(name)>0 AND "group" IN ('SHIFT','ADMIN')), 
	CHECK (active_until IS NULL OR active_until>=active_from)
)

;


CREATE TABLE input_state (
	id INTEGER NOT NULL, 
	revision INTEGER NOT NULL, 
	PRIMARY KEY (id)
)

;


CREATE TABLE rule_profiles (
	id INTEGER NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	revision INTEGER NOT NULL, 
	rules JSON NOT NULL, 
	work_policy JSON, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name, revision), 
	CHECK (revision>0)
)

;


CREATE TABLE work_codes (
	code VARCHAR(5) NOT NULL, 
	credit INTEGER NOT NULL, 
	PRIMARY KEY (code), 
	CHECK (code IN ('1','2','3','OFF','CT','DT','BKD','T'))
)

;


CREATE TABLE employee_permissions (
	id INTEGER NOT NULL, 
	employee_id INTEGER NOT NULL, 
	shift INTEGER NOT NULL, 
	valid_from DATE NOT NULL, 
	valid_until DATE, 
	PRIMARY KEY (id), 
	UNIQUE (employee_id, shift, valid_from), 
	CHECK (shift IN (1,2,3)), 
	FOREIGN KEY(employee_id) REFERENCES employees (id)
)

;

CREATE INDEX ix_employee_permissions_employee_id ON employee_permissions (employee_id);


CREATE TABLE planning_months (
	id INTEGER NOT NULL, 
	year INTEGER NOT NULL, 
	month INTEGER NOT NULL, 
	annual_plan_id INTEGER, 
	rule_profile_id INTEGER NOT NULL, 
	current_version_id VARCHAR(36), 
	baseline_version_id VARCHAR(36), 
	input_confirmed BOOLEAN NOT NULL, 
	assumptions TEXT NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (year, month), 
	CHECK (month BETWEEN 1 AND 12), 
	CHECK (year BETWEEN 1900 AND 2200), 
	FOREIGN KEY(annual_plan_id) REFERENCES annual_plans (id), 
	FOREIGN KEY(rule_profile_id) REFERENCES rule_profiles (id), 
	FOREIGN KEY(current_version_id) REFERENCES schedule_versions (id), 
	FOREIGN KEY(baseline_version_id) REFERENCES schedule_versions (id)
)

;

CREATE INDEX ix_planning_months_annual_plan_id ON planning_months (annual_plan_id);


CREATE TABLE preferences (
	id INTEGER NOT NULL, 
	employee_id INTEGER NOT NULL, 
	day DATE NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	shift INTEGER, 
	weight INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (employee_id, day), 
	CHECK (weight BETWEEN 1 AND 10), 
	CHECK ((kind IN ('PREFER_SHIFT','AVOID_SHIFT') AND shift IN (1,2,3)) OR (kind IN ('PREFER_OFF','PREFER_WORK') AND shift IS NULL)), 
	FOREIGN KEY(employee_id) REFERENCES employees (id)
)

;

CREATE INDEX ix_preferences_day ON preferences (day);


CREATE TABLE requests (
	id VARCHAR(36) NOT NULL, 
	employee_id INTEGER NOT NULL, 
	day DATE NOT NULL, 
	code VARCHAR(5) NOT NULL, 
	reason TEXT NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	revision INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','APPLIED','REJECTED','CANCELLED')), 
	FOREIGN KEY(employee_id) REFERENCES employees (id), 
	FOREIGN KEY(code) REFERENCES work_codes (code)
)

;

CREATE INDEX ix_requests_employee_id ON requests (employee_id);

CREATE UNIQUE INDEX uq_request_active_cell ON requests (employee_id, day) WHERE status NOT IN ('REJECTED','CANCELLED');

CREATE INDEX ix_requests_day ON requests (day);


CREATE TABLE shift_types (
	code VARCHAR(5) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	PRIMARY KEY (code), 
	FOREIGN KEY(code) REFERENCES work_codes (code)
)

;


CREATE TABLE employee_monthly_settings (
	month_id INTEGER NOT NULL, 
	employee_id INTEGER NOT NULL, 
	position INTEGER NOT NULL, 
	target_credit INTEGER, 
	name VARCHAR(200) NOT NULL, 
	"group" VARCHAR(8) NOT NULL, 
	is_leader BOOLEAN NOT NULL, 
	PRIMARY KEY (month_id, employee_id), 
	CHECK (target_credit IS NULL OR target_credit>=0), 
	FOREIGN KEY(month_id) REFERENCES planning_months (id), 
	FOREIGN KEY(employee_id) REFERENCES employees (id)
)

;


CREATE TABLE historical_assignments (
	month_id INTEGER NOT NULL, 
	employee_id INTEGER NOT NULL, 
	day DATE NOT NULL, 
	code VARCHAR(5) NOT NULL, 
	PRIMARY KEY (month_id, employee_id, day), 
	FOREIGN KEY(month_id) REFERENCES planning_months (id), 
	FOREIGN KEY(employee_id) REFERENCES employees (id), 
	FOREIGN KEY(code) REFERENCES work_codes (code)
)

;


CREATE TABLE holidays (
	month_id INTEGER NOT NULL, 
	day DATE NOT NULL, 
	note TEXT NOT NULL, 
	PRIMARY KEY (month_id, day), 
	FOREIGN KEY(month_id) REFERENCES planning_months (id)
)

;


CREATE TABLE import_records (
	digest VARCHAR(64) NOT NULL, 
	month_id INTEGER NOT NULL, 
	report JSON NOT NULL, 
	PRIMARY KEY (digest), 
	FOREIGN KEY(month_id) REFERENCES planning_months (id)
)

;


CREATE TABLE input_snapshots (
	id VARCHAR(36) NOT NULL, 
	month_id INTEGER NOT NULL, 
	revision INTEGER NOT NULL, 
	digest VARCHAR(64) NOT NULL, 
	payload JSON NOT NULL, 
	source_hashes JSON NOT NULL, 
	work_policy JSON, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(month_id) REFERENCES planning_months (id)
)

;

CREATE INDEX ix_input_snapshots_month_id ON input_snapshots (month_id);


CREATE TABLE request_revisions (
	id INTEGER NOT NULL, 
	request_id VARCHAR(36) NOT NULL, 
	revision INTEGER NOT NULL, 
	payload JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (request_id, revision), 
	FOREIGN KEY(request_id) REFERENCES requests (id)
)

;

CREATE INDEX ix_request_revisions_request_id ON request_revisions (request_id);


CREATE TABLE schedule_locks (
	month_id INTEGER NOT NULL, 
	employee_id INTEGER NOT NULL, 
	day DATE NOT NULL, 
	reason TEXT NOT NULL, 
	PRIMARY KEY (month_id, employee_id, day), 
	FOREIGN KEY(month_id) REFERENCES planning_months (id), 
	FOREIGN KEY(employee_id) REFERENCES employees (id)
)

;


CREATE TABLE solver_runs (
	id VARCHAR(36) NOT NULL, 
	scope_key VARCHAR(50) NOT NULL, 
	month_id INTEGER, 
	annual_plan_id INTEGER, 
	mode VARCHAR(12) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	revision INTEGER NOT NULL, 
	options JSON NOT NULL, 
	snapshots JSON NOT NULL, 
	result JSON, 
	error TEXT, 
	log TEXT NOT NULL, 
	stage VARCHAR(150) NOT NULL, 
	cancel_requested BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	started_at DATETIME, 
	finished_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(month_id) REFERENCES planning_months (id), 
	FOREIGN KEY(annual_plan_id) REFERENCES annual_plans (id)
)

;

CREATE UNIQUE INDEX uq_active_job_scope ON solver_runs (scope_key) WHERE status IN ('QUEUED','RUNNING','CANCELLING');


CREATE TABLE staffing_demands (
	month_id INTEGER NOT NULL, 
	day DATE NOT NULL, 
	shift INTEGER NOT NULL, 
	count INTEGER NOT NULL, 
	PRIMARY KEY (month_id, day, shift), 
	CHECK (shift IN (1,2,3) AND count>=0), 
	FOREIGN KEY(month_id) REFERENCES planning_months (id)
)

;


CREATE TABLE schedule_versions (
	id VARCHAR(36) NOT NULL, 
	month_id INTEGER NOT NULL, 
	parent_version_id VARCHAR(36), 
	annual_baseline_id VARCHAR(36), 
	snapshot_id VARCHAR(36) NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	source VARCHAR(30) NOT NULL, 
	proof JSON NOT NULL, 
	validation_status VARCHAR(20) NOT NULL, 
	input_revision INTEGER NOT NULL, 
	confirmed_at DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (status IN ('DRAFT','PLANNED','PUBLISHED','ACTUAL')), 
	FOREIGN KEY(month_id) REFERENCES planning_months (id), 
	FOREIGN KEY(parent_version_id) REFERENCES schedule_versions (id), 
	FOREIGN KEY(annual_baseline_id) REFERENCES schedule_versions (id), 
	FOREIGN KEY(snapshot_id) REFERENCES input_snapshots (id)
)

;

CREATE INDEX ix_schedule_versions_month_id ON schedule_versions (month_id);


CREATE TABLE objective_scores (
	version_id VARCHAR(36) NOT NULL, 
	layer VARCHAR(30) NOT NULL, 
	value JSON NOT NULL, 
	PRIMARY KEY (version_id, layer), 
	FOREIGN KEY(version_id) REFERENCES schedule_versions (id)
)

;


CREATE TABLE schedule_assignments (
	version_id VARCHAR(36) NOT NULL, 
	employee_id INTEGER NOT NULL, 
	day DATE NOT NULL, 
	code VARCHAR(5) NOT NULL, 
	PRIMARY KEY (version_id, employee_id, day), 
	CHECK (code IN ('1','2','3','OFF','CT','DT','BKD','T')), 
	FOREIGN KEY(version_id) REFERENCES schedule_versions (id), 
	FOREIGN KEY(employee_id) REFERENCES employees (id), 
	FOREIGN KEY(code) REFERENCES work_codes (code)
)

;

CREATE INDEX ix_assignment_date_employee ON schedule_assignments (day, employee_id);


CREATE TABLE schedule_changes (
	id INTEGER NOT NULL, 
	version_id VARCHAR(36) NOT NULL, 
	employee_id INTEGER NOT NULL, 
	day DATE NOT NULL, 
	"before" VARCHAR(5) NOT NULL, 
	"after" VARCHAR(5) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(version_id) REFERENCES schedule_versions (id), 
	FOREIGN KEY(employee_id) REFERENCES employees (id), 
	FOREIGN KEY("before") REFERENCES work_codes (code), 
	FOREIGN KEY("after") REFERENCES work_codes (code)
)

;

CREATE INDEX ix_schedule_changes_version_id ON schedule_changes (version_id);


CREATE TABLE validation_results (
	id INTEGER NOT NULL, 
	version_id VARCHAR(36) NOT NULL, 
	payload JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(version_id) REFERENCES schedule_versions (id)
)

;

CREATE INDEX ix_validation_results_version_id ON validation_results (version_id);

-- Immutable history triggers
CREATE TRIGGER protect_confirmed_assignment_insert BEFORE INSERT ON schedule_assignments
        WHEN (SELECT status FROM schedule_versions WHERE id=NEW.version_id) != 'DRAFT'
        BEGIN SELECT RAISE(ABORT, 'confirmed assignments are immutable'); END;

CREATE TRIGGER protect_confirmed_versions BEFORE UPDATE ON schedule_versions
        WHEN OLD.status IN ('PLANNED','PUBLISHED','ACTUAL')
        BEGIN SELECT RAISE(ABORT, 'confirmed version is immutable; create a child'); END;

CREATE TRIGGER protect_input_snapshots_delete BEFORE DELETE ON input_snapshots BEGIN SELECT RAISE(ABORT, 'immutable history'); END;

CREATE TRIGGER protect_input_snapshots_update BEFORE UPDATE ON input_snapshots BEGIN SELECT RAISE(ABORT, 'immutable history'); END;

CREATE TRIGGER protect_rule_profiles_delete BEFORE DELETE ON rule_profiles BEGIN SELECT RAISE(ABORT, 'immutable history'); END;

CREATE TRIGGER protect_rule_profiles_update BEFORE UPDATE ON rule_profiles BEGIN SELECT RAISE(ABORT, 'immutable history'); END;

CREATE TRIGGER protect_schedule_assignments_delete BEFORE DELETE ON schedule_assignments BEGIN SELECT RAISE(ABORT, 'immutable history'); END;

CREATE TRIGGER protect_schedule_assignments_update BEFORE UPDATE ON schedule_assignments BEGIN SELECT RAISE(ABORT, 'immutable history'); END;

CREATE TRIGGER protect_schedule_changes_delete BEFORE DELETE ON schedule_changes BEGIN SELECT RAISE(ABORT, 'immutable history'); END;

CREATE TRIGGER protect_schedule_changes_update BEFORE UPDATE ON schedule_changes BEGIN SELECT RAISE(ABORT, 'immutable history'); END;

CREATE TRIGGER protect_version_delete BEFORE DELETE ON schedule_versions BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
