## Environment
You are running inside a **Kali Linux Docker container** with full root access.

### System Details
- **OS**: Kali Linux (Debian-based) — use `apt install` for packages
- **User**: root (full privileges)
- **Base dir**: AGIX framework is a Python project in `/agix/`
- **Network**: Running inside Docker — use container networking for scans
  - Target services may be on the Docker bridge network (`172.17.0.0/16`)
  - Use `host.docker.internal` to reach the host machine
  - For external targets, verify DNS resolution first

### Pre-installed Security Tools
| Category | Tools |
|----------|-------|
| **Scanning** | `nmap`, `masscan`, `unicornscan` |
| **Web** | `nikto`, `sqlmap`, `wpscan`, `whatweb`, `wafw00f` |
| **Brute-force** | `hydra`, `john`, `hashcat`, `medusa` |
| **Directory** | `gobuster`, `dirb`, `dirbuster`, `ffuf` |
| **Exploitation** | `metasploit-framework`, `searchsploit` |
| **Recon** | `whois`, `dig`, `dnsrecon`, `sublist3r`, `amass` |
| **Proxy** | `burpsuite`, `mitmproxy`, `zaproxy` |
| **Crypto** | `openssl`, `sslscan`, `testssl.sh` |
| **Network** | `tcpdump`, `wireshark-cli`, `netcat`, `socat` |

### Wordlists
Wordlists must be downloaded before use:
```bash
# Common wordlists
apt install -y seclists wordlists
# Default paths after install:
# /usr/share/seclists/
# /usr/share/wordlists/
```

### Docker Considerations
- Port scans of `localhost` will scan the **container**, not the host
- Use `host.docker.internal` or the Docker bridge IP to reach the host
- Some tools (e.g., masscan) need `--rate` limiting in containers
- File system is ephemeral — save important findings with `save_deliverable`