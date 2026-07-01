# Roadmap

## Current Goal

`openai-tunnel-kit` is a portable CLI toolkit for setting up, managing, and debugging persistent `tunnel-client` plus MCP workflows on Linux systems.

The project should stay boring, explicit, reproducible, and easy to recover from.

## Milestone 1: Stable Core CLI

* [ ] Keep the CLI dependency-light, preferably Python standard library only.
* [ ] Support profile-based setup.
* [ ] Support user-level systemd service installation.
* [ ] Support service status checks.
* [ ] Provide clear errors when `tunnel-client`, `systemctl --user`, or expected config paths are missing.
* [x] Include a Bash venv/helper script for local development convenience.

Core commands:

```bash
openai-tunnel-kit init <profile>
openai-tunnel-kit install-service <profile>
openai-tunnel-kit status <profile>
openai-tunnel-kit doctor
```

## Milestone 2: Command Layer Split

Restructure commands around the actual layers of the tool. **Complete.**

Target shape:

```bash
openai-tunnel-kit profile init <profile>
openai-tunnel-kit profile list
openai-tunnel-kit profile show <profile>

openai-tunnel-kit service install <profile>
openai-tunnel-kit service uninstall <profile>
openai-tunnel-kit service start <profile>
openai-tunnel-kit service stop <profile>
openai-tunnel-kit service status <profile>

openai-tunnel-kit mcp add <profile> <file>
openai-tunnel-kit mcp list <profile>
openai-tunnel-kit mcp print <profile>

openai-tunnel-kit doctor
openai-tunnel-kit explain <profile>
```

The implementation should keep these layers separate internally:

* profile/config management
* systemd/service management
* MCP registration/export
* diagnostics/explanation

## Milestone 3: `explain <profile>`

Add a human-readable inspection command. **Complete.**

`openai-tunnel-kit explain <profile>` should show:

* profile name
* config paths
* tunnel command
* MCP servers associated with the profile
* systemd unit path
* systemd service name
* whether the service is installed
* whether the service is enabled
* whether the service is currently active
* relevant environment files
* exact command systemd will execute

Goal: future debugging without archaeology.

## Milestone 4: Strong `doctor`

Expand `doctor` into a full diagnostics command.

Checks:

* Python version
* `tunnel-client` available
* `systemctl --user` available
* user systemd session available
* profile directory exists
* profile config is valid
* service file exists
* service is enabled
* service is active
* MCP config is parseable
* expected commands resolve correctly
* optional desktop environment variables are present when requested

Output should include actionable fixes, not just failures.

## Milestone 5: Dry Run Mode

Add `--dry-run` to commands that modify files or services.

Dry run should show:

* files that would be created
* files that would be modified
* commands that would be executed
* systemd units that would be installed
* MCP snippets that would be generated

No filesystem or systemd changes should occur in dry-run mode.

## Milestone 6: Portable Executable

Provide a portable executable build path.

Goals:

* single-file or near-single-file artifact
* easy download/copy onto fresh machines
* no required virtualenv for normal use
* still support source/dev install for contributors

Document:

```bash
openai-tunnel-kit --version
openai-tunnel-kit doctor
```

## Milestone 7: Remote Bootstrap

Design remote bootstrap carefully. This is powerful and sharp.

Possible future target:

```bash
curl -fsSL <bootstrap-url> | sh
```

But the safer preferred form should be:

```bash
curl -fsSLO <bootstrap-script>
less <bootstrap-script>
sh <bootstrap-script>
```

Bootstrap should:

* download or install the executable
* verify expected checksum when possible
* create config directories
* optionally initialize a profile
* optionally install the user service
* never silently enable remote access without explicit confirmation

This milestone must include security notes before implementation.

## Milestone 8: Homelab Profiles

Support multiple machines and roles cleanly.

Example profiles:

```text
desktop
homelab
laptop
mobile
```

Each profile should be inspectable, exportable, and reproducible.

Possible commands:

```bash
openai-tunnel-kit profile export <profile>
openai-tunnel-kit profile import <file>
```

## Milestone 9: Control-Plane Lifecycle

Manage the API-side tunnel lifecycle without conflating runtime and admin
credentials. **Implemented on `dev-oauth`.**

* Inspect live tunnel metadata with the profile runtime key.
* Create and attach tunnels only with explicit organization/workspace IDs.
* Verify requested scopes by reading metadata back after mutation.
* Keep the admin key transient and out of profiles and systemd units.
* Allow `setup-env` to provision a tunnel before installing the runtime.
* Preserve both stdio and remote OAuth-capable MCP target configurations.

## Milestone 10: Optional Browser Wizard

Provide an easy path without making web dependencies part of the core CLI.
**Implemented on `dev-oauth`.**

* Loopback-only FastAPI/uvicorn setup UI.
* Random per-process API token plus strict browser security headers.
* Local stdio and remote OAuth-capable URL targets.
* Existing, attach, and provision tunnel workflows.
* Transient admin/runtime inputs with encrypted runtime persistence.
* Explicit destructive confirmation and profile replacement control.
* Sanitized result and ChatGPT GUI handoff.

## Non-Goals For Now

* Do not become a general process manager.
* Do not become a full MCP server framework.
* Do not hide OpenAI or tunnel-client registration steps that still require user action.
* Do not require networking dependencies in the core CLI.
* Do not auto-enable powerful remote access without explicit user confirmation.
* Do not store secrets casually in repo-tracked files.

## Design Principle

This toolkit should turn “I forgot how I set up my tunnel” into:

```bash
openai-tunnel-kit explain <profile>
openai-tunnel-kit doctor
```

The user should never have to rediscover the ritual from shell history.
