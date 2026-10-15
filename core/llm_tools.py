"""
ابزارهای LLM برای پرس‌وجوی هوشمند از داده‌های دیتاسنتر
(رک، دیوایس، اتصالات)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union
import pandas as pd

from . import connections as conn_mod
from . import inventory


def _devices_df() -> pd.DataFrame:
    _, devices, _ = inventory.load_inventory()
    return devices if devices is not None else pd.DataFrame()


def _racks_df() -> pd.DataFrame:
    racks, _, _ = inventory.load_inventory()
    return racks if racks is not None else pd.DataFrame()


def _placements_df() -> pd.DataFrame:
    _, _, placements = inventory.load_inventory()
    return placements if placements is not None else pd.DataFrame()


def _connections_df() -> pd.DataFrame:
    return conn_mod.load_connections()


def _as_int(val: Any) -> Optional[int]:
    """Normalize id-like values from CSV (int/float/str) to int."""
    if val is None:
        return None
    try:
        if isinstance(val, str) and not val.strip():
            return None
        return int(float(val))
    except Exception:
        return None


def _device_name(device_id: Any) -> str:
    df = _devices_df()
    did = _as_int(device_id)
    if did is None:
        return str(device_id)
    if df.empty:
        return f"Device-{did}"
    ids = pd.to_numeric(df["device_id"], errors="coerce")
    row = df[ids == did]
    if row.empty:
        return f"Device-{did}"
    name = str(row.iloc[0].get("device_name", "") or "").strip()
    model = str(row.iloc[0].get("model", "") or "").strip()
    if name and model and name != model:
        return f"{name} ({model})"
    return name or model or f"Device-{did}"


def _rack_name(rack_id: Any) -> str:
    df = _racks_df()
    if df.empty:
        return str(rack_id)
    try:
        rid = int(float(rack_id))
    except Exception:
        return str(rack_id)
    row = df[df["rack_id"] == rid]
    if row.empty:
        return f"Rack-{rid}"
    return str(row.iloc[0].get("rack_name", "") or f"Rack-{rid}")


def find_device(query: str) -> List[Dict[str, Any]]:
    """جستجوی دیوایس با نام یا ID — ID از فایل devices، نام هم از همان."""
    df = _devices_df()
    if df.empty:
        return []
    q = str(query).strip().lower()
    q_id = _as_int(query)
    results = []
    ids = pd.to_numeric(df["device_id"], errors="coerce")
    for idx, row in df.iterrows():
        did = _as_int(row.get("device_id"))
        name = str(row.get("device_name", "") or "").lower()
        model = str(row.get("model", "") or "").lower()
        desc = str(row.get("description", "") or "").lower()
        did_s = str(did) if did is not None else ""
        match = False
        if q_id is not None and did == q_id:
            match = True
        elif q and (q == did_s or q in name or q in model or q in desc):
            match = True
        if match:
            results.append({
                "device_id": did,
                "device_name": row.get("device_name"),
                "model": row.get("model"),
                "description": row.get("description"),
                "network_ports": row.get("network_ports"),
                "fiber_ports": row.get("fiber_ports"),
                "datacenter_id": row.get("datacenter_id"),
            })
    # exact ID first
    if q_id is not None:
        exact = [r for r in results if r.get("device_id") == q_id]
        if exact:
            return exact[:15]
    return results[:15]


def find_rack(query: str) -> List[Dict[str, Any]]:
    """جستجوی رک با نام یا ID"""
    df = _racks_df()
    if df.empty:
        return []
    q = str(query).strip().lower()
    results = []
    for _, row in df.iterrows():
        rid = str(row.get("rack_id", ""))
        name = str(row.get("rack_name", "")).lower()
        if q == rid or q in name:
            results.append({
                "rack_id": int(row["rack_id"]) if pd.notna(row.get("rack_id")) else None,
                "rack_name": row.get("rack_name"),
                "total_units": row.get("total_units"),
                "datacenter_id": row.get("datacenter_id"),
            })
    return results


def get_device_connections(device: str) -> Dict[str, Any]:
    """
    همه اتصالات یک دیوایس (به عنوان مبدأ یا مقصد).
    device می‌تواند ID یا بخشی از نام باشد.
    """
    devices = find_device(device)
    if not devices:
        return {"error": f"دیوایسی با مشخصه «{device}» پیدا نشد.", "connections": []}

    # اگر چند تا پیدا شد، اولی را بگیر (یا همه را گزارش کن)
    target = devices[0]
    did = target["device_id"]
    df = _connections_df()
    if df.empty:
        return {
            "device": target,
            "connections": [],
            "message": "هیچ اتصالی در سیستم ثبت نشده است."
        }

    src = pd.to_numeric(df["source_device_id"], errors="coerce")
    dst = pd.to_numeric(df["destination_device_id"], errors="coerce")
    rows = df[(src == did) | (dst == did)]
    links = []
    for _, r in rows.iterrows():
        src_id = r.get("source_device_id")
        dst_id = r.get("destination_device_id")
        links.append({
            "connection_id": r.get("connection_id"),
            "from_device": _device_name(src_id),
            "from_device_id": int(src_id) if pd.notna(src_id) else None,
            "from_rack": _rack_name(r.get("source_rack_id")),
            "from_port": r.get("source_port"),
            "to_device": _device_name(dst_id),
            "to_device_id": int(dst_id) if pd.notna(dst_id) else None,
            "to_rack": _rack_name(r.get("destination_rack_id")),
            "to_port": r.get("destination_port"),
            "type": r.get("connection_type"),
            "status": r.get("status"),
            "notes": r.get("notes"),
        })

    return {
        "device": target,
        "total_connections": len(links),
        "connections": links,
    }


def get_rack_devices(rack: str) -> Dict[str, Any]:
    """دیوایس‌های داخل یک رک + محل قرارگیری"""
    racks = find_rack(rack)
    if not racks:
        return {"error": f"رکی با مشخصه «{rack}» پیدا نشد.", "devices": []}

    target = racks[0]
    rid = target["rack_id"]
    placements = _placements_df()
    devices = _devices_df()

    if placements.empty:
        return {"rack": target, "devices": [], "message": "هیچ placement ثبت نشده."}

    pl = placements[pd.to_numeric(placements["rack_id"], errors="coerce") == rid]
    result_devices = []
    for _, p in pl.iterrows():
        did = p.get("device_id")
        dev_row = devices[pd.to_numeric(devices["device_id"], errors="coerce") == _as_int(did)] if not devices.empty else pd.DataFrame()
        info = {
            "device_id": int(did) if pd.notna(did) else None,
            "start_unit": p.get("start_unit"),
            "end_unit": p.get("end_unit"),
        }
        if not dev_row.empty:
            info["device_name"] = dev_row.iloc[0].get("device_name")
            info["model"] = dev_row.iloc[0].get("model")
            info["description"] = dev_row.iloc[0].get("description")
        else:
            info["device_name"] = _device_name(did)
        result_devices.append(info)

    # مرتب بر اساس unit
    result_devices.sort(key=lambda x: (x.get("start_unit") or 0), reverse=True)

    return {
        "rack": target,
        "total_devices": len(result_devices),
        "devices": result_devices,
    }


def search_all(query: str) -> Dict[str, Any]:
    """جستجوی کلی روی دیوایس و رک"""
    return {
        "devices": find_device(query),
        "racks": find_rack(query),
    }


def list_racks() -> List[Dict[str, Any]]:
    """لیست همه رک‌ها"""
    df = _racks_df()
    if df.empty:
        return []
    out = []
    for _, row in df.iterrows():
        out.append({
            "rack_id": int(row["rack_id"]) if pd.notna(row.get("rack_id")) else None,
            "rack_name": row.get("rack_name"),
            "total_units": row.get("total_units"),
            "datacenter_id": row.get("datacenter_id"),
        })
    return out


def _resolve_device_and_rack(device_query: str) -> Dict[str, Any]:
    """دیوایس را از نام/ID پیدا می‌کند و rack_id را از placement برمی‌گرداند."""
    devices = find_device(device_query)
    if not devices:
        return {"error": f"دیوایسی با مشخصه «{device_query}» پیدا نشد."}
    target = devices[0]
    did = target["device_id"]
    placements = _placements_df()
    rack_id = None
    if not placements.empty and did is not None:
        pl = placements[pd.to_numeric(placements["device_id"], errors="coerce") == did]
        if not pl.empty:
            rack_id = int(pl.iloc[0]["rack_id"])
    # fallback: از اتصالات موجود rack را حدس بزن
    if rack_id is None:
        df = _connections_df()
        if not df.empty and did is not None:
            as_src = df[pd.to_numeric(df["source_device_id"], errors="coerce") == did]
            as_dst = df[pd.to_numeric(df["destination_device_id"], errors="coerce") == did]
            if not as_src.empty:
                rack_id = int(as_src.iloc[0]["source_rack_id"])
            elif not as_dst.empty:
                rack_id = int(as_dst.iloc[0]["destination_rack_id"])
    if rack_id is None:
        return {
            "error": f"رک دیوایس «{_device_name(did)}» مشخص نیست (در placement ثبت نشده).",
            "device": target,
        }
    return {
        "device_id": did,
        "rack_id": rack_id,
        "device_name": _device_name(did),
        "rack_name": _rack_name(rack_id),
        "device": target,
    }


def connect_devices(
    source_device: str,
    source_port: str,
    destination_device: str,
    destination_port: str,
    connection_type: str = "Fiber",
    notes: str = "",
) -> Dict[str, Any]:
    """
    ایجاد اتصال جدید بین دو دیوایس.
    source_device / destination_device می‌توانند ID یا بخشی از نام باشند.
    connection_type: Fiber | Copper | network | power
    """
    src = _resolve_device_and_rack(source_device)
    if src.get("error"):
        return src
    dst = _resolve_device_and_rack(destination_device)
    if dst.get("error"):
        return dst

    ctype = (connection_type or "Fiber").strip()
    # نرمال‌سازی نوع
    cl = ctype.lower()
    if cl in ("fiber", "fc", "san", "فیبر"):
        ctype = "Fiber"
    elif cl in ("copper", "network", "eth", "ethernet", "کاپر", "شبکه"):
        ctype = "Copper"
    elif cl in ("power", "برق"):
        ctype = "power"
    else:
        ctype = connection_type or "Fiber"

    ok, msg, row = conn_mod.create_connection(
        source_rack_id=int(src["rack_id"]),
        source_device_id=int(src["device_id"]),
        source_port=str(source_port).strip(),
        destination_rack_id=int(dst["rack_id"]),
        destination_device_id=int(dst["device_id"]),
        destination_port=str(destination_port).strip(),
        connection_type=ctype,
        status="active",
        notes=notes or "",
    )
    if not ok:
        return {
            "success": False,
            "message": msg,
            "source": {"device": src["device_name"], "rack": src["rack_name"], "port": source_port},
            "destination": {"device": dst["device_name"], "rack": dst["rack_name"], "port": destination_port},
        }
    return {
        "success": True,
        "message": msg,
        "connection_id": row.get("connection_id") if row else None,
        "connection": {
            "from": f"{src['device_name']} ({src['rack_name']}) پورت {source_port}",
            "to": f"{dst['device_name']} ({dst['rack_name']}) پورت {destination_port}",
            "type": ctype,
            "status": "active",
        },
    }


def delete_connection_tool(
    connection_id: Optional[str] = None,
    source_device: Optional[str] = None,
    source_port: Optional[str] = None,
    destination_device: Optional[str] = None,
    destination_port: Optional[str] = None,
) -> Dict[str, Any]:
    """
    حذف اتصال.
    یا با connection_id، یا با مشخص کردن دیوایس/پورت مبدأ و مقصد.
    """
    df = _connections_df()
    if df.empty:
        return {"success": False, "message": "هیچ اتصالی در سیستم ثبت نشده است."}

    cid = None
    if connection_id is not None and str(connection_id).strip():
        try:
            cid = int(float(str(connection_id).strip()))
        except Exception:
            return {"success": False, "message": f"شناسه اتصال نامعتبر: {connection_id}"}
    else:
        # جستجو بر اساس دیوایس و پورت
        if not source_device and not destination_device:
            return {
                "success": False,
                "message": "برای حذف، یا connection_id بده یا حداقل یک طرف اتصال (دیوایس/پورت) را مشخص کن.",
            }
        mask = pd.Series([True] * len(df))
        if source_device:
            src_devs = find_device(source_device)
            if not src_devs:
                return {"success": False, "message": f"دیوایس مبدأ «{source_device}» پیدا نشد."}
            sid = src_devs[0]["device_id"]
            mask = mask & (
                (df["source_device_id"] == sid) | (df["destination_device_id"] == sid)
            )
            if source_port:
                sp = str(source_port).strip().lower()
                mask = mask & (
                    (df["source_port"].astype(str).str.lower() == sp)
                    | (df["destination_port"].astype(str).str.lower() == sp)
                )
        if destination_device:
            dst_devs = find_device(destination_device)
            if not dst_devs:
                return {"success": False, "message": f"دیوایس مقصد «{destination_device}» پیدا نشد."}
            did = dst_devs[0]["device_id"]
            mask = mask & (
                (df["source_device_id"] == did) | (df["destination_device_id"] == did)
            )
            if destination_port:
                dp = str(destination_port).strip().lower()
                mask = mask & (
                    (df["source_port"].astype(str).str.lower() == dp)
                    | (df["destination_port"].astype(str).str.lower() == dp)
                )
        matches = df[mask]
        if matches.empty:
            return {"success": False, "message": "اتصالی با این مشخصات پیدا نشد."}
        if len(matches) > 1 and not (source_port or destination_port):
            # چند تا پیدا شد — لیست کن تا کاربر دقیق‌تر بگوید
            items = []
            for _, r in matches.iterrows():
                items.append({
                    "connection_id": int(r["connection_id"]),
                    "from": f"{_device_name(r['source_device_id'])} پورت {r.get('source_port')}",
                    "to": f"{_device_name(r['destination_device_id'])} پورت {r.get('destination_port')}",
                    "type": r.get("connection_type"),
                })
            return {
                "success": False,
                "message": "چند اتصال پیدا شد. یکی را با connection_id مشخص کن یا پورت را دقیق‌تر بگو.",
                "candidates": items,
            }
        cid = int(matches.iloc[0]["connection_id"])

    # جزئیات قبل از حذف برای گزارش
    row = df[df["connection_id"].astype(int) == int(cid)]
    detail = None
    if not row.empty:
        r = row.iloc[0]
        detail = {
            "connection_id": int(cid),
            "from": f"{_device_name(r['source_device_id'])} پورت {r.get('source_port')}",
            "to": f"{_device_name(r['destination_device_id'])} پورت {r.get('destination_port')}",
            "type": r.get("connection_type"),
        }

    ok = conn_mod.delete_connection(int(cid))
    if not ok:
        return {"success": False, "message": f"اتصال با شناسه {cid} پیدا نشد یا حذف نشد."}
    return {
        "success": True,
        "message": f"اتصال {cid} با موفقیت حذف شد.",
        "deleted": detail,
    }


# ---------- Tool definitions for Ollama ----------
OLLAMA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_device_connections",
            "description": "لیست تمام اتصالات (لینک‌های شبکه/فیبر/کاپر) یک دیوایس را برمی‌گرداند. با نام یا ID دیوایس صدا بزن.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device": {
                        "type": "string",
                        "description": "نام یا ID دیوایس مثلا 1004 یا SW-2960 یا Cisco"
                    }
                },
                "required": ["device"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_rack_devices",
            "description": "لیست دیوایس‌های داخل یک رک را برمی‌گرداند (با واحد رک). با نام رک مثل MOKHABERAT یا BACKUP یا SERVER یا ID رک صدا بزن.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rack": {
                        "type": "string",
                        "description": "نام یا ID رک مثلا MOKHABERAT یا 4 یا SAN"
                    }
                },
                "required": ["rack"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_device",
            "description": "جستجوی دیوایس بر اساس نام، مدل یا ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "عبارت جستجو"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_racks",
            "description": "لیست تمام رک‌های دیتاسنتر را برمی‌گرداند",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_all",
            "description": "جستجوی همزمان در دیوایس‌ها و رک‌ها",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "عبارت جستجو"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "connect_devices",
            "description": "ایجاد اتصال جدید بین دو دیوایس. وقتی کاربر می‌گوید «وصل کن»، «لینک بزن»، «connect» از این ابزار استفاده کن. نام یا ID دیوایس و نام پورت را بده.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_device": {
                        "type": "string",
                        "description": "نام یا ID دیوایس مبدأ مثلا 2013 یا SAN-Switch"
                    },
                    "source_port": {
                        "type": "string",
                        "description": "پورت مبدأ مثلا port 2 یا 0/3 یا lan"
                    },
                    "destination_device": {
                        "type": "string",
                        "description": "نام یا ID دیوایس مقصد مثلا 1016 یا SRV-DELL"
                    },
                    "destination_port": {
                        "type": "string",
                        "description": "پورت مقصد مثلا port 6 یا fiber"
                    },
                    "connection_type": {
                        "type": "string",
                        "description": "نوع لینک: Fiber یا Copper یا power (پیش‌فرض Fiber)"
                    },
                    "notes": {
                        "type": "string",
                        "description": "یادداشت اختیاری"
                    }
                },
                "required": ["source_device", "source_port", "destination_device", "destination_port"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_connection",
            "description": "حذف یک اتصال موجود. وقتی کاربر می‌گوید «حذف کن»، «قطع کن»، «پاک کن» از این ابزار استفاده کن. یا connection_id بده یا دیوایس/پورت مبدأ و مقصد را مشخص کن.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {
                        "type": "string",
                        "description": "شناسه عددی اتصال مثلا 38"
                    },
                    "source_device": {
                        "type": "string",
                        "description": "نام یا ID دیوایس یکی از دو طرف اتصال"
                    },
                    "source_port": {
                        "type": "string",
                        "description": "پورت مربوط به همان دیوایس"
                    },
                    "destination_device": {
                        "type": "string",
                        "description": "نام یا ID دیوایس طرف دیگر"
                    },
                    "destination_port": {
                        "type": "string",
                        "description": "پورت طرف دیگر"
                    }
                },
                "required": []
            }
        }
    },
]


def execute_tool(name: str, arguments: Dict[str, Any]) -> Any:
    """اجرای یک tool بر اساس نام و آرگومان‌ها"""
    args = arguments or {}
    if name == "get_device_connections":
        return get_device_connections(str(args.get("device", "")))
    if name == "get_rack_devices":
        return get_rack_devices(str(args.get("rack", "")))
    if name == "find_device":
        return find_device(str(args.get("query", "")))
    if name == "list_racks":
        return list_racks()
    if name == "search_all":
        return search_all(str(args.get("query", "")))
    if name == "connect_devices":
        return connect_devices(
            source_device=str(args.get("source_device", "")),
            source_port=str(args.get("source_port", "")),
            destination_device=str(args.get("destination_device", "")),
            destination_port=str(args.get("destination_port", "")),
            connection_type=str(args.get("connection_type") or "Fiber"),
            notes=str(args.get("notes") or ""),
        )
    if name == "delete_connection":
        return delete_connection_tool(
            connection_id=args.get("connection_id"),
            source_device=args.get("source_device"),
            source_port=args.get("source_port"),
            destination_device=args.get("destination_device"),
            destination_port=args.get("destination_port"),
        )
    return {"error": f"ابزار ناشناخته: {name}"}
