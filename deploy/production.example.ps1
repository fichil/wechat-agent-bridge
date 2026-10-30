# Copy to a local-only file and use your actual DNS hostname.
$env:BRIDGE_PUBLIC_ORIGIN = 'https://bridge.example.com'
$env:BRIDGE_CADDY = 'C:\Tools\Caddy\caddy.exe'
# The administrator password is entered using python -m backend.admin init.
# No passwords, API keys or session cookies belong in this file.
