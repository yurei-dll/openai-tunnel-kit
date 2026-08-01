# openai-tunnel-kit

`openai-tunnel-kit` is a small, dependency-free Python CLI for keeping an
external `tunnel-client` running as a persistent systemd user service. Profiles
are ordinary environment and JSON files: there is no database and no hidden
state.

## Requirements

- Linux with a systemd user manager
- Python 3.9 or newer
- A separately installed `tunnel-client` executable

The core profile and service commands do not make OpenAI API calls. The optional
browser wizard uses the OpenAI Administration API when you ask it to load
projects or provision credentials and tunnels. The toolkit does not install the
tunnel client or MCP servers.

## Install

On Debian, Ubuntu, and other distributions that mark the system Python as
**externally managed** (PEP 668), use the included venv helper instead of
`sudo pip`, `--break-system-packages`, or modifying the OS Python:

```console
./scripts/bootstrap-venv.sh
./.venv/bin/openai-tunnel-kit --help
```

The helper creates `.venv` and installs this checkout in editable mode. Run
commands directly through `./.venv/bin/openai-tunnel-kit`, or activate the
environment so the shorter commands used below work:

```console
source .venv/bin/activate
openai-tunnel-kit --help
```

Install the optional browser wizard dependencies when you want the guided path:

```console
./.venv/bin/python -m pip install -e '.[wizard]'
./.venv/bin/openai-tunnel-kit wizard
```

The wizard listens only on `127.0.0.1`, chooses an unused port by default, and
opens a local browser. Use `--no-browser` on headless machines. Submitted API
keys remain in process memory and mode-`0600` temporary files. The runtime key
is retained per profile, encrypted through `systemd-creds`. The wizard can
optionally retain one global admin key in the current OS user's system wallet
(GNOME Keyring/Secret Service or KWallet); it is never copied into a kit
profile, generated config, or systemd service. Without that option, the admin
key remains transient and is discarded after control-plane provisioning.

Each wizard-created tunnel runtime also binds its health/admin server to an
OS-assigned loopback port (`127.0.0.1:0`) so multiple profiles can run at once.
The resolved base URL is written to the per-profile file
`~/.config/openai-tunnel-kit/health/<profile>.url`. Explicit health arguments
already present on an existing profile are preserved.

If `python3 -m venv` is unavailable, install your distribution's venv package
first (commonly `python3-venv` on Debian-family systems). On Python
installations that are not externally managed, a normal virtual environment or
`python3 -m pip install .` also works.

### Portable executables

The `Portable executables` GitHub Actions workflow builds self-contained Linux
archives for `x86_64` and `arm64`. Download the artifact for your architecture,
verify it with the adjacent `.sha256` file, and extract it:

