"""远程采集脚本与输出解析:硬件/系统配置、运行状态、网站与端口。"""
import re
import shlex
from datetime import datetime, timezone

# 所有脚本都通过 `sh -s` 从 stdin 传入,避免引号转义问题;同时兼容 Linux 与 macOS
_PRELUDE = r"""
export LC_ALL=C PATH="$PATH:/usr/sbin:/sbin:/usr/local/sbin"
k(){ printf '%s=%s\n' "$1" "$(printf '%s' "$2" | tr '\n' ' ' | sed 's/[[:space:]]*$//')"; }
S=""; if [ "$(id -u)" != 0 ] && sudo -n true 2>/dev/null; then S="sudo -n"; fi
"""

FACTS_SCRIPT = _PRELUDE + r"""
k hostname "$(hostname 2>/dev/null)"
if [ -r /etc/os-release ]; then k os "$(. /etc/os-release; echo "$PRETTY_NAME")"; else k os "$(sw_vers -productName 2>/dev/null) $(sw_vers -productVersion 2>/dev/null)"; fi
k kernel "$(uname -sr)"
k arch "$(uname -m)"
cpu="$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2-)"
[ -z "$cpu" ] && cpu="$(sysctl -n machdep.cpu.brand_string 2>/dev/null)"
k cpu_model "$cpu"
k cpu_cores "$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null)"
k cpu_sockets "$(grep 'physical id' /proc/cpuinfo 2>/dev/null | sort -u | wc -l | tr -d ' ')"
mt="$(awk '/MemTotal/{printf "%d", $2*1024}' /proc/meminfo 2>/dev/null)"
[ -z "$mt" ] && mt="$(sysctl -n hw.memsize 2>/dev/null)"
k mem_total "$mt"
k mem_available "$(awk '/MemAvailable/{printf "%d", $2*1024}' /proc/meminfo 2>/dev/null)"
k swap_total "$(awk '/SwapTotal/{printf "%d", $2*1024}' /proc/meminfo 2>/dev/null)"
k disks "$(df -kP 2>/dev/null | awk 'NR>1 && $1 ~ /^\/dev\// {printf "%s|%d|%d|%s;", $6, $2*1024, $3*1024, $5}')"
k vendor "$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null)"
prod="$(cat /sys/class/dmi/id/product_name 2>/dev/null)"
[ -z "$prod" ] && prod="$(sysctl -n hw.model 2>/dev/null)"
k product "$prod"
k serial "$(cat /sys/class/dmi/id/product_serial 2>/dev/null || $S cat /sys/class/dmi/id/product_serial 2>/dev/null)"
k board "$(cat /sys/class/dmi/id/board_name 2>/dev/null)"
k bios "$(cat /sys/class/dmi/id/bios_version 2>/dev/null)"
k virt "$(systemd-detect-virt 2>/dev/null)"
k uptime_seconds "$(awk '{printf "%d", $1}' /proc/uptime 2>/dev/null)"
k load "$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null || sysctl -n vm.loadavg 2>/dev/null | tr -d '{}')"
k ips "$(hostname -I 2>/dev/null || ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2}')"
k timezone "$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null)"
k docker "$(docker --version 2>/dev/null)"
k processes "$(ps -e 2>/dev/null | wc -l | tr -d ' ')"
BTP="${BT_PANEL:-/www/server/panel}"
if [ -d "$BTP" ]; then
  k bt_installed 1
  k bt_version "$({ cat "$BTP/class/common.py" 2>/dev/null || $S cat "$BTP/class/common.py" 2>/dev/null; } | grep -m1 -oE "g\.version *= *['\"][0-9.]+" | grep -oE "[0-9][0-9.]*$")"
  k bt_port "$(cat "$BTP/data/port.pl" 2>/dev/null || $S cat "$BTP/data/port.pl" 2>/dev/null)"
  k bt_path "$(cat "$BTP/data/admin_path.pl" 2>/dev/null || $S cat "$BTP/data/admin_path.pl" 2>/dev/null)"
  k bt_domain "$(cat "$BTP/data/domain.conf" 2>/dev/null || $S cat "$BTP/data/domain.conf" 2>/dev/null)"
  k bt_ssl "$( { [ -f "$BTP/data/ssl.pl" ] || $S test -f "$BTP/data/ssl.pl"; } 2>/dev/null && echo 1 || echo 0)"
  k bt_running "$(pgrep -f BT-Panel >/dev/null 2>&1 && echo 1 || echo 0)"
fi
# ---- 算力:NVIDIA / 昇腾 NPU / PCI 加速卡 ----
if command -v nvidia-smi >/dev/null 2>&1; then
  k gpu_vendor nvidia
  k gpu_driver "$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)"
  k gpu_cuda "$(nvidia-smi 2>/dev/null | grep -oE 'CUDA Version: *[0-9.]+' | grep -oE '[0-9.]+$')"
  k gpus "$(nvidia-smi --query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw,power.limit --format=csv,noheader,nounits 2>/dev/null | tr '\n' ';')"
elif command -v npu-smi >/dev/null 2>&1; then
  k gpu_vendor ascend
  k gpu_driver "$(cat /usr/local/Ascend/driver/version.info 2>/dev/null | grep -m1 -i version | cut -d= -f2)"
  k npu_raw "$(npu-smi info 2>/dev/null | head -120 | base64 | tr -d '\n')"
fi
k gpu_pci "$(lspci 2>/dev/null | grep -iE 'vga|3d controller|display controller|processing accelerators' | grep -viE 'aspeed|matrox|cirrus|virtio|qxl|vmware|bochs|red hat|hyper-v' | cut -d: -f3- | sed 's/^ *//' | tr '\n' ';')"
"""

