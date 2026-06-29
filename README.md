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