**[Download the latest portable builds →](https://github.com/yurei-dll/openai-tunnel-kit/actions/workflows/portable-executables.yml)**

Open the newest successful run and choose the `x86_64` or `arm64` artifact near
the bottom of its summary page.

```console
sha256sum --check openai-tunnel-kit-linux-x86_64.tar.gz.sha256
tar -xzf openai-tunnel-kit-linux-x86_64.tar.gz
./openai-tunnel-kit --help
```

These archives include Python, this toolkit, and the browser wizard's HTTP and
system-wallet dependencies. The wizard can therefore be launched directly with
`./openai-tunnel-kit wizard`; no separate Python installation or `pip` step is
needed. The archives intentionally do not bundle the external `tunnel-client`,
systemd, or an MCP server's runtime. They are built on Ubuntu 22.04 and target
compatible glibc-based Linux distributions of the matching architecture.

## Quick start

```console
openai-tunnel-kit profile init my-profile \
  --tunnel-id tunnel_... \
  --api-key-file ~/.config/openai/runtime-api-key \
  --mcp-file /path/to/mcp.json
openai-tunnel-kit service install my-profile
openai-tunnel-kit doctor my-profile
```

### One-shot setup from an environment file

For a source-and-run installation, copy
[`examples/setup.env.example`](examples/setup.env.example), set its paths, and
run:

```console
source /path/to/setup.env
openai-tunnel-kit setup-env
```

`setup-env` validates and copies the MCP configuration, creates the profile,
encrypts the runtime API key with `systemd-creds --user`, installs the systemd
user unit, and enables and starts it. The toolkit's service launcher decrypts
the host-bound credential with `systemd-creds --user` immediately before
executing `tunnel-client`; the key is not written to the generated profile
`.env`.

The recommended `CONTROL_PLANE_API_KEY_FILE` variable points to a file
containing only the key. It keeps the secret out of the sourceable setup file
and shell history; the source file still contains every value needed to locate
and install the setup. `CONTROL_PLANE_API_KEY` is accepted as an alternative
for secrets supplied transiently by a password manager or provisioning system.
Do not commit either the key file or a setup file containing a literal key.

`setup-env` can provision the tunnel before installing the service. Omit
`CONTROL_PLANE_TUNNEL_ID` and provide explicit scope plus a transient admin-key
file:

```console
export OPENAI_TUNNEL_ADMIN_KEY_FILE="$HOME/.config/openai/admin-api-key"
export OPENAI_TUNNEL_NAME="PAT Tunnel Desktop"
export OPENAI_TUNNEL_DESCRIPTION="Personal Access Tool tunnel for desktop"
export OPENAI_TUNNEL_ORGANIZATION_IDS="org_..."
export OPENAI_TUNNEL_WORKSPACE_IDS="ws_..."
```

Comma-separate multiple IDs. The admin key is used only for tunnel creation and
is not copied into generated files. The runtime key is encrypted as before.

This flow requires a systemd release with user-scoped encrypted credentials
(`systemd-creds encrypt --user`). Encryption is tied to the local user/host, so
rerun `setup-env --force` on a new machine rather than copying the encrypted
credential. Environment values embedded in the MCP JSON are still stored in
the mode-`0600` profile; keep sensitive MCP-server values in that server's own
secret store when possible.

The two OpenAI values have first-class options:

- `--tunnel-id` sets `CONTROL_PLANE_TUNNEL_ID`. Create or find the ID in
  **OpenAI Platform → Organization settings → Tunnels**.
- `--api-key-file` reads the runtime key into `CONTROL_PLANE_API_KEY`. Create a
  runtime API key in **OpenAI Platform → Organization settings → API keys**.
  Use a runtime key, not an admin key.

The key file should contain only the key. `--api-key KEY` is also supported,
but it can expose the key in shell history and process listings. If either
value is omitted, `profile init` creates a clearly labeled blank field in the profile
and prints the exact action still required.

`profile init` creates:

```text
~/.config/openai-tunnel-kit/profiles/my-profile.env
~/.config/openai-tunnel-kit/profiles/my-profile.mcp.json
```

Edit the generated MCP example or replace it with a real JSON blob:

```console
openai-tunnel-kit mcp add my-profile ./mcp.json
openai-tunnel-kit mcp print my-profile
```

`mcp add` is operational, not merely archival: it validates one standard
`mcpServers` entry and configures `tunnel-client run` to launch its `command`
and `args` on the `main` channel. It also supports a single `url` transport and
copies the server's `env` strings into the protected profile environment.

For example:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["/absolute/path/to/repo/dist/index.js"],
      "env": {}
    }
  }
}
```

If the client needs additional `run` flags, declare each argument explicitly
when creating the profile.

```console
openai-tunnel-kit profile init work \
  --binary /opt/tunnel/bin/tunnel-client \
  --arg=--health.listen-addr --arg=127.0.0.1:9090