STATUS_SCRIPT = r"""
export LC_ALL=C
echo "load=$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null || sysctl -n vm.loadavg 2>/dev/null | tr -d '{}' | sed 's/^ *//')"
echo "mem=$(awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{if(t)printf "%d",(t-a)*100/t}' /proc/meminfo 2>/dev/null)"
echo "disk=$(df -P / 2>/dev/null | awk 'NR==2{print $5}' | tr -d '%')"
echo "uptime=$(awk '{printf "%d", $1}' /proc/uptime 2>/dev/null)"
echo "cores=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null)"
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "gpus=$(nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>/dev/null | tr '\n' ';')"
elif command -v npu-smi >/dev/null 2>&1; then
  echo "npu_raw=$(npu-smi info 2>/dev/null | head -120 | base64 | tr -d '\n')"
fi
"""

GPU_LIVE_SCRIPT = r"""
export LC_ALL=C
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "@@VENDOR@@"; echo nvidia
  echo "@@GPUS@@"
  nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu,utilization.memory,temperature.gpu,power.draw,power.limit,fan.speed,clocks.sm --format=csv,noheader,nounits 2>&1
  echo "@@APPS@@"
  A="$(nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null)"
  echo "$A"
  echo "@@PS@@"
  P="$(echo "$A" | awk -F', ' 'NF>1{print $2}' | sort -u | tr '\n' ',' | sed 's/,$//')"
  [ -n "$P" ] && ps -o pid=,user=,etime=,args= -p "$P" 2>/dev/null | cut -c1-240
  echo "@@HEAD@@"
  nvidia-smi 2>/dev/null | head -4
elif command -v npu-smi >/dev/null 2>&1; then
  echo "@@VENDOR@@"; echo ascend
  echo "@@RAW@@"; npu-smi info 2>&1 | head -150
fi
echo "@@END@@"
"""

SITES_SCRIPT = _PRELUDE + r"""
export PATH="$PATH:/usr/local/nginx/sbin:/www/server/nginx/sbin:/opt/nginx/sbin"
echo "@@NGINX@@"
if command -v nginx >/dev/null 2>&1; then $S nginx -T 2>/dev/null || nginx -T 2>/dev/null; fi
echo "@@APACHE@@"
for c in apachectl apache2ctl httpd; do
  if command -v $c >/dev/null 2>&1; then $S $c -S 2>&1 | head -400; break; fi
done
echo "@@PORTS@@"
if command -v ss >/dev/null 2>&1; then $S ss -tlnpH 2>/dev/null || ss -tlnH 2>/dev/null
elif command -v netstat >/dev/null 2>&1 && netstat -tlnp >/dev/null 2>&1; then $S netstat -tlnp 2>/dev/null
else lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null; fi
echo "@@DOCKER@@"
if command -v docker >/dev/null 2>&1; then $S docker ps --format '{{.Names}}|{{.Image}}|{{.Ports}}|{{.Status}}' 2>/dev/null; fi
echo "@@END@@"
"""


