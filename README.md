# CASBAH

CASBAH (CMC's Assisted Server Build Automated Helper) is a Django web application for requesting and automating new VM builds across the CLL environment. It replaces ad hoc manual server-build steps with a single guided form, live infrastructure validation, and background orchestration against GitLab, NetBox, vCenter, Vault, and Active Directory.

This project is designed for internal operations use: a user can submit a server request, have the app validate the hostname/IP against existing records, create a GitLab build repository, provision the VM in NetBox, and monitor the Terraform/GitLab pipeline until the environment is ready.

## What the project does

CASBAH brings together a few major workflows:

- A web form for new server build requests
- Site-specific validation for RCD09 and SJC17 environments
- Live inventory lookups from vCenter and NetBox to populate dropdowns
- Hostname and IPv4 validation against existing build history and infrastructure data
- Automatic GitLab project/repo creation for each VM build
- Background orchestration of Terraform plan/apply through GitLab pipelines
- Optional firewall ticket creation in Jira for the build
- Build status tracking and CSV export for operational review

## Why it exists

The project is effectively a production-facing web frontend for a previously manual or semi-manual build process. Instead of editing YAML files and running scripts by hand, the app centralizes the workflow:

1. User requests a VM via the web form.
2. Validation checks the name and IP before submission.
3. The app creates or updates a `BuildRequest` record.
4. A background task creates the GitLab build repo and configures the VM in NetBox.
5. The GitLab pipeline monitors plan/merge/apply progression automatically.
6. The user sees status updates and final build information in the UI.

## Core architecture

The application is a Django project with one main app, `builds/`.

- `casbah/` contains the Django project settings and root URL routing.
- `builds/models.py` defines `BuildRequest`, the central database model for each server build.
- `builds/forms.py` defines the server build form and validation rules.
- `builds/views.py` contains the form web views, validation endpoints, and status UI.
- `builds/tasks.py` runs the background build pipeline and advances GitLab/Terraform state transitions.
- `builds/gitlab_client.py` manages GitLab project creation, repo setup, and pipeline interactions.
- `builds/netbox_client.py` provisions and checks VM inventory in NetBox.
- `builds/vcenter_client.py` gathers live datacenter inventory for dropdowns.
- `builds/vault_client.py` retrieves service credentials from Vault.
- `builds/ad_auth.py` provides Active Directory-backed authentication.
- `builds/yaml_builder.py` creates the YAML used for the virtual machine build definition.

## Key features

### Server build form

The app provides a site-specific form for:

- VM hostname / naming convention
- IPv4 address and prefix
- CPU / memory sizing
- tenant group and tenant selection
- datastore, cluster, resource pool, folder, and portgroup
- template selection
- optional firewall ticket reason

This form is tailored for both `rcd09` and `sjc17` locations, with staging support for the RCD09 environment.

### Infrastructure integration

The project relies on live infrastructure APIs:

- GitLab: create build repos, poll pipeline states, and trigger merge/apply behavior
- NetBox: validate IPs and create VM records
- vCenter: fetch compute clusters, datastores, resource pools, folders, and templates
- Vault: source service credentials and support token bootstrap
- Active Directory: authenticate authorized users before allowing app access

### Background job processing

The app uses `django-q2` with SQLite-backed ORM task scheduling. That lets long-running GitLab and infrastructure steps continue in the background while the user watches status updates in the web UI.

### Status tracking

Each build request records detailed state in the database, including:

- pipeline state
- GitLab URLs and project IDs
- NetBox VM links
- Jira ticket URL and errors
- final error details
- timestamps for build progression

## Project structure

```text
.
├── builds/                         # Main Django app
│   ├── management/                # Custom management commands
│   ├── migrations/                # Database migrations
│   ├── templates/                 # HTML templates for the UI
│   ├── tests/                     # Unit tests for app behavior
│   ├── ad_auth.py                 # AD login backend
│   ├── forms.py                   # Build form and validation logic
│   ├── gitlab_client.py           # GitLab API integration
│   ├── jira_client.py             # Jira ticket automation
│   ├── models.py                  # BuildRequest model and state enum
│   ├── netbox_client.py           # NetBox VM management
│   ├── tasks.py                   # Background orchestration
│   ├── vcenter_client.py          # vCenter inventory lookups
│   ├── vault_client.py            # Vault credential retrieval
│   ├── views.py                  # Request handlers and status views
│   ├── yaml_builder.py            # YAML generation for build specs
│   └── widgets.py                # Custom form widgets
├── casbah/                        # Django project settings and root URL config
├── example_files/                 # Example VM YAML files
├── scripts/                       # Supporting scripts
├── static/                        # Static assets
├── templates/                     # Shared templates
├── .gitignore
├── db.sqlite3                    # Local SQLite database for development
├── manage.py                     # Django management entry point
├── requirements.txt              # Python dependencies
├── README.md                     # Project documentation
├── ai_prompt_revised.txt          # Design rationale and implementation notes
└── PrepareNewBuild.log           # Local operational notes
```

## Requirements

This project is built for an internal environment and expects access to a number of platform services.

### Software

- Python 3.10+ (Django 6.1 is used in this repo)
- Git
- A local or reachable VM with network access to:
  - GitLab
  - NetBox
  - vCenter
  - Vault
  - Active Directory / LDAP

### Python dependencies

Install dependencies with:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Local configuration

This project reads several credentials from the local environment and a shared secrets file. The code expects a file at `~/.api_variables_env` with values exported in shell format.

Example:

```bash
export VAULT_TOKEN="..."
export EMAIL_ADDRESS="user@example.com"
export JIRA_API_TOKEN="..."
export VM_BUILDS_DIR="$HOME/builds"
export AD_ALLOWED_GROUP_DN="CN=System_Administrator,OU=CLL Team,DC=cll,DC=cloud"
```

The app also supports encrypted Vault AppRole values for bootstrapping access, using the helper command that is referenced in the project settings and management commands.

## Running the app

1. Create the database schema:

```bash
python manage.py migrate
```

2. Create an admin or local user if needed:

```bash
python manage.py createsuperuser
```

3. Start the background task worker:

```bash
python manage.py qcluster
```

4. Start the web app:

```bash
python manage.py runserver 0.0.0.0:8000
```

5. Open the app in a browser at:

```text
http://localhost:8000/
```

## Typical usage

1. Log in with your Active Directory credentials.
2. Select the site (`rcd09` or `sjc17`).
3. Fill in the requested VM details.
4. Confirm that the hostname and IP are valid.
5. Submit the request.
6. Track the build on the status page while the app creates the GitLab repo and NetBox VM.
7. Wait for the GitLab/Terraform pipeline to complete and mark the build as successful.

## Notable management commands

A few custom commands are included for operational validation and support:

```bash
python manage.py ad_lookup
python manage.py set_vault_approle
python manage.py sync_local_build_repos
```

These are used to validate AD configuration, bootstrap Vault AppRole secrets, and synchronize local build repository state.

## Security and operational notes

This repository is clearly an internal operational tool, not a polished public-facing app. A few important caveats are worth noting:

- `DEBUG = True` is enabled in the Django settings.
- The app stores sensitive values in local environment and secret files rather than a hardened production secrets manager.
- Access control depends on AD group membership and LDAP bind behavior.
- The project assumes access to internal GitLab, NetBox, vCenter, Vault, and Jira infrastructure.
- The app is intended for internal use by authorized staff within the CLL environment.

## Testing

The repo includes a Django test suite under `builds/tests/` covering the application logic for auth, forms, GitLab interactions, NetBox, vCenter, and build workflows.

Example:

```bash
python manage.py test
```

## License

This repository does not currently include a `LICENSE` file, so the licensing status is unspecified at this time.

## Project status

This project is an internal automation tool for workstation/server provisioning workflows. It is functional for the targeted internal environment and is structured around real operational systems rather than generic cloud abstractions.