```

Use `--force` to intentionally replace a profile. Existing MCP JSON is retained
unless `--mcp-file` is also supplied.

## Commands

```text
openai-tunnel-kit profile init <profile> [options]
openai-tunnel-kit profile list
openai-tunnel-kit profile show <profile>
openai-tunnel-kit profile remove <profile>
openai-tunnel-kit service install <profile> [--no-start] [--force]
openai-tunnel-kit service start|stop|status <profile>
openai-tunnel-kit service uninstall <profile> [--purge]
openai-tunnel-kit mcp add <profile> <file>
openai-tunnel-kit mcp list|print <profile>
openai-tunnel-kit tunnel inspect <profile>
openai-tunnel-kit tunnel attach <profile> [scope options]
openai-tunnel-kit tunnel provision <profile> [scope options]
openai-tunnel-kit wizard [--port PORT] [--no-browser]
openai-tunnel-kit setup-env [--force]
openai-tunnel-kit doctor [profile]
openai-tunnel-kit explain <profile>
```

### Tunnel control-plane lifecycle

The toolkit can inspect, create, and attach OpenAI control-plane tunnels without
storing an admin key in the profile or systemd service. Runtime polling still
uses the narrower runtime API key.

```console
openai-tunnel-kit tunnel inspect personal-access-tool

openai-tunnel-kit tunnel attach personal-access-tool \
  --admin-key-file ~/.config/openai/admin-api-key \
  --organization-id org_... \
  --workspace-id ws_...

openai-tunnel-kit tunnel provision personal-access-tool \
  --admin-key-file ~/.config/openai/admin-api-key \
  --name "PAT Tunnel Desktop" \
  --description "Personal Access Tool tunnel for desktop" \
  --organization-id org_... \
  --workspace-id ws_...