def parse_kv(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_facts(text: str) -> dict:
    kv = parse_kv(text)
    facts = {k: kv.get(k, "") for k in (
        "hostname", "os", "kernel", "arch", "cpu_model", "vendor", "product", "serial", "board",
        "bios", "virt", "load", "timezone", "docker")}
    for k in ("bt_installed", "bt_version", "bt_port", "bt_path", "bt_domain", "bt_ssl", "bt_running"):
        if kv.get(k):
            facts[k] = kv[k]
    facts["cpu_model"] = facts["cpu_model"].strip()
    for k in ("cpu_cores", "cpu_sockets", "mem_total", "mem_available", "swap_total", "uptime_seconds", "processes"):
        facts[k] = _int(kv.get(k))
    facts["ips"] = [ip for ip in kv.get("ips", "").split() if ip]
    disks = []
    for item in kv.get("disks", "").split(";"):
        parts = item.split("|")
        if len(parts) == 4:
            disks.append({"mount": parts[0], "size": _int(parts[1]), "used": _int(parts[2]),
                          "pct": _int(parts[3].rstrip("%"))})
    facts["disks"] = disks
    facts.update(parse_gpu_facts(kv))
    vendor, product = (x if x.lower() not in ("none", "default string", "to be filled by o.e.m.") else "" for x in (facts["vendor"], facts["product"]))
    facts["model"] = product if vendor and product.lower().startswith(vendor.lower()) else " ".join(x for x in (vendor, product) if x)
    return facts


def parse_status(text: str) -> dict:
    kv = parse_kv(text)
    d = {"load": kv.get("load", ""), "mem_pct": _int(kv.get("mem")), "disk_pct": _int(kv.get("disk")),
         "uptime_s": _int(kv.get("uptime")), "cores": _int(kv.get("cores"))}
    gpus = []
    for item in kv.get("gpus", "").split(";"):
        p = [x.strip() for x in item.split(",")]
        if len(p) == 4:
            gpus.append({"util": _int(p[0]), "mem_used": _int(p[1]), "mem_total": _int(p[2]), "temp": _int(p[3])})
    if kv.get("npu_raw"):
        gpus = [{"util": n["util"], "mem_used": n["mem_used"], "mem_total": n["mem_total"], "temp": n["temp"]}
                for n in parse_npu(_b64(kv["npu_raw"]))]
    if gpus:
        d["gpus"] = gpus
    return d


# ---------------- 算力 (GPU / NPU) ----------------

def _b64(s):
    import base64
    try:
        return base64.b64decode(s).decode("utf-8", "replace")
    except Exception:
        return ""


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_npu(text: str) -> list:
    """解析 npu-smi info 表格(昇腾 910/310),两行一组:芯片名/功耗/温度 + AICore 利用率/HBM。"""
    npus, cur = [], None
    for line in text.splitlines():
        m = re.match(r"\|\s*(\d+)\s+(\S*(?:9[1-9]0|310)\S*)\s*\|\s*(\w+)\s*\|\s*([\d.NA]+)\s+(\d+)", line)
        if m:
            cur = {"index": int(m.group(1)), "name": f"Ascend {m.group(2)}", "health": m.group(3),
                   "power": _float(m.group(4)), "temp": _int(m.group(5)), "util": None, "mem_used": None, "mem_total": None}
            npus.append(cur)
            continue
        m = re.match(r"\|\s*\d+\s*\|\s*[0-9a-fA-F:.]+\s*\|\s*(\d+)\s+.*?(\d+)\s*/\s*(\d+)\s*\|\s*$", line)
        if m and cur and cur["util"] is None:
            cur.update(util=_int(m.group(1)), mem_used=_int(m.group(2)), mem_total=_int(m.group(3)))
    return npus


def parse_gpu_facts(kv: dict) -> dict:
    out = {}
    vendor = kv.get("gpu_vendor", "")
    gpus = []
    if vendor == "nvidia":
        for item in kv.get("gpus", "").split(";"):
            p = [x.strip() for x in item.split(",")]
            if len(p) >= 8:
                gpus.append({"index": _int(p[0]), "name": p[1], "mem_total": _int(p[2]), "mem_used": _int(p[3]),
                             "util": _int(p[4]), "temp": _int(p[5]), "power": _float(p[6]), "power_limit": _float(p[7])})
        out.update(gpu_driver=kv.get("gpu_driver", ""), gpu_cuda=kv.get("gpu_cuda", ""))
    elif vendor == "ascend":
        gpus = parse_npu(_b64(kv.get("npu_raw", "")))
        out["gpu_driver"] = kv.get("gpu_driver", "")
    pci = [x.strip() for x in kv.get("gpu_pci", "").split(";") if x.strip()]
    if vendor:
        out["gpu_vendor"] = vendor
    if gpus:
        out["gpus"] = gpus
        names = {}
        for g in gpus:
            names[g["name"]] = names.get(g["name"], 0) + 1
        out["gpu_summary"] = " + ".join(f"{n}× {k}" for k, n in names.items())
        out["gpu_mem_total"] = sum(g["mem_total"] or 0 for g in gpus)
    elif pci:
        out["gpu_pci"] = pci
        out["gpu_summary"] = f"{len(pci)}× {pci[0]}(未安装驱动)"
    return out


def parse_gpu_live(text: str) -> dict:
    sec = _sections(text)
    vendor = sec.get("vendor", "").strip()
    if vendor == "ascend":
        raw = sec.get("raw", "")
        return {"vendor": "ascend", "gpus": parse_npu(raw), "processes": [], "raw": raw}
    if vendor != "nvidia":
        return {"vendor": "", "gpus": [], "processes": []}
    gpus, uuid_idx = [], {}
    for line in sec.get("gpus", "").splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 12:
            continue
        uuid_idx[p[1]] = _int(p[0])
        gpus.append({"index": _int(p[0]), "uuid": p[1], "name": p[2], "mem_total": _int(p[3]), "mem_used": _int(p[4]),
                     "util": _int(p[5]), "mem_util": _int(p[6]), "temp": _int(p[7]), "power": _float(p[8]),
                     "power_limit": _float(p[9]), "fan": _int(p[10]), "clock_sm": _int(p[11])})
    ps = {}
    for line in sec.get("ps", "").splitlines():
        p = line.split(None, 3)
        if len(p) >= 4:
            ps[p[0]] = {"user": p[1], "elapsed": p[2], "cmd": p[3]}
    procs = []
    for line in sec.get("apps", "").splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 4 and p[1].isdigit():
            info = ps.get(p[1], {})
            procs.append({"gpu": uuid_idx.get(p[0]), "pid": int(p[1]), "name": p[2], "mem": _int(p[3]),
                          "user": info.get("user", ""), "elapsed": info.get("elapsed", ""), "cmd": info.get("cmd", p[2])})
    head = sec.get("head", "")
    m = re.search(r"CUDA Version:\s*([0-9.]+)", head)
    d = re.search(r"Driver Version:\s*([0-9.]+)", head)
    return {"vendor": "nvidia", "gpus": gpus, "processes": procs,
            "cuda": m.group(1) if m else "", "driver": d.group(1) if d else ""}


# ---------------- 网站 / 端口 ----------------

def _sections(text: str) -> dict:
    out, cur = {}, None
    for line in text.splitlines():
        m = re.fullmatch(r"@@(\w+)@@", line.strip())
        if m:
            cur = m.group(1).lower()
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def _nginx_tokens(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c in "{};":
            yield ("S", c)
            i += 1
        elif c in "\"'":
            j, buf = i + 1, []
            while j < n and text[j] != c:
                if text[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(text[j])
                j += 1
            yield ("W", "".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in "{};":
                j += 1
            yield ("W", text[i:j])
            i = j


def _nginx_parse(tokens, pos=0):
    items, cur = [], []
    while pos < len(tokens):
        kind, val = tokens[pos]
        if kind == "S" and val == ";":
            if cur:
                items.append((cur[0], cur[1:], None))
            cur = []
            pos += 1
        elif kind == "S" and val == "{":
            children, pos = _nginx_parse(tokens, pos + 1)
            items.append((cur[0] if cur else "", cur[1:], children))
            cur = []
        elif kind == "S" and val == "}":
            return items, pos + 1
        else:
            cur.append(val)
            pos += 1
    return items, pos


def _nginx_files(text: str):
    """nginx -T 输出按 "# configuration file X:" 拆成各个文件。"""
    files, cur_name, buf = [], None, []
    for line in text.splitlines():
        m = re.match(r"# configuration file (.+):$", line.strip())
        if m:
            if cur_name:
                files.append((cur_name, "\n".join(buf)))
            cur_name, buf = m.group(1), []
        elif cur_name:
            buf.append(line)
    if cur_name:
        files.append((cur_name, "\n".join(buf)))
    return files


def parse_nginx(text: str) -> list:
    sites, upstreams = [], {}

    def directive(children, name):
        return [args for (n, args, ch) in children if n == name and ch is None]

    def walk(items, file, ctx):
        for name, args, children in items:
            if children is None:
                continue
            if name == "upstream" and args:
                upstreams[args[0]] = [a[0] for a in directive(children, "server") if a]
            if name == "server" and ctx != "upstream":
                listens = [" ".join(a) for a in directive(children, "listen")]
                names = [n for a in directive(children, "server_name") for n in a]
                roots = directive(children, "root")
                certs = directive(children, "ssl_certificate")
                locations = []
                for n2, a2, ch2 in children:
                    if n2 == "location" and ch2 is not None:
                        loc = {"path": " ".join(a2)}
                        for key in ("proxy_pass", "root", "alias", "return", "fastcgi_pass"):
                            v = directive(ch2, key)
                            if v:
                                loc[key] = " ".join(v[0])
                        locations.append(loc)
                ret = directive(children, "return")
                sites.append({
                    "type": "stream" if ctx == "stream" else "http",
                    "file": file,
                    "domains": names,
                    "listen": listens,
                    "root": roots[0][0] if roots and roots[0] else "",
                    "ssl": bool(certs) or any("ssl" in l or "443" in l for l in listens),
                    "cert": certs[0][0] if certs and certs[0] else "",
                    "proxy": sorted({l["proxy_pass"] for l in locations if "proxy_pass" in l}
                                    | {" ".join(a) for a in directive(children, "proxy_pass")}),
                    "redirect": " ".join(ret[0]) if ret else "",
                    "locations": locations[:30],
                })
            walk(children, file, name if name in ("stream", "upstream", "mail") else ctx)

    for fname, content in _nginx_files(text):
        try:
            tree, _ = _nginx_parse(list(_nginx_tokens(content)))
            walk(tree, fname, "")
        except Exception:
            continue
    for s in sites:
        s["upstreams"] = {p.split("://", 1)[-1].split("/")[0]: upstreams[p.split("://", 1)[-1].split("/")[0]]
                          for p in s["proxy"] if p.split("://", 1)[-1].split("/")[0] in upstreams}
    return sites


def parse_apache(text: str) -> list:
    sites, cur = [], None
    for line in text.splitlines():
        m = re.search(r"port (\d+) namevhost (\S+) \((.+)\)", line)
        m2 = re.match(r"^\s*([\d.*\[\]:a-fA-F]+):(\d+)\s+(\S+)\s+\((.+)\)", line)
        if m:
            cur = {"type": "apache", "domains": [m.group(2)], "listen": [m.group(1)], "file": m.group(3)}
            sites.append(cur)
        elif m2 and "namevhost" not in line:
            cur = {"type": "apache", "domains": [m2.group(3)], "listen": [m2.group(2)], "file": m2.group(4)}
            sites.append(cur)
        elif cur and line.strip().startswith("alias "):
            cur["domains"].append(line.strip().split(None, 1)[1])
    return sites


def parse_ports(text: str) -> list:
    res, seen = [], set()
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        addr, proc = None, ""
        m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
        if parts[0] == "LISTEN" and len(parts) >= 4:          # ss
            addr = parts[3]
            if m:
                proc = f"{m.group(1)} (pid {m.group(2)})"
        elif parts[0].startswith("tcp") and len(parts) >= 6:  # netstat
            addr = parts[3]
            if "/" in parts[-1]:
                pid, _, name = parts[-1].partition("/")
                proc = f"{name} (pid {pid})"
        elif "(LISTEN)" in line and len(parts) >= 9:          # lsof
            addr = parts[-2]
            proc = f"{parts[0]} (pid {parts[1]})"
        if not addr:
            continue
        host, _, port = addr.rpartition(":")
        if not port.isdigit():
            continue
        key = (host, port, proc)
        if key in seen:
            continue
        seen.add(key)
        res.append({"addr": host or "*", "port": int(port), "process": proc})
    res.sort(key=lambda x: (x["port"], x["addr"]))
    return res


def parse_docker(text: str) -> list:
    out = []
    for line in text.splitlines():
        p = line.split("|")
        if len(p) == 4:
            out.append({"name": p[0], "image": p[1], "ports": p[2], "status": p[3]})
    return out


def parse_sites(text: str) -> dict:
    sec = _sections(text)
    return {
        "nginx": parse_nginx(sec.get("nginx", "")),
        "apache": parse_apache(sec.get("apache", "")),
        "ports": parse_ports(sec.get("ports", "")),
        "docker": parse_docker(sec.get("docker", "")),
        "nginx_found": bool(sec.get("nginx", "").strip()),
    }


def cert_script(paths) -> str:
    lines = [_PRELUDE]
    for p in paths:
        q = shlex.quote(p)
        lines.append(f"printf '%s|' {q}; (openssl x509 -enddate -noout -in {q} 2>/dev/null || "
                     f"$S openssl x509 -enddate -noout -in {q} 2>/dev/null) | cut -d= -f2; echo")
    return "\n".join(lines)


def parse_cert_dates(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "|" not in line:
            continue
        path, _, date = line.partition("|")
        date = " ".join(date.split())
        if not date:
            continue
        try:
            dt = datetime.strptime(date, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            out[path] = dt.isoformat(timespec="seconds")
        except ValueError:
            continue
    return out
