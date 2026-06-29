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

```console
python3 -m pip install .
```

For development, `scripts/bootstrap-venv.sh` creates `.venv` and installs the
project in editable mode.

## Quick start

```console
openai-tunnel-kit init my-profile
openai-tunnel-kit install-service my-profile
systemctl --user status tunnel-client@my-profile
```

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

If the client needs flags, declare each argument explicitly when creating the
profile. The MCP path is also exported to the client as `MCP_CONFIG`.

```console
openai-tunnel-kit init work \
  --binary /opt/tunnel/bin/tunnel-client \
  --arg=--config --arg=/home/me/.config/openai-tunnel-kit/profiles/work.mcp.json
```

Use `--force` to intentionally replace a profile. Existing MCP JSON is retained
unless `--mcp-file` is also supplied.

## Commands

```text
openai-tunnel-kit init <profile> [--binary PATH] [--arg ARG] [--mcp-file FILE]
openai-tunnel-kit install-service <profile> [--no-start]
openai-tunnel-kit status <profile>
openai-tunnel-kit print-mcp <profile>
openai-tunnel-kit set-mcp <profile> <file>
openai-tunnel-kit doctor [profile]
openai-tunnel-kit uninstall <profile> [--purge]
```

`install-service` writes
`~/.config/systemd/user/tunnel-client@.service`, reloads the user manager, and
enables and starts the requested instance. The unit uses `Restart=on-failure`
and is enabled under `default.target`.

`uninstall` disables and stops the instance but retains its reproducible config.
Add `--purge` to delete the profile files too.

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

Copy the profile `.env` and `.mcp.json` files into the toolkit's profile
directory on another machine, install the external binaries referenced by
them, then run:

```console
openai-tunnel-kit install-service my-profile
openai-tunnel-kit doctor my-profile
```

For isolated tests or managed environments, set
`OPENAI_TUNNEL_KIT_CONFIG_DIR` to override the profile directory. Install the
service with the same environment so the generated unit references that path.
