# openai-tunnel-kit

`openai-tunnel-kit` is a small, dependency-free Python CLI for keeping an
external `tunnel-client` running as a persistent systemd user service. Profiles
are ordinary environment and JSON files: there is no database and no hidden
state.

## Requirements

- Linux with a systemd user manager
- Python 3.9 or newer
- A separately installed `tunnel-client` executable

The toolkit does not make OpenAI API calls and does not install the tunnel
client or MCP servers.

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

If `python3 -m venv` is unavailable, install your distribution's venv package
first (commonly `python3-venv` on Debian-family systems). On Python
installations that are not externally managed, a normal virtual environment or
`python3 -m pip install .` also works.

### Portable executables

The `Portable executables` GitHub Actions workflow builds self-contained Linux
archives for `x86_64` and `arm64`. Download the artifact for your architecture,
verify it with the adjacent `.sha256` file, and extract it:

```console
sha256sum --check openai-tunnel-kit-linux-x86_64.tar.gz.sha256
tar -xzf openai-tunnel-kit-linux-x86_64.tar.gz
./openai-tunnel-kit --help
```

These archives include Python and this toolkit, but intentionally do not bundle
the external `tunnel-client`, systemd, or an MCP server's runtime. They are
built on Ubuntu 22.04 and target compatible glibc-based Linux distributions of
the matching architecture.

## Quick start

```console
openai-tunnel-kit init my-profile \
  --tunnel-id tunnel_... \
  --api-key-file ~/.config/openai/runtime-api-key \
  --mcp-file /path/to/mcp.json
openai-tunnel-kit install-service my-profile
systemctl --user status tunnel-client@my-profile
```

The two OpenAI values have first-class options:

- `--tunnel-id` sets `CONTROL_PLANE_TUNNEL_ID`. Create or find the ID in
  **OpenAI Platform → Organization settings → Tunnels**.
- `--api-key-file` reads the runtime key into `CONTROL_PLANE_API_KEY`. Create a
  runtime API key in **OpenAI Platform → Organization settings → API keys**.
  Use a runtime key, not an admin key.

The key file should contain only the key. `--api-key KEY` is also supported,
but it can expose the key in shell history and process listings. If either
value is omitted, `init` creates a clearly labeled blank field in the profile
and prints the exact action still required.

`init` creates:

```text
~/.config/openai-tunnel-kit/profiles/my-profile.env
~/.config/openai-tunnel-kit/profiles/my-profile.mcp.json
```

Edit the generated MCP example or replace it with a real JSON blob:

```console
openai-tunnel-kit set-mcp my-profile ./mcp.json
openai-tunnel-kit print-mcp my-profile
```

`set-mcp` is operational, not merely archival: it validates one standard
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
openai-tunnel-kit init work \
  --binary /opt/tunnel/bin/tunnel-client \
  --arg=--health.listen-addr --arg=127.0.0.1:9090
```

Use `--force` to intentionally replace a profile. Existing MCP JSON is retained
unless `--mcp-file` is also supplied.

## Commands

```text
openai-tunnel-kit init <profile> [--tunnel-id ID] [--api-key-file FILE | --api-key KEY]
                               [--binary PATH] [--arg ARG] [--mcp-file FILE]
                               [--pass-desktop-environment]
openai-tunnel-kit install-service <profile> [--no-start]
openai-tunnel-kit status <profile>
openai-tunnel-kit print-mcp <profile>
openai-tunnel-kit set-mcp <profile> <file>
openai-tunnel-kit list-profiles
openai-tunnel-kit remove-profile <profile>
openai-tunnel-kit doctor [profile]
openai-tunnel-kit uninstall <profile> [--purge]
```

`install-service` writes
`~/.config/systemd/user/tunnel-client@.service`, reloads the user manager, and
enables and starts the requested instance. The unit uses `Restart=on-failure`
and is enabled under `default.target`.

`uninstall` disables and stops the instance but retains its reproducible config.
Add `--purge` to delete the profile files too.

`list-profiles` prints configured profile names in deterministic order.
`remove-profile` disables and stops the profile's service before deleting its
`.env` and MCP JSON files. If systemd removal fails, the profile files are
retained so the service cannot be left pointing at missing configuration.

For MCP servers that interact with the logged-in desktop, add
`--pass-desktop-environment` when creating the profile. `install-service` then
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
openai-tunnel-kit install-service my-profile
openai-tunnel-kit doctor my-profile
```

For isolated tests or managed environments, set
`OPENAI_TUNNEL_KIT_CONFIG_DIR` to override the profile directory. Install the
service with the same environment so the generated unit references that path.