```

IDs are never guessed. Mutating commands read the tunnel metadata back and fail
if the requested scope was not retained. `inspect` can use the runtime key;
`attach` and `provision` require a real admin key supplied through a file.

Remote OAuth-capable MCP servers use the existing standard `url` entry. The
external `tunnel-client` handles OAuth and protected-resource discovery while
this toolkit manages the same profile and service lifecycle:

```json
{
  "mcpServers": {
    "private-http-server": {
      "url": "https://mcp.example.com/mcp"
    }
  }
}
```

### Browser wizard

`openai-tunnel-kit wizard` guides the complete supported flow:

1. Upload a standard `mcp.json` (or enter a target manually).
2. Use the global admin key saved in the OS user's system wallet, or enter an
   organization Admin API key and optionally save it there, then load active
   projects.
3. Select a project and create a dedicated service-account runtime key.
4. Reuse, attach, or provision a control-plane tunnel.
5. Enter explicit organization/workspace scopes when the backend exposes them.
6. Encrypt the generated runtime key and discard the transient admin key.
7. Install and start the systemd user service.
8. Read live tunnel metadata back and provide the exact ChatGPT handoff.

New service accounts default to `openai-tunnel-kit-<profile>` so their owning
kit profile is visible in OpenAI's project administration UI. The wizard also
accepts an explicit service-account name when a different lifecycle is
intentional.

Replacing an existing profile reuses its encrypted runtime credential by
default instead of creating a duplicate service account. **Rotate automatic
runtime credential** explicitly creates a replacement key; after the new setup
passes verification, revoke the previous service account in OpenAI's project
administration UI. New profiles still create one dedicated service account.

The tunnel name defaults to the profile name. After a successful setup, the
wizard remembers the first organization ID in the user-global plaintext file
`~/.config/openai-tunnel-kit/wizard-preferences.json` and pre-fills it on the
next wizard run. Organization IDs are identifiers rather than credentials; the
file is nevertheless written with mode `0600` to avoid unnecessary disclosure.

The saved admin credential is global to the logged-in OS user and reusable by
every wizard run; it is deliberately not associated with any tunnel-kit
profile or selected project. The browser never receives a saved key. Project
loading and setup ask the local Python backend to read it directly from the
wallet, and **Forget saved key** removes the global wallet entry. An entered
replacement is saved only after OpenAI accepts it during project loading or a
pre-setup project validation. Desktop wallet policy controls whether retrieval
prompts for the user's password or reuses the wallet unlocked at login.

The automatic clean-slate path therefore needs an `mcp.json`, an Admin API key,
and an explicit organization or workspace ID for tunnel scope. OpenAI's Admin
API returns selectable projects but does not include the parent organization ID
in project objects, so the wizard refuses to guess that boundary. The wizard
uses the documented project service-account endpoint and retains only the
returned one-time runtime key as an encrypted systemd credential:

<https://platform.openai.com/docs/api-reference/project-service-accounts>

The wizard deliberately reports an absent `workspace_ids` attachment instead
of inventing an ID. ChatGPT app creation itself remains a final GUI handoff
because OpenAI does not expose a stable public app-creation API. For the Server
URL workaround, the wizard accepts an OAuth-capable HTTPS MCP endpoint and lets
`tunnel-client` perform its standard OAuth/protected-resource discovery.

The top-level help points out the usual happy path:
`profile init` → `service install` → `doctor`. Use `explain <profile>` when you
want the non-mystical version of a setup: it reports the unit's installed,
enabled, and running state; the exact systemd `ExecStart`; the expanded tunnel
command; attached MCP server targets; and all relevant environment values.
Secret-like values are reported as set or unset but are never printed.

`service install` writes
`~/.config/systemd/user/tunnel-client@.service`, reloads the user manager, and
enables and starts the requested instance. The unit uses `Restart=on-failure`
and is enabled under `default.target`.

Before changing systemd state, `service install` requires one usable API-key
source: either a non-empty profile value or an encrypted credential that can be
decrypted on the current host. Missing, conflicting, and undecryptable
credentials stop installation with an actionable error. `--force` overrides
that guardrail and prints a warning; it is intended for deliberate diagnostics,
not normal setup.

`service uninstall` disables and stops the instance but retains its reproducible config.
Add `--purge` to delete the profile files too.

`profile list` prints configured profile names in deterministic order.
`profile remove` disables and stops the profile's service before deleting its
`.env` and MCP JSON files. If systemd removal fails, the profile files are
retained so the service cannot be left pointing at missing configuration.

For MCP servers that interact with the logged-in desktop, add
`--pass-desktop-environment` when creating the profile. `service install` then
creates an instance-specific systemd drop-in that passes current Wayland/X11,
D-Bus, XDG runtime, and desktop-session variables to tunnel-client and its MCP
child. The values come from the systemd user manager on each launch; they are
not frozen into the profile or copied between machines.

## Debugging and reboot behavior

Run `openai-tunnel-kit doctor` for clear checks of the systemd user manager,
unit template, configured binary, and MCP JSON. For a single profile:

```console
openai-tunnel-kit doctor my-profile
journalctl --user -u tunnel-client@my-profile.service
```

Service health is based on systemd's exact active and sub-state. In particular,
an `activating (auto-restart)` service whose process keeps exiting is reported
as failed, not running; the diagnostic includes its result, exit status, and
restart count.

Enabled user services start whenever the user's systemd manager starts. Most
desktop/server login sessions do this at login. To start user services at boot
before login, an administrator may enable lingering:

```console
loginctl enable-linger "$USER"
```

That is deliberately not done by this tool because it is a host-level policy
choice.

## Reproducing a setup

Securely copy the profile `.env` and `.mcp.json` files into the toolkit's
profile directory on another machine, install the external binaries referenced
by them, then run. The `.env` file contains the runtime API key and must remain
private (the toolkit writes it with mode `0600`).

```console
openai-tunnel-kit service install my-profile
openai-tunnel-kit doctor my-profile
```

Profiles created by `setup-env` use a host-bound encrypted credential and
should instead be reproduced from the original setup environment and key
source.

For isolated tests or managed environments, set
`OPENAI_TUNNEL_KIT_CONFIG_DIR` to override the profile directory. Install the
service with the same environment so the generated unit references that path.
