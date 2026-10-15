from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .config import DATA_DIR
from .inventory import load_inventory

CONNECTIONS_FILE = DATA_DIR / "connections.csv"
COLUMNS = [
    "connection_id",
    "source_rack_id",
    "source_device_id",
    "source_port",
    "destination_rack_id",
    "destination_device_id",
    "destination_port",
    "connection_type",
    "status",
    "notes",
]

TYPE_COLORS = {
    "fiber": "#22c55e",
    "copper": "#f97316",
    "network": "#f59e0b",
    "power": "#3b82f6",
    "other": "#94a3b8",
}


def ensure_connections_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONNECTIONS_FILE.exists():
        pd.DataFrame(columns=COLUMNS).to_csv(CONNECTIONS_FILE, index=False)


def load_connections() -> pd.DataFrame:
    ensure_connections_file()
    try:
        from . import db_backend
        if db_backend.is_db_mode():
            df = db_backend.read_table("connections")
            if df is None or df.empty:
                return pd.DataFrame(columns=COLUMNS)
        else:
            df = None
    except Exception:
        df = None
    if df is None:
        try:
            df = pd.read_csv(CONNECTIONS_FILE, encoding="utf-8-sig")
        except Exception:
            try:
                df = pd.read_csv(CONNECTIONS_FILE)
            except Exception:
                return pd.DataFrame(columns=COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    # trim column names
    df.columns = [str(c).strip() for c in df.columns]

    # نرمال‌سازی نام ستون‌های احتمالی
    rename = {}
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    aliases = {
        "connection_id": ["connection_id", "id", "conn_id"],
        "source_rack_id": ["source_rack_id", "src_rack_id", "source_rack"],
        "source_device_id": ["source_device_id", "src_device_id", "source_device", "from_device"],
        "source_port": ["source_port", "src_port", "from_port"],
        "destination_rack_id": ["destination_rack_id", "dst_rack_id", "dest_rack_id", "destination_rack"],
        "destination_device_id": ["destination_device_id", "dst_device_id", "dest_device_id", "to_device"],
        "destination_port": ["destination_port", "dst_port", "dest_port", "to_port"],
        "connection_type": ["connection_type", "type", "link_type"],
        "status": ["status", "state"],
        "notes": ["notes", "note", "description"],
    }
    for std, names in aliases.items():
        if std in df.columns:
            continue
        for n in names:
            if n in lower_map:
                rename[lower_map[n]] = std
                break
    if rename:
        df = df.rename(columns=rename)

    for col in [
        "connection_id",
        "source_rack_id",
        "source_device_id",
        "destination_rack_id",
        "destination_device_id",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["source_port", "destination_port", "connection_type", "status", "notes"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)
        else:
            df[col] = ""
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = "" if col in ("source_port", "destination_port", "connection_type", "status", "notes") else pd.NA
    return df


def save_connections(df: pd.DataFrame) -> None:
    ensure_connections_file()
    out = df.copy()
    for c in COLUMNS:
        if c not in out.columns:
            out[c] = ""
    out = out[COLUMNS]
    try:
        from . import db_backend
        if db_backend.is_db_mode():
            db_backend.write_table("connections", out)
            return
    except Exception:
        pass
    out.to_csv(CONNECTIONS_FILE, index=False)


def next_connection_id(df: pd.DataFrame) -> int:
    if df.empty:
        return 1
    return int(pd.to_numeric(df["connection_id"], errors="coerce").max()) + 1


def port_capacity(device_row: dict, connection_type: str) -> int:
    ctype = str(connection_type).lower()
    if ctype in ("network", "copper"):
        return int(device_row.get("network_ports") or 0)
    if ctype == "power":
        return int(device_row.get("power_ports") or 0)
    if ctype == "fiber":
        return int(device_row.get("fiber_ports") or 0)
    return 0


def create_connection(
    *,
    source_rack_id: int,
    source_device_id: int,
    source_port: str,
    destination_rack_id: int,
    destination_device_id: int,
    destination_port: str,
    connection_type: str = "network",
    status: str = "active",
    notes: str = "",
) -> Tuple[bool, str, Optional[dict]]:
    racks, devices, placements = load_inventory()
    df = load_connections()
    ctype = str(connection_type).lower()
    if ctype not in TYPE_COLORS:
        ctype = "other"
    st = str(status).lower() if status else "active"

    # prefer placement; otherwise allow if device appears in inventory or existing links
    for side, rid, did in (
        ("source", source_rack_id, source_device_id),
        ("destination", destination_rack_id, destination_device_id),
    ):
        placed = placements[
            (placements["rack_id"].astype(int) == int(rid))
            & (placements["device_id"].astype(int) == int(did))
        ] if not placements.empty else placements
        if placed is not None and not placed.empty:
            continue
        in_devices = (
            not devices.empty
            and not devices[devices["device_id"].astype(int) == int(did)].empty
        )
        in_links = False
        if not df.empty:
            in_links = not df[
                (df["source_device_id"].astype(int) == int(did))
                | (df["destination_device_id"].astype(int) == int(did))
            ].empty
        if not in_devices and not in_links and int(rid) <= 0:
            return False, f"{side}: device {did} not found in inventory/connections", None

    if int(source_device_id) == int(destination_device_id) and str(source_port) == str(destination_port):
        return False, "Cannot connect a port to itself", None

    # duplicate check
    if not df.empty:
        dup = df[
            (
                (df["source_device_id"].astype(int) == int(source_device_id))
                & (df["source_port"].astype(str) == str(source_port))
            )
            | (
                (df["destination_device_id"].astype(int) == int(destination_device_id))
                & (df["destination_port"].astype(str) == str(destination_port))
            )
            | (
                (df["source_device_id"].astype(int) == int(destination_device_id))
                & (df["source_port"].astype(str) == str(destination_port))
            )
            | (
                (df["destination_device_id"].astype(int) == int(source_device_id))
                & (df["destination_port"].astype(str) == str(source_port))
            )
        ]
        if not dup.empty:
            return False, "One of the ports is already used in another connection", None

    cid = next_connection_id(df)
    row = {
        "connection_id": cid,
        "source_rack_id": int(source_rack_id),
        "source_device_id": int(source_device_id),
        "source_port": str(source_port),
        "destination_rack_id": int(destination_rack_id),
        "destination_device_id": int(destination_device_id),
        "destination_port": str(destination_port),
        "connection_type": ctype,
        "status": st,
        "notes": notes or "",
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    save_connections(df)
    return True, f"Connection {cid} created", row


def delete_connection(connection_id: int) -> bool:
    df = load_connections()
    if df.empty:
        return False
    before = len(df)
    df = df[df["connection_id"].astype(int) != int(connection_id)]
    save_connections(df)
    return len(df) < before



def build_topology_graph(datacenter_id: Optional[int] = None) -> Dict[str, Any]:
    """Topology graph grouped by datacenter and rack.

    Layout strategy:
    - Each datacenter is a separate horizontal band (or filter by datacenter_id)
    - Within a DC, racks are vertical columns
    - Devices stack inside rack by unit
    - Intra-rack edges stay short/straight; inter-rack edges curve
    """
    racks, devices, placements = load_inventory()
    conns = load_connections()
    dcs_df, _ = None, None
    try:
        from .inventory import load_datacenters
        dcs_df, _ = load_datacenters()
    except Exception:
        dcs_df = pd.DataFrame()

    dc_name = {}
    if dcs_df is not None and not dcs_df.empty:
        for _, r in dcs_df.iterrows():
            try:
                dc_name[int(r["datacenter_id"])] = str(r.get("datacenter_name") or f"DC {r['datacenter_id']}")
            except Exception:
                pass

    device_map: Dict[int, dict] = {}
    if not devices.empty:
        for _, r in devices.iterrows():
            try:
                device_map[int(r["device_id"])] = r.to_dict()
            except Exception:
                pass

    rack_map: Dict[int, dict] = {}
    if not racks.empty:
        for _, r in racks.iterrows():
            try:
                rid = int(r["rack_id"])
                rack_map[rid] = r.to_dict()
            except Exception:
                pass

    placement_info: Dict[int, dict] = {}
    if not placements.empty:
        for _, p in placements.iterrows():
            try:
                did = int(p["device_id"])
                rid = int(p["rack_id"])
                placement_info[did] = {
                    "rack_id": rid,
                    "start_unit": int(p["start_unit"]),
                    "end_unit": int(p["end_unit"]),
                }
            except Exception:
                pass

    # rack -> datacenter from inventory; fallback: first digit of rack_id (1001 -> 1)
    def rack_dc(rid: int) -> int:
        info = rack_map.get(rid) or {}
        if info.get("datacenter_id") is not None and str(info.get("datacenter_id")).strip() not in ("", "nan"):
            try:
                return int(float(info["datacenter_id"]))
            except Exception:
                pass
        # heuristic from rack_id like 1001, 2015, 3012
        try:
            return int(rid) // 1000 if int(rid) >= 1000 else 1
        except Exception:
            return 1

    rack_from_conn: Dict[int, int] = {}
    if not conns.empty:
        for _, c in conns.iterrows():
            try:
                sid, did = int(c["source_device_id"]), int(c["destination_device_id"])
                if sid not in rack_from_conn and str(c.get("source_rack_id", "")).strip() not in ("", "nan"):
                    rack_from_conn[sid] = int(float(c["source_rack_id"]))
                if did not in rack_from_conn and str(c.get("destination_rack_id", "")).strip() not in ("", "nan"):
                    rack_from_conn[did] = int(float(c["destination_rack_id"]))
            except Exception:
                pass

    node_ids = set(device_map.keys())
    if not conns.empty:
        for col in ("source_device_id", "destination_device_id"):
            if col not in conns.columns:
                continue
            for v in conns[col].dropna():
                try:
                    node_ids.add(int(float(v)))
                except Exception:
                    pass

    nodes = []
    for did in sorted(node_ids):
        d = device_map.get(did, {})
        place = placement_info.get(did, {})
        rid = place.get("rack_id") or rack_from_conn.get(did)
        dc = rack_dc(int(rid)) if rid is not None else None
        # filter by datacenter if requested
        if datacenter_id is not None and dc is not None and int(dc) != int(datacenter_id):
            continue
        if datacenter_id is not None and dc is None:
            continue
        rname = ""
        if rid is not None:
            rname = str((rack_map.get(int(rid)) or {}).get("rack_name") or f"Rack {rid}")
        nodes.append(
            {
                "id": f"d{did}",
                "device_id": did,
                "label": str(d.get("device_name") or f"Device {did}"),
                "model": str(d.get("model") or ""),
                "rack_id": int(rid) if rid is not None else None,
                "rack_name": rname or "—",
                "datacenter_id": dc,
                "datacenter_name": dc_name.get(dc, f"DC {dc}") if dc is not None else "—",
                "start_unit": place.get("start_unit"),
                "end_unit": place.get("end_unit"),
                "network_ports": int(d.get("network_ports") or 0),
                "power_ports": int(d.get("power_ports") or 0),
                "fiber_ports": int(d.get("fiber_ports") or 0),
                "group": f"dc{dc}-r{rid}" if rid is not None else "unplaced",
            }
        )

    allowed_ids = {n["device_id"] for n in nodes}

    edges = []
    if not conns.empty:
        for _, c in conns.iterrows():
            try:
                sid = int(c["source_device_id"])
                did = int(c["destination_device_id"])
            except Exception:
                continue
            if sid not in allowed_ids or did not in allowed_ids:
                continue
            raw_type = str(c.get("connection_type") or "network").strip().lower()
            if raw_type in ("copper", "lan"):
                ctype = "copper"
            elif raw_type in ("fiber", "fibre"):
                ctype = "fiber"
            elif raw_type == "power":
                ctype = "power"
            elif raw_type in ("network", "net"):
                ctype = "network"
            else:
                ctype = "other"
            status = str(c.get("status") or "active").strip().lower()
            if status in ("disabled", "inactive", "down"):
                status = "inactive"
            src_rack = None
            dst_rack = None
            try:
                if str(c.get("source_rack_id", "")).strip() not in ("", "nan"):
                    src_rack = int(float(c["source_rack_id"]))
                if str(c.get("destination_rack_id", "")).strip() not in ("", "nan"):
                    dst_rack = int(float(c["destination_rack_id"]))
            except Exception:
                pass
            same_rack = src_rack is not None and dst_rack is not None and src_rack == dst_rack
            edges.append(
                {
                    "id": f"c{int(c['connection_id'])}",
                    "connection_id": int(c["connection_id"]),
                    "from": f"d{sid}",
                    "to": f"d{did}",
                    "source_device_id": sid,
                    "destination_device_id": did,
                    "source_rack_id": src_rack,
                    "destination_rack_id": dst_rack,
                    "source_port": str(c.get("source_port") or ""),
                    "destination_port": str(c.get("destination_port") or ""),
                    "connection_type": ctype,
                    "status": status,
                    "notes": str(c.get("notes") or ""),
                    "color": TYPE_COLORS.get(ctype, TYPE_COLORS["other"]),
                    "label": f"{c.get('source_port')} → {c.get('destination_port')}",
                    "same_rack": same_rack,
                }
            )

    # Layout: DC bands (Y), racks as columns (X), devices stacked in rack (Y local)
    by_dc: Dict[Any, Dict[Any, List[dict]]] = {}
    for n in nodes:
        dc = n.get("datacenter_id") if n.get("datacenter_id") is not None else "unplaced"
        rid = n.get("rack_id") if n.get("rack_id") is not None else "unplaced"
        by_dc.setdefault(dc, {}).setdefault(rid, []).append(n)

    dc_order = sorted(by_dc.keys(), key=lambda x: (x == "unplaced", int(x) if str(x).isdigit() else 9999, str(x)))
    dc_gap = 640
    rack_gap = 360
    row_gap = 96

    racks_meta = []
    dcs_meta = []
    for dc_i, dc in enumerate(dc_order):
        rack_dict = by_dc[dc]
        rack_order = sorted(rack_dict.keys(), key=lambda x: (x == "unplaced", int(x) if str(x).isdigit() else 9999, str(x)))
        dcs_meta.append({
            "datacenter_id": dc,
            "name": dc_name.get(dc, f"DC {dc}") if dc != "unplaced" else "Unplaced",
            "y": dc_i * dc_gap,
            "rack_count": len(rack_order),
        })
        for rack_i, rid in enumerate(rack_order):
            group = rack_dict[rid]
            group.sort(key=lambda n: (n.get("start_unit") or 0, n.get("label") or ""))
            base_x = rack_i * rack_gap + 40
            base_y = dc_i * dc_gap + 40
            for row_i, n in enumerate(group):
                n["x"] = base_x
                n["y"] = base_y + row_i * row_gap
                n["fixed"] = {"x": True, "y": True}
            rname = ""
            if rid != "unplaced" and rid in rack_map:
                rname = str(rack_map[rid].get("rack_name") or f"Rack {rid}")
            else:
                rname = f"Rack {rid}" if rid != "unplaced" else "Unplaced"
            racks_meta.append({
                "rack_id": rid,
                "name": rname,
                "datacenter_id": dc,
                "x": base_x,
                "y": base_y - 36,
                "device_count": len(group),
            })

    return {
        "nodes": nodes,
        "edges": edges,
        "type_colors": TYPE_COLORS,
        "racks": racks_meta,
        "datacenters": dcs_meta,
    }





def build_rack_overview(datacenter_id: Optional[int] = None) -> Dict[str, Any]:
    """نمای سطح‌بالا: هر نود = یک رک، یال = تعداد لینک بین دو رک."""
    full = build_topology_graph(datacenter_id=datacenter_id)
    nodes_in = full.get("nodes") or []
    edges_in = full.get("edges") or []

    # device -> rack
    dev_rack: Dict[int, int] = {}
    rack_info: Dict[int, dict] = {}
    for n in nodes_in:
        rid = n.get("rack_id")
        if rid is None:
            continue
        rid = int(rid)
        dev_rack[int(n["device_id"])] = rid
        if rid not in rack_info:
            rack_info[rid] = {
                "rack_id": rid,
                "rack_name": n.get("rack_name") or f"Rack {rid}",
                "datacenter_id": n.get("datacenter_id"),
                "datacenter_name": n.get("datacenter_name") or "",
                "device_count": 0,
                "devices": [],
            }
        rack_info[rid]["device_count"] += 1
        rack_info[rid]["devices"].append({
            "device_id": n["device_id"],
            "label": n.get("label"),
        })

    # aggregate edges rack-rack
    pair_stats: Dict[tuple, dict] = {}
    for e in edges_in:
        s = dev_rack.get(int(e["source_device_id"]))
        d = dev_rack.get(int(e["destination_device_id"]))
        if s is None or d is None:
            continue
        if s == d:
            key = (s, s)
        else:
            key = (min(s, d), max(s, d))
        st = pair_stats.setdefault(
            key,
            {"count": 0, "types": {}, "same_rack": s == d, "samples": []},
        )
        st["count"] += 1
        ctype = e.get("connection_type") or "other"
        st["types"][ctype] = st["types"].get(ctype, 0) + 1
        if len(st["samples"]) < 5:
            st["samples"].append(
                f"{e.get('source_port')}→{e.get('destination_port')} ({ctype})"
            )

    # layout racks in a circle / grid by DC
    by_dc: Dict[Any, List[int]] = {}
    for rid, info in rack_info.items():
        dc = info.get("datacenter_id") if info.get("datacenter_id") is not None else "x"
        by_dc.setdefault(dc, []).append(rid)

    nodes = []
    col = 0
    RACK_COLORS = ["#8b5cf6", "#38bdf8", "#34d399", "#fbbf24", "#f472b6", "#22d3ee"]
    for dc, rids in sorted(by_dc.items(), key=lambda x: str(x[0])):
        rids = sorted(rids)
        for i, rid in enumerate(rids):
            info = rack_info[rid]
            nodes.append({
                "id": f"r{rid}",
                "rack_id": rid,
                "label": info["rack_name"],
                "title": f"{info['rack_name']}\n{info['device_count']} devices\n{info.get('datacenter_name') or ''}",
                "device_count": info["device_count"],
                "datacenter_id": info.get("datacenter_id"),
                "datacenter_name": info.get("datacenter_name") or "",
                "color": RACK_COLORS[col % len(RACK_COLORS)],
                "x": col * 320 + (i % 2) * 40,
                "y": (i // 1) * 140,
                "fixed": {"x": True, "y": True},
                "devices": info["devices"],
            })
        col += 1

    edges = []
    eid = 0
    for (a, b), st in pair_stats.items():
        if a == b:
            # self-loop for intra-rack: skip in overview or show as node badge only
            continue
        dominant = max(st["types"].items(), key=lambda x: x[1])[0] if st["types"] else "other"
        edges.append({
            "id": f"re{eid}",
            "from": f"r{a}",
            "to": f"r{b}",
            "count": st["count"],
            "connection_type": dominant,
            "types": st["types"],
            "samples": st["samples"],
            "color": TYPE_COLORS.get(dominant, TYPE_COLORS["other"]),
            "label": str(st["count"]),
            "title": f"{st['count']} links\n" + "\n".join(st["samples"]),
        })
        eid += 1

    # intra counts on racks
    intra = {rid: 0 for rid in rack_info}
    for (a, b), st in pair_stats.items():
        if a == b:
            intra[a] = st["count"]
    for n in nodes:
        n["intra_links"] = intra.get(n["rack_id"], 0)

    return {
        "mode": "racks",
        "nodes": nodes,
        "edges": edges,
        "type_colors": TYPE_COLORS,
        "datacenters": full.get("datacenters") or [],
    }


def build_rack_detail(rack_id: int) -> Dict[str, Any]:
    """جزئیات یک رک: فقط دستگاه‌های همان رک + لینک‌های داخلی و خروجی."""
    full = build_topology_graph()
    rid = int(rack_id)
    nodes = [n for n in (full.get("nodes") or []) if n.get("rack_id") is not None and int(n["rack_id"]) == rid]
    node_ids = {n["device_id"] for n in nodes}
    # also include remote endpoints of edges leaving the rack (as ghost nodes)
    extra = []
    edges = []
    for e in full.get("edges") or []:
        s, d = int(e["source_device_id"]), int(e["destination_device_id"])
        s_in, d_in = s in node_ids, d in node_ids
        if not s_in and not d_in:
            continue
        if s_in and d_in:
            e2 = dict(e)
            e2["same_rack"] = True
            e2["scope"] = "internal"
            edges.append(e2)
        else:
            e2 = dict(e)
            e2["same_rack"] = False
            e2["scope"] = "uplink"
            edges.append(e2)
            remote = d if s_in else s
            if remote not in node_ids and remote not in {x["device_id"] for x in extra}:
                # find remote in full
                remote_node = next((n for n in full["nodes"] if int(n["device_id"]) == remote), None)
                if remote_node:
                    rn = dict(remote_node)
                    rn["ghost"] = True
                    extra.append(rn)

    all_nodes = nodes + extra
    # layout: internal devices left column, ghost/uplink right
    internals = [n for n in all_nodes if not n.get("ghost")]
    ghosts = [n for n in all_nodes if n.get("ghost")]
    for i, n in enumerate(internals):
        n["x"] = 80
        n["y"] = 40 + i * 88
        n["fixed"] = {"x": True, "y": True}
    for i, n in enumerate(ghosts):
        n["x"] = 480
        n["y"] = 40 + i * 88
        n["fixed"] = {"x": True, "y": True}

    return {
        "mode": "rack_detail",
        "rack_id": rid,
        "nodes": all_nodes,
        "edges": edges,
        "type_colors": TYPE_COLORS,
    }








def build_elevation(datacenter_id: Optional[int] = None) -> Dict[str, Any]:
    """Elevation: همه دستگاه‌های رک (placement) + لینک‌ها از connections.csv."""
    racks_df, devices_df, placements_df = load_inventory()
    conns = load_connections()

    dc_name: Dict[int, str] = {}
    try:
        from .inventory import load_datacenters
        dcs_df, _ = load_datacenters()
        if dcs_df is not None and not dcs_df.empty:
            for _, r in dcs_df.iterrows():
                try:
                    dc_name[int(r["datacenter_id"])] = str(r.get("datacenter_name") or f"DC {r['datacenter_id']}")
                except Exception:
                    pass
    except Exception:
        pass

    inv_rack: Dict[int, dict] = {}
    if racks_df is not None and not racks_df.empty:
        for _, r in racks_df.iterrows():
            try:
                inv_rack[int(r["rack_id"])] = r.to_dict()
            except Exception:
                pass

    inv_dev: Dict[int, dict] = {}
    if devices_df is not None and not devices_df.empty:
        for _, r in devices_df.iterrows():
            try:
                inv_dev[int(r["device_id"])] = r.to_dict()
            except Exception:
                pass

    def rack_dc(rid: int) -> int:
        info = inv_rack.get(rid) or {}
        v = info.get("datacenter_id")
        if v is not None and str(v).strip() not in ("", "nan"):
            try:
                return int(float(v))
            except Exception:
                pass
        if rid >= 1000:
            return int(rid) // 1000
        return 1

    def rack_name(rid: int) -> str:
        n = str((inv_rack.get(rid) or {}).get("rack_name") or "").strip()
        return n if n and n.lower() != "nan" else f"Rack {rid}"

    def rack_units(rid: int) -> int:
        try:
            return int((inv_rack.get(rid) or {}).get("total_units") or 42)
        except Exception:
            return 42

    def dev_name(did: int) -> str:
        n = str((inv_dev.get(did) or {}).get("device_name") or "").strip()
        return n if n and n.lower() != "nan" else f"Device {did}"

    def dev_model(did: int) -> str:
        return str((inv_dev.get(did) or {}).get("model") or "")

    def dev_units(did: int) -> int:
        try:
            return max(1, int((inv_dev.get(did) or {}).get("units_used") or 1))
        except Exception:
            return 1

    # --- devices listed per rack from placements (ALL devices in rack) ---
    place_by_rack: Dict[int, List[dict]] = {}
    if placements_df is not None and not placements_df.empty:
        for _, p in placements_df.iterrows():
            try:
                rid = int(p["rack_id"])
                did = int(p["device_id"])
                su, eu = int(p["start_unit"]), int(p["end_unit"])
            except Exception:
                continue
            place_by_rack.setdefault(rid, []).append({
                "device_id": did,
                "start_unit": su,
                "end_unit": eu,
                "units": max(1, eu - su + 1),
                "from_placement": True,
            })

    # --- also devices seen in connections per rack ---
    conn_by_rack: Dict[int, List[int]] = {}
    if conns is not None and not conns.empty:
        for _, c in conns.iterrows():
            try:
                for rv, dv in (
                    (c.get("source_rack_id"), c.get("source_device_id")),
                    (c.get("destination_rack_id"), c.get("destination_device_id")),
                ):
                    if str(rv).strip() in ("", "nan") or str(dv).strip() in ("", "nan"):
                        continue
                    rid, did = int(float(rv)), int(float(dv))
                    conn_by_rack.setdefault(rid, [])
                    if did not in conn_by_rack[rid]:
                        conn_by_rack[rid].append(did)
            except Exception:
                continue

    # candidate racks = inventory (filtered) ∪ placement ∪ connections
    candidate = set(conn_by_rack.keys()) | set(place_by_rack.keys())
    if datacenter_id is not None:
        for rid in list(inv_rack.keys()):
            if rack_dc(rid) == int(datacenter_id):
                candidate.add(rid)
        candidate = {rid for rid in candidate if rack_dc(rid) == int(datacenter_id)}

    rack_rows: Dict[int, dict] = {}
    placed: List[dict] = []

    for rid in sorted(candidate):
        dc = rack_dc(rid)
        if datacenter_id is not None and int(dc) != int(datacenter_id):
            continue
        total = rack_units(rid)
        rack_rows[rid] = {
            "rack_id": int(rid),
            "rack_name": rack_name(rid),
            "total_units": total,
            "datacenter_id": dc,
            "datacenter_name": dc_name.get(dc, f"DC {dc}"),
        }

        # merge: all placement devices + connection-only devices
        have: Dict[int, dict] = {}
        for item in place_by_rack.get(rid, []):
            have[item["device_id"]] = dict(item)
        for did in conn_by_rack.get(rid, []):
            if did not in have:
                have[did] = {
                    "device_id": did,
                    "start_unit": None,
                    "end_unit": None,
                    "units": dev_units(did),
                    "from_placement": False,
                }

        # assign units for those without placement: stack from top, skip occupied
        occupied = set()
        for item in have.values():
            if item.get("start_unit") is not None:
                for u in range(int(item["start_unit"]), int(item["end_unit"]) + 1):
                    occupied.add(u)

        u_cursor = total
        # sort: placed first by start_unit desc, then connection-only by id
        ordered = sorted(
            have.values(),
            key=lambda x: (0 if x.get("from_placement") else 1, -(x.get("start_unit") or 0), x["device_id"]),
        )
        for item in ordered:
            did = int(item["device_id"])
            if item.get("start_unit") is not None:
                su, eu = int(item["start_unit"]), int(item["end_unit"])
                h = max(1, eu - su + 1)
            else:
                h = int(item.get("units") or 1)
                su = eu = None
                while u_cursor >= h:
                    su_try, eu_try = u_cursor - h + 1, u_cursor
                    if not any(x in occupied for x in range(su_try, eu_try + 1)):
                        su, eu = su_try, eu_try
                        break
                    u_cursor -= 1
                if su is None:
                    # find any free single unit
                    for uu in range(total, 0, -1):
                        if uu not in occupied:
                            su = eu = uu
                            h = 1
                            break
                if su is None:
                    su = eu = 1
                    h = 1
                u_cursor = su - 1
            for x in range(su, eu + 1):
                occupied.add(x)
            placed.append({
                "device_id": did,
                "rack_id": int(rid),
                "start_unit": int(su),
                "end_unit": int(eu),
                "units": int(h),
                "name": dev_name(did),
                "model": dev_model(did),
                "from_placement": bool(item.get("from_placement")),
            })

    visible_devs = {d["device_id"] for d in placed}
    links = []
    if conns is not None and not conns.empty:
        for _, c in conns.iterrows():
            try:
                sid = int(c["source_device_id"])
                did = int(c["destination_device_id"])
                sr = int(float(c["source_rack_id"])) if str(c.get("source_rack_id", "")).strip() not in ("", "nan") else None
                dr = int(float(c["destination_rack_id"])) if str(c.get("destination_rack_id", "")).strip() not in ("", "nan") else None
            except Exception:
                continue
            if sid not in visible_devs or did not in visible_devs:
                continue
            raw = str(c.get("connection_type") or "network").strip().lower()
            if raw in ("copper", "lan"):
                ctype = "copper"
            elif raw in ("fiber", "fibre"):
                ctype = "fiber"
            elif raw == "power":
                ctype = "power"
            elif raw in ("network", "net"):
                ctype = "network"
            else:
                ctype = "other"
            status = str(c.get("status") or "active").strip().lower()
            if status in ("disabled", "inactive", "down"):
                status = "inactive"
            links.append({
                "connection_id": int(c["connection_id"]) if str(c.get("connection_id", "")).strip() not in ("", "nan") else None,
                "source_device_id": sid,
                "destination_device_id": did,
                "source_rack_id": sr,
                "destination_rack_id": dr,
                "source_port": str(c.get("source_port") or ""),
                "destination_port": str(c.get("destination_port") or ""),
                "connection_type": ctype,
                "status": status,
                "same_rack": sr is not None and dr is not None and sr == dr,
                "color": TYPE_COLORS.get(ctype, TYPE_COLORS["other"]),
            })

    rack_list = sorted(rack_rows.values(), key=lambda r: (r.get("datacenter_id") or 0, r["rack_id"]))
    return {
        "mode": "elevation",
        "racks": rack_list,
        "devices": placed,
        "links": links,
        "type_colors": TYPE_COLORS,
        "datacenters": [{"datacenter_id": k, "name": v} for k, v in sorted(dc_name.items())],
        "meta": {
            "source": "placements+connections",
            "connection_rows": int(len(conns)) if conns is not None else 0,
            "rack_count": len(rack_list),
            "device_count": len(placed),
            "link_count": len(links),
            "engine": "elevation-v4",
        },
    }



def device_port_map(device_id: int) -> Dict[str, Any]:
    """All connections touching a device, grouped by port."""
    conns = load_connections()
    racks, devices, placements = load_inventory()
    drow = devices[devices["device_id"].astype(int) == int(device_id)]
    device = drow.iloc[0].to_dict() if not drow.empty else {"device_id": device_id}
    name_map = {}
    if not devices.empty:
        for _, r in devices.iterrows():
            name_map[int(r["device_id"])] = str(r.get("device_name") or r["device_id"])

    links = []
    if not conns.empty:
        for _, c in conns.iterrows():
            sid, did = int(c["source_device_id"]), int(c["destination_device_id"])
            if sid != int(device_id) and did != int(device_id):
                continue
            if sid == int(device_id):
                links.append(
                    {
                        "direction": "out",
                        "local_port": str(c.get("source_port") or ""),
                        "remote_device_id": did,
                        "remote_device_name": name_map.get(did, str(did)),
                        "remote_port": str(c.get("destination_port") or ""),
                        "connection_type": str(c.get("connection_type") or ""),
                        "status": str(c.get("status") or ""),
                        "connection_id": int(c["connection_id"]),
                    }
                )
            else:
                links.append(
                    {
                        "direction": "in",
                        "local_port": str(c.get("destination_port") or ""),
                        "remote_device_id": sid,
                        "remote_device_name": name_map.get(sid, str(sid)),
                        "remote_port": str(c.get("source_port") or ""),
                        "connection_type": str(c.get("connection_type") or ""),
                        "status": str(c.get("status") or ""),
                        "connection_id": int(c["connection_id"]),
                    }
                )
    return {
        "device": {
            "device_id": int(device_id),
            "device_name": str(device.get("device_name") or device_id),
            "model": str(device.get("model") or ""),
            "network_ports": int(device.get("network_ports") or 0),
            "power_ports": int(device.get("power_ports") or 0),
            "fiber_ports": int(device.get("fiber_ports") or 0),
        },
        "links": links,
    }
