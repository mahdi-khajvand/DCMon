"""
DC Monitor Pro — FastAPI + modern SPA + Bale Bot
uvicorn app:app --host 0.0.0.0 --port 8600 --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional

import pandas as pd
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pathlib import Path
import shutil

from core import auth, inventory, connections
from core import monitoring_core as mc
from core import bale_integration as bale
from core import llm_service as llm_svc


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    inventory.ensure_data_files()
    mc.ensure_monitoring_storage()
    auth.ensure_users_file()
    try:
        bale.ensure_started_if_configured()
    except Exception as e:
        print(f"[bale] auto-start skipped: {e}")
    yield
    # shutdown
    try:
        bale.stop_runner()
    except Exception:
        pass


app = FastAPI(title="DC Monitor Pro", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
STATIC = Path(__file__).parent / "static"

inventory.ensure_data_files()
mc.ensure_monitoring_storage()
auth.ensure_users_file()


def require_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Unauthorized")
    sess = auth.resolve_token(authorization.split(" ", 1)[1])
    if not sess:
        raise HTTPException(401, "Session expired")
    return sess


def require_edit(sess: dict = Depends(require_user)) -> dict:
    if not auth.can_edit(sess):
        raise HTTPException(403, f"Not allowed: role={sess.get('role')} needs admin/editor")
    return sess


def require_admin(sess: dict = Depends(require_user)) -> dict:
    if not auth.is_admin(sess):
        raise HTTPException(403, "فقط ادمین")
    return sess


def scoped_inventory(sess: dict):
    racks, devices, placements = inventory.load_inventory()
    return inventory.apply_dc_scope(
        racks, devices, placements, sess.get("allowed_dcs")
    )


# ---------- models ----------
class LoginIn(BaseModel):
    username: str
    password: str


class RackIn(BaseModel):
    rack_name: str
    datacenter_id: int = 1
    total_units: int = 42
    description: str = ""


class DeviceIn(BaseModel):
    device_name: str
    model: str = ""
    units_used: int = 1
    network_ports: int = 0
    power_ports: int = 0
    fiber_ports: int = 0
    description: str = ""
    datacenter_id: int = 1


class PlacementIn(BaseModel):
    rack_id: int
    device_id: int
    start_unit: int = 1


class DcIn(BaseModel):
    datacenter_name: str
    location: str = ""
    description: str = ""


class LinkIn(BaseModel):
    source_dc_id: int
    destination_dc_id: int
    link_type: str = "Dark Fiber"
    capacity_gbps: float = 10
    status: str = "UP"
    provider: str = ""
    description: str = ""


class MonDeviceIn(BaseModel):
    display_name: str
    host: str
    vendor: str = "Unknown"
    model: str = "Unknown"
    template_id: str = "tpl-generic-snmp"
    credential_id: str = ""
    poll_mode: str = "demo"
    network_ports: int = 24
    datacenter_id: str = ""


class AckIn(BaseModel):
    id: str


# ---------- auth ----------
@app.post("/api/login")
def login(body: LoginIn):
    result = auth.login(body.username, body.password)
    if not result:
        raise HTTPException(401, "ACCESS DENIED")
    return result


@app.get("/api/setup/status")
def setup_status():
    """وضعیت راه‌اندازی اولیه — بدون نیاز به لاگین."""
    return {"needs_setup": auth.needs_setup()}


@app.post("/api/setup/enter")
def setup_enter():
    """ورود بدون یوزر/پسورد فقط در اولین راه‌اندازی."""
    result = auth.bootstrap_setup_session()
    if not result:
        raise HTTPException(403, "سیستم قبلاً راه‌اندازی شده؛ با یوزر ادمین وارد شوید")
    return result


@app.get("/api/me")
def me(sess: dict = Depends(require_user)):
    return {
        "user": sess["user"],
        "role": sess["role"],
        "allowed_dcs": list(sess["allowed_dcs"])
        if isinstance(sess["allowed_dcs"], set)
        else sess["allowed_dcs"],
        "setup_mode": bool(sess.get("setup_mode")),
        "needs_setup": auth.needs_setup(),
    }


# ---------- summary / dashboard ----------
@app.get("/api/summary")
def summary(sess: dict = Depends(require_user)):
    racks, devices, placements = scoped_inventory(sess)
    inv = inventory.inventory_summary(racks, devices, placements)
    mc.sync_inventory_devices(devices)
    mon = mc.list_monitoring_devices()
    snaps = mc.build_snapshot_table(mon)
    if not snaps.empty:
        mc.sync_alerts(snaps)
    alerts = mc.load_table("alerts")
    traps = mc.load_table("traps")
    up = int((snaps["status"] == "UP").sum()) if not snaps.empty else 0
    down = int((snaps["status"] == "DOWN").sum()) if not snaps.empty else 0
    active_alerts = (
        int(alerts["status"].isin(["OPEN", "ACKNOWLEDGED"]).sum()) if not alerts.empty else 0
    )
    # topology / connections stats
    try:
        conn_df = connections.load_connections()
        conn_total = int(len(conn_df)) if conn_df is not None and not conn_df.empty else 0
        conn_active = 0
        if conn_df is not None and not conn_df.empty and "status" in conn_df.columns:
            conn_active = int(conn_df["status"].astype(str).str.lower().isin(["active", "up", ""]).sum())
            if conn_active == 0:
                conn_active = conn_total
        by_type = {}
        if conn_df is not None and not conn_df.empty and "connection_type" in conn_df.columns:
            for t, g in conn_df.groupby(conn_df["connection_type"].astype(str).str.lower()):
                by_type[str(t)] = int(len(g))
    except Exception:
        conn_total, conn_active, by_type = 0, 0, {}

    # racks per datacenter
    dc_racks = []
    try:
        dcs, _ = inventory.load_datacenters()
        if dcs is not None and not dcs.empty and racks is not None and not racks.empty:
            for _, r in dcs.iterrows():
                try:
                    did = int(r["datacenter_id"])
                except Exception:
                    continue
                cnt = 0
                if "datacenter_id" in racks.columns:
                    cnt = int((pd.to_numeric(racks["datacenter_id"], errors="coerce") == did).sum())
                dc_racks.append({
                    "datacenter_id": did,
                    "name": str(r.get("datacenter_name") or f"DC {did}"),
                    "racks": cnt,
                })
    except Exception:
        pass

    return {
        **inv,
        "monitored": len(snaps),
        "up": up,
        "down": down,
        "availability": round((up / max(1, up + down)) * 100, 1),
        "active_alerts": active_alerts,
        "traps": len(traps),
        "connections": conn_total,
        "connections_active": conn_active,
        "connections_by_type": by_type,
        "dc_racks": dc_racks,
        "role": sess["role"],
        "user": sess["user"],
    }


@app.get("/api/dashboard/health")
def dash_health(sess: dict = Depends(require_user)):
    _, devices, _ = scoped_inventory(sess)
    mc.sync_inventory_devices(devices)
    mon = mc.list_monitoring_devices()
    snaps = mc.build_snapshot_table(mon)
    if snaps.empty:
        return {"devices": [], "alerts": []}
    rows = snaps[
        [
            "device_name",
            "host",
            "vendor",
            "model",
            "status",
            "cpu_percent",
            "memory_percent",
            "temperature_c",
            "source",
        ]
    ].to_dict("records")
    alerts = mc.load_table("alerts")
    active = []
    if not alerts.empty:
        a = alerts[alerts["status"].isin(["OPEN", "ACKNOWLEDGED"])]
        active = a.to_dict("records")
    return {"devices": rows, "alerts": active}


# ---------- inventory ----------
@app.get("/api/racks")
def get_racks(sess: dict = Depends(require_user)):
    racks, _, _ = scoped_inventory(sess)
    return racks.fillna("").to_dict("records")


@app.post("/api/racks")
def post_rack(body: RackIn, sess: dict = Depends(require_edit)):
    racks, devices, placements = inventory.load_inventory()
    racks, ok, msg = inventory.create_rack(
        racks, body.rack_name, body.datacenter_id, body.total_units, body.description
    )
    if not ok:
        raise HTTPException(400, msg)
    inventory.save_inventory(racks, devices, placements)
    return {"ok": True, "message": msg}


@app.get("/api/devices")
def get_devices(sess: dict = Depends(require_user)):
    _, devices, _ = scoped_inventory(sess)
    return devices.fillna("").to_dict("records")


@app.post("/api/devices")
def post_device(body: DeviceIn, sess: dict = Depends(require_edit)):
    racks, devices, placements = inventory.load_inventory()
    devices, ok, msg = inventory.create_device(
        devices,
        body.device_name,
        body.model,
        body.units_used,
        body.network_ports,
        body.power_ports,
        body.fiber_ports,
        body.description,
        body.datacenter_id,
    )
    if not ok:
        raise HTTPException(400, msg)
    inventory.save_inventory(racks, devices, placements)
    return {"ok": True, "message": msg}


@app.delete("/api/devices/{device_id}")
def del_device(device_id: int, sess: dict = Depends(require_edit)):
    racks, devices, placements = inventory.load_inventory()
    racks, devices, placements, ok, msg = inventory.delete_device(
        racks, devices, placements, device_id
    )
    if not ok:
        raise HTTPException(404, msg)
    # drop connections involving this device
    try:
        cdf = connections.load_connections()
        if not cdf.empty:
            src = pd.to_numeric(cdf["source_device_id"], errors="coerce")
            dst = pd.to_numeric(cdf["destination_device_id"], errors="coerce")
            cdf = cdf[~((src == int(device_id)) | (dst == int(device_id)))]
            connections.save_connections(cdf)
    except Exception:
        pass
    inventory.save_inventory(racks, devices, placements)
    return {"ok": True, "message": msg}


@app.delete("/api/racks/{rack_id}")
def del_rack(rack_id: int, sess: dict = Depends(require_edit)):
    racks, devices, placements = inventory.load_inventory()
    racks, devices, placements, ok, msg = inventory.delete_rack(
        racks, devices, placements, rack_id
    )
    if not ok:
        raise HTTPException(404, msg)
    try:
        cdf = connections.load_connections()
        if not cdf.empty:
            src = pd.to_numeric(cdf["source_rack_id"], errors="coerce")
            dst = pd.to_numeric(cdf["destination_rack_id"], errors="coerce")
            cdf = cdf[~((src == int(rack_id)) | (dst == int(rack_id)))]
            connections.save_connections(cdf)
    except Exception:
        pass
    inventory.save_inventory(racks, devices, placements)
    return {"ok": True, "message": msg}


@app.get("/api/placements")
def get_placements(sess: dict = Depends(require_user)):
    racks, devices, placements = scoped_inventory(sess)
    if placements.empty:
        return []
    merged = placements.merge(
        racks[["rack_id", "rack_name"]] if not racks.empty else placements,
        on="rack_id",
        how="left",
    ).merge(
        devices[["device_id", "device_name", "model", "units_used"]]
        if not devices.empty
        else placements,
        on="device_id",
        how="left",
    )
    return merged.fillna("").to_dict("records")


@app.post("/api/placements")
def post_placement(body: PlacementIn, sess: dict = Depends(require_edit)):
    racks, devices, placements = inventory.load_inventory()
    placements, ok, msg = inventory.place_device(
        racks, devices, placements, body.rack_id, body.device_id, body.start_unit
    )
    if not ok:
        raise HTTPException(400, msg)
    inventory.save_inventory(racks, devices, placements)
    return {"ok": True, "message": msg}


@app.delete("/api/placements/{placement_id}")
def del_placement(placement_id: int, sess: dict = Depends(require_edit)):
    racks, devices, placements = inventory.load_inventory()
    if placements.empty or placement_id not in placements["placement_id"].astype(int).values:
        raise HTTPException(404, "یافت نشد")
    placements = placements[placements["placement_id"].astype(int) != placement_id]
    inventory.save_inventory(racks, devices, placements)
    return {"ok": True}


def _rack_image_paths(rack_id: int, rack_name: str) -> tuple[Path, list[Path]]:
    """Return preferred save path and all candidate paths for a rack image."""
    base = Path(__file__).parent / "assets" / "racks"
    base.mkdir(parents=True, exist_ok=True)
    name = str(rack_name or "").strip()
    preferred = base / f"{name}.jpg" if name else base / f"rack_{rack_id}.jpg"
    candidates = []
    for key in (name, name.upper(), name.lower(), name.replace(" ", "_"), str(rack_id), f"rack_{rack_id}", f"rack-{rack_id}"):
        if not key:
            continue
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG"):
            candidates.append(base / f"{key}{ext}")
    return preferred, candidates


@app.get("/api/racks/{rack_id}/image")
def rack_image(rack_id: int, sess: dict = Depends(require_user)):
    """Resolve rack photo from assets/racks/*.jpg"""
    racks, _, _ = scoped_inventory(sess)
    rack = racks[racks["rack_id"].astype(int) == int(rack_id)] if not racks.empty else racks
    if rack is None or rack.empty:
        raise HTTPException(404, "Rack not found")
    row = rack.iloc[0]
    name = str(row.get("rack_name") or "").strip()
    _, candidates = _rack_image_paths(rack_id, name)
    for p in candidates:
        if p.exists() and p.is_file():
            return {"url": f"/assets/racks/{p.name}", "file": p.name, "found": True}
    available = [f.name for f in (Path(__file__).parent / "assets" / "racks").glob("*") if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
    return {"url": None, "found": False, "available": available, "tried_name": name}


@app.post("/api/racks/{rack_id}/image")
async def upload_rack_image(
    rack_id: int,
    file: UploadFile = File(...),
    sess: dict = Depends(require_edit),
):
    """Upload or replace rack photo. Accepts jpg/png/webp."""
    racks, _, _ = scoped_inventory(sess)
    rack = racks[racks["rack_id"].astype(int) == int(rack_id)] if not racks.empty else racks
    if rack is None or rack.empty:
        raise HTTPException(404, "Rack not found")
    row = rack.iloc[0]
    name = str(row.get("rack_name") or "").strip()
    content_type = (file.content_type or "").lower()
    allowed_ct = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/pjpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    orig = (file.filename or "").lower()
    ext = allowed_ct.get(content_type)
    if not ext:
        if orig.endswith((".jpg", ".jpeg")):
            ext = ".jpg"
        elif orig.endswith(".png"):
            ext = ".png"
        elif orig.endswith(".webp"):
            ext = ".webp"
    if not ext:
        raise HTTPException(400, "فرمت مجاز: JPG, PNG, WEBP")

    preferred, candidates = _rack_image_paths(rack_id, name)
    # remove any existing candidates so we don't leave stale files
    for p in candidates:
        try:
            if p.exists() and p.is_file():
                p.unlink()
        except Exception:
            pass

    save_path = preferred.with_suffix(ext)
    try:
        with save_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception as e:
        raise HTTPException(500, f"ذخیره فایل ناموفق: {e}")

    return {
        "ok": True,
        "url": f"/assets/racks/{save_path.name}",
        "file": save_path.name,
        "found": True,
    }


@app.delete("/api/racks/{rack_id}/image")
def delete_rack_image(rack_id: int, sess: dict = Depends(require_edit)):
    """Delete rack photo if present."""
    racks, _, _ = scoped_inventory(sess)
    rack = racks[racks["rack_id"].astype(int) == int(rack_id)] if not racks.empty else racks
    if rack is None or rack.empty:
        raise HTTPException(404, "Rack not found")
    row = rack.iloc[0]
    name = str(row.get("rack_name") or "").strip()
    _, candidates = _rack_image_paths(rack_id, name)
    deleted = []
    for p in candidates:
        try:
            if p.exists() and p.is_file():
                p.unlink()
                deleted.append(p.name)
        except Exception:
            pass
    return {"ok": True, "deleted": deleted}


@app.get("/api/racks/{rack_id}/units")
def rack_units(rack_id: int, sess: dict = Depends(require_user)):
    racks, devices, placements = scoped_inventory(sess)
    rack = racks[racks["rack_id"].astype(int) == int(rack_id)]
    if rack.empty:
        raise HTTPException(404, "رک یافت نشد")
    total = int(rack.iloc[0]["total_units"])
    rp = placements[placements["rack_id"].astype(int) == int(rack_id)]
    if not rp.empty and not devices.empty:
        rp = rp.merge(
            devices[["device_id", "device_name", "model", "units_used"]],
            on="device_id",
            how="left",
        )
    rows = []
    for unit in range(total, 0, -1):
        occ = rp[(rp["start_unit"] <= unit) & (rp["end_unit"] >= unit)] if not rp.empty else rp
        if occ is None or len(occ) == 0:
            rows.append({"unit": unit, "status": "EMPTY", "device_name": "", "model": "", "range": ""})
        else:
            o = occ.iloc[0]
            rows.append(
                {
                    "unit": unit,
                    "status": "USED",
                    "device_name": str(o.get("device_name", "")),
                    "model": str(o.get("model", "")),
                    "range": f"{int(o['start_unit'])}-{int(o['end_unit'])}",
                }
            )
    return {"rack": rack.iloc[0].fillna("").to_dict(), "units": rows}


# ---------- datacenters ----------
@app.get("/api/datacenters")
def get_dcs(sess: dict = Depends(require_user)):
    dcs, links = inventory.load_datacenters()
    return {
        "datacenters": dcs.fillna("").to_dict("records"),
        "links": links.fillna("").to_dict("records"),
    }


@app.post("/api/datacenters")
def post_dc(body: DcIn, sess: dict = Depends(require_edit)):
    dcs, links = inventory.load_datacenters()
    next_id = 1 if dcs.empty else int(pd.to_numeric(dcs["datacenter_id"], errors="coerce").max()) + 1
    row = {
        "datacenter_id": next_id,
        "datacenter_name": body.datacenter_name.strip(),
        "location": body.location,
        "description": body.description,
    }
    dcs = pd.concat([dcs, pd.DataFrame([row])], ignore_index=True)
    inventory.save_datacenters(dcs, links)
    return {"ok": True, "id": next_id}


@app.delete("/api/datacenters/{datacenter_id}")
def del_dc(datacenter_id: int, sess: dict = Depends(require_edit)):
    dcs, links = inventory.load_datacenters()
    racks, devices, placements = inventory.load_inventory()
    dcs, links, racks, devices, placements, ok, msg = inventory.delete_datacenter(
        dcs, links, racks, devices, placements, datacenter_id
    )
    if not ok:
        raise HTTPException(404, msg)
    inventory.save_datacenters(dcs, links)
    inventory.save_inventory(racks, devices, placements)
    return {"ok": True, "message": msg}


@app.post("/api/dci-links")
def post_link(body: LinkIn, sess: dict = Depends(require_edit)):
    dcs, links = inventory.load_datacenters()
    next_id = 1 if links.empty else int(pd.to_numeric(links["link_id"], errors="coerce").max()) + 1
    row = {
        "link_id": next_id,
        "source_dc_id": body.source_dc_id,
        "destination_dc_id": body.destination_dc_id,
        "link_type": body.link_type,
        "capacity_gbps": body.capacity_gbps,
        "status": body.status,
        "provider": body.provider,
        "description": body.description,
    }
    links = pd.concat([links, pd.DataFrame([row])], ignore_index=True)
    inventory.save_datacenters(dcs, links)
    return {"ok": True, "id": next_id}


@app.delete("/api/dci-links/{link_id}")
def del_link(link_id: int, sess: dict = Depends(require_edit)):
    dcs, links = inventory.load_datacenters()
    links = links[links["link_id"].astype(int) != int(link_id)]
    inventory.save_datacenters(dcs, links)
    return {"ok": True}


class DcLayoutIn(BaseModel):
    positions: list  # [{datacenter_id, x, y}, ...]  x/y in 0..100 percent of map


@app.post("/api/datacenters/layout")
def save_dc_layout(body: DcLayoutIn, sess: dict = Depends(require_edit)):
    """ذخیره موقعیت نودهای نقشه دیتاسنتر (درصدی روی بوم)."""
    dcs, links = inventory.load_datacenters()
    if dcs.empty:
        return {"ok": True}
    # ensure columns
    if "topology_x" not in dcs.columns:
        dcs["topology_x"] = ""
    if "topology_y" not in dcs.columns:
        dcs["topology_y"] = ""
    for item in body.positions or []:
        try:
            did = int(item.get("datacenter_id"))
            x = float(item.get("x"))
            y = float(item.get("y"))
        except Exception:
            continue
        x = max(0.0, min(100.0, x))
        y = max(0.0, min(100.0, y))
        mask = pd.to_numeric(dcs["datacenter_id"], errors="coerce") == did
        dcs.loc[mask, "topology_x"] = x
        dcs.loc[mask, "topology_y"] = y
    inventory.save_datacenters(dcs, links)
    return {"ok": True}


def _dc_map_settings_path() -> Path:
    return Path(__file__).parent / "data" / "dc_map_settings.json"


def _dc_map_dir() -> Path:
    p = Path(__file__).parent / "assets" / "maps"
    p.mkdir(parents=True, exist_ok=True)
    return p


@app.get("/api/datacenters/map")
def get_dc_map(sess: dict = Depends(require_user)):
    """تنظیمات نقشه ویژوال: بک‌گراند + موقعیت‌ها."""
    import json
    dcs, links = inventory.load_datacenters()
    settings = {"background": None, "bg_opacity": 0.45}
    sp = _dc_map_settings_path()
    if sp.exists():
        try:
            settings.update(json.loads(sp.read_text(encoding="utf-8")))
        except Exception:
            pass
    nodes = []
    if not dcs.empty:
        for i, row in dcs.iterrows():
            try:
                did = int(row["datacenter_id"])
            except Exception:
                continue
            tx = row.get("topology_x")
            ty = row.get("topology_y")
            try:
                x = float(tx) if tx is not None and str(tx).strip() != "" else None
            except Exception:
                x = None
            try:
                y = float(ty) if ty is not None and str(ty).strip() != "" else None
            except Exception:
                y = None
            nodes.append({
                "datacenter_id": did,
                "datacenter_name": str(row.get("datacenter_name") or f"DC {did}"),
                "location": str(row.get("location") or ""),
                "x": x,
                "y": y,
            })
    return {
        "background": settings.get("background"),
        "bg_opacity": float(settings.get("bg_opacity") or 0.45),
        "nodes": nodes,
        "links": links.fillna("").to_dict("records") if links is not None and not links.empty else [],
    }


@app.post("/api/datacenters/map/background")
async def upload_dc_map_bg(
    file: UploadFile = File(...),
    sess: dict = Depends(require_edit),
):
    """آپلود تصویر پس‌زمینه نقشه (مثلاً نقشه شهر)."""
    import json
    content_type = (file.content_type or "").lower()
    allowed = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/pjpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    orig = (file.filename or "").lower()
    ext = allowed.get(content_type)
    if not ext:
        if orig.endswith((".jpg", ".jpeg")):
            ext = ".jpg"
        elif orig.endswith(".png"):
            ext = ".png"
        elif orig.endswith(".webp"):
            ext = ".webp"
    if not ext:
        raise HTTPException(400, "فرمت مجاز: JPG, PNG, WEBP")

    maps_dir = _dc_map_dir()
    # clean old bg files
    for p in maps_dir.glob("dc_map_bg.*"):
        try:
            p.unlink()
        except Exception:
            pass
    save_path = maps_dir / f"dc_map_bg{ext}"
    try:
        with save_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception as e:
        raise HTTPException(500, f"ذخیره ناموفق: {e}")

    url = f"/assets/maps/{save_path.name}"
    sp = _dc_map_settings_path()
    settings = {}
    if sp.exists():
        try:
            settings = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    settings["background"] = url
    sp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "url": url}


@app.post("/api/datacenters/map/opacity")
def set_dc_map_opacity(body: dict, sess: dict = Depends(require_edit)):
    import json
    op = float(body.get("bg_opacity", 0.45))
    op = max(0.0, min(1.0, op))
    sp = _dc_map_settings_path()
    settings = {}
    if sp.exists():
        try:
            settings = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    settings["bg_opacity"] = op
    sp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "bg_opacity": op}


@app.delete("/api/datacenters/map/background")
def clear_dc_map_bg(sess: dict = Depends(require_edit)):
    import json
    maps_dir = _dc_map_dir()
    for p in maps_dir.glob("dc_map_bg.*"):
        try:
            p.unlink()
        except Exception:
            pass
    sp = _dc_map_settings_path()
    settings = {}
    if sp.exists():
        try:
            settings = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    settings["background"] = None
    sp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


# ---------- storage / database ----------
class StorageSettingsIn(BaseModel):
    mode: str = "csv"  # csv | postgres
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = ""
    database: str = "datacenter-monitoring"
    migrate: bool = True


@app.get("/api/storage/settings")
def get_storage_settings(sess: dict = Depends(require_user)):
    from core import db_backend
    s = db_backend.load_settings()
    # never echo full password to non-admin? still mask partially
    pw = s.get("password") or ""
    return {
        **{k: s.get(k) for k in ("mode", "host", "port", "user", "database", "connected", "last_error", "last_ok")},
        "has_password": bool(pw),
        "password_hint": ("••••" + pw[-2:]) if len(pw) > 2 else ("••••" if pw else ""),
    }


@app.post("/api/storage/settings")
def post_storage_settings(body: StorageSettingsIn, sess: dict = Depends(require_admin)):
    from core import db_backend
    s = db_backend.load_settings()
    if body.mode == "csv":
        return db_backend.switch_to_csv()
    # postgres
    s.update({
        "host": body.host,
        "port": body.port,
        "user": body.user,
        "database": body.database or "datacenter-monitoring",
    })
    if body.password:
        s["password"] = body.password
    try:
        result = db_backend.test_and_setup(s, migrate_from_csv=body.migrate)
        return result
    except Exception as e:
        s["connected"] = False
        s["mode"] = "csv"
        s["last_error"] = str(e)
        s["last_ok"] = ""
        db_backend.save_settings(s)
        raise HTTPException(400, f"اتصال ناموفق: {e}")


@app.post("/api/storage/test")
def test_storage(body: StorageSettingsIn, sess: dict = Depends(require_admin)):
    """Test connection without switching mode permanently on failure."""
    from core import db_backend
    s = db_backend.load_settings()
    s.update({
        "host": body.host,
        "port": body.port,
        "user": body.user,
        "database": body.database or "datacenter-monitoring",
    })
    if body.password:
        s["password"] = body.password
    elif not s.get("password"):
        raise HTTPException(400, "رمز عبور را وارد کنید")
    try:
        result = db_backend.test_and_setup(s, migrate_from_csv=body.migrate)
        return {"ok": True, **{k: result.get(k) for k in ("mode", "connected", "database", "last_ok")}}
    except Exception as e:
        raise HTTPException(400, f"اتصال ناموفق: {e}")


# ---------- monitoring ----------
@app.get("/api/monitoring/devices")
def mon_devices(sess: dict = Depends(require_user)):
    _, devices, _ = scoped_inventory(sess)
    mc.sync_inventory_devices(devices)
    mon = mc.list_monitoring_devices()
    snaps = mc.build_snapshot_table(mon)
    if snaps.empty:
        return []
    merged = mon.merge(
        snaps[
            [
                "monitoring_device_id",
                "status",
                "cpu_percent",
                "memory_percent",
                "temperature_c",
                "uptime_days",
                "source",
                "observed_at",
            ]
        ],
        on="monitoring_device_id",
        how="left",
    )
    return merged.fillna("").to_dict("records")


@app.get("/api/monitoring/devices/{device_id}")
def mon_device_detail(device_id: str, sess: dict = Depends(require_user)):
    mon = mc.list_monitoring_devices()
    row = mon[mon["monitoring_device_id"] == device_id]
    if row.empty:
        raise HTTPException(404, "Device not found")
    device = row.iloc[0].to_dict()
    snap = mc.build_device_snapshot(device)
    ifaces = mc.build_interfaces(device)
    history = mc.build_metric_history(device, "CPU", hours=12, points=72)
    return {
        "device": device,
        "snapshot": snap,
        "interfaces": ifaces.to_dict("records"),
        "cpu_history": history.to_dict("records"),
    }


@app.post("/api/monitoring/devices")
def mon_add_device(body: MonDeviceIn, sess: dict = Depends(require_edit)):
    try:
        did = mc.add_monitoring_device(body.model_dump())
        return {"ok": True, "id": did}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/monitoring/performance")
def mon_perf(device_id: str, metric: str = "CPU", hours: int = 24, sess: dict = Depends(require_user)):
    mon = mc.list_monitoring_devices()
    row = mon[mon["monitoring_device_id"] == device_id]
    if row.empty:
        raise HTTPException(404, "Device not found")
    hist = mc.build_metric_history(row.iloc[0].to_dict(), metric, hours=hours, points=96)
    return hist.to_dict("records")


@app.get("/api/monitoring/alerts")
def mon_alerts(sess: dict = Depends(require_user)):
    _, devices, _ = scoped_inventory(sess)
    mc.sync_inventory_devices(devices)
    snaps = mc.build_snapshot_table()
    if not snaps.empty:
        mc.sync_alerts(snaps)
    alerts = mc.load_table("alerts")
    return alerts.fillna("").to_dict("records")


@app.post("/api/monitoring/alerts/ack")
def ack_alert(body: AckIn, sess: dict = Depends(require_edit)):
    try:
        mc.acknowledge_alert(body.id, sess["user"])
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/monitoring/traps")
def mon_traps(sess: dict = Depends(require_user)):
    return mc.load_table("traps").fillna("").to_dict("records")


@app.post("/api/monitoring/traps/test")
def test_trap(device_id: str, message: str = "linkDown test", sess: dict = Depends(require_edit)):
    mon = mc.list_monitoring_devices()
    row = mon[mon["monitoring_device_id"] == device_id]
    if row.empty:
        raise HTTPException(404, "Device not found")
    tid = mc.record_trap(
        {
            "monitoring_device_id": device_id,
            "device_name": row.iloc[0]["display_name"],
            "source_address": row.iloc[0].get("host") or "127.0.0.1",
            "severity": "WARNING",
            "message": message,
        }
    )
    return {"ok": True, "id": tid}


@app.post("/api/monitoring/traps/ack")
def ack_trap(body: AckIn, sess: dict = Depends(require_edit)):
    try:
        mc.acknowledge_trap(body.id, sess["user"])
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/monitoring/credentials")
def mon_creds(sess: dict = Depends(require_user)):
    return mc.load_table("credentials").fillna("").to_dict("records")


@app.get("/api/monitoring/templates")
def mon_tpl(sess: dict = Depends(require_user)):
    return mc.load_table("templates").fillna("").to_dict("records")


@app.get("/api/monitoring/settings")
def mon_settings(sess: dict = Depends(require_user)):
    return {"settings": mc.load_settings(), "capabilities": mc.snmp_capabilities()}


@app.post("/api/discovery/preview")
def disc_preview(cidr: str = "192.168.1.0/24", sess: dict = Depends(require_edit)):
    try:
        df = mc.preview_discovery(cidr)
        return df.to_dict("records")
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/discovery/icmp")
def disc_icmp(cidr: str, sess: dict = Depends(require_edit)):
    try:
        df = mc.discover_icmp(cidr, max_hosts=64)
        return df.to_dict("records")
    except Exception as e:
        raise HTTPException(400, str(e))


# ---------- admin users ----------
@app.get("/api/admin/users")
def admin_users(sess: dict = Depends(require_user)):
    if not (auth.is_admin(sess) or sess.get("setup_mode") or auth.needs_setup()):
        raise HTTPException(403, "فقط ادمین")
    users = auth.load_users()
    if "password_hash" in users.columns:
        users = users.drop(columns=["password_hash"])
    return users.fillna("").to_dict("records")


@app.post("/api/admin/users")
def admin_add_user(
    username: str,
    password: str,
    role: str = "read_only",
    allowed_dcs: str = "all",
    sess: dict = Depends(require_user),
):
    # در حالت setup اجازه ساخت اولین ادمین؛ در غیر این صورت فقط admin
    if auth.needs_setup():
        if not sess.get("setup_mode") and not auth.is_admin(sess):
            raise HTTPException(403, "فقط در حالت راه‌اندازی یا با نقش ادمین")
        # اولین کاربر باید admin باشد
        role = "admin"
    elif not auth.is_admin(sess):
        raise HTTPException(403, "فقط ادمین")
    users = auth.load_users()
    if not users.empty and users["username"].astype(str).eq(username.strip()).any():
        raise HTTPException(400, "Username exists")
    if len(password) < 4:
        raise HTTPException(400, "Password too short")
    row = {
        "username": username.strip(),
        "password_hash": auth.hash_password(password),
        "role": role,
        "allowed_dcs": allowed_dcs,
        "is_active": 1,
    }
    users = pd.concat([users, pd.DataFrame([row])], ignore_index=True)
    users.to_csv(auth.USERS_FILE, index=False)
    return {"ok": True, "setup_complete": not auth.needs_setup()}


@app.delete("/api/admin/users/{username}")
def admin_delete_user(username: str, sess: dict = Depends(require_admin)):
    username = (username or "").strip()
    if not username:
        raise HTTPException(400, "Username required")
    if username == sess.get("user"):
        raise HTTPException(400, "Cannot delete your own account")
    users = auth.load_users()
    mask = users["username"].astype(str) == username
    if not mask.any():
        raise HTTPException(404, "User not found")
    # prevent removing the last admin
    target = users.loc[mask].iloc[0]
    if str(target.get("role", "")).lower() == "admin":
        other_admins = users[
            (~mask)
            & (users["role"].astype(str).str.lower() == "admin")
            & (users["is_active"].astype(str).isin(["1", "True", "true"]))
        ]
        if other_admins.empty:
            raise HTTPException(400, "Cannot delete the last admin")
    users = users[~mask]
    users.to_csv(auth.USERS_FILE, index=False)
    return {"ok": True, "deleted": username}



# ---------- topology / connections ----------
class ConnectionIn(BaseModel):
    source_rack_id: int
    source_device_id: int
    source_port: str
    destination_rack_id: int
    destination_device_id: int
    destination_port: str
    connection_type: str = "network"
    status: str = "active"
    notes: str = ""


@app.get("/api/topology/debug")
def topology_debug():
    import os
    from core.connections import CONNECTIONS_FILE, load_connections, build_elevation
    df = load_connections()
    elev = build_elevation()
    return {
        "cwd": os.getcwd(),
        "connections_file": str(CONNECTIONS_FILE.resolve()),
        "connections_exists": CONNECTIONS_FILE.exists(),
        "connections_rows": int(len(df)),
        "columns": list(df.columns.astype(str)) if not df.empty else [],
        "sample": df.head(3).fillna("").astype(str).to_dict("records") if not df.empty else [],
        "elevation_meta": elev.get("meta"),
        "elevation_racks": elev.get("racks"),
        "elevation_device_count": len(elev.get("devices") or []),
        "elevation_devices_sample": (elev.get("devices") or [])[:5],
    }


@app.get("/api/topology/datacenters")
def topology_datacenters(sess: dict = Depends(require_user)):
    """لیست دیتاسنترها بر اساس رک‌های موجود در connections.csv."""
    elev = connections.build_elevation()
    # group racks by dc
    from collections import Counter
    c = Counter()
    names = {}
    for r in elev.get("racks") or []:
        dc = r.get("datacenter_id")
        if dc is None:
            continue
        c[int(dc)] += 1
        names[int(dc)] = r.get("datacenter_name") or f"DC {dc}"
    # also include known DCs with 0
    dcs, _ = inventory.load_datacenters()
    rows = []
    seen = set()
    if dcs is not None and not dcs.empty:
        for _, r in dcs.iterrows():
            try:
                did = int(r["datacenter_id"])
            except Exception:
                continue
            seen.add(did)
            rows.append({
                "datacenter_id": did,
                "datacenter_name": str(r.get("datacenter_name") or names.get(did) or f"DC {did}"),
                "location": str(r.get("location") or ""),
                "rack_count": int(c.get(did, 0)),
            })
    for did, cnt in c.items():
        if did not in seen:
            rows.append({
                "datacenter_id": did,
                "datacenter_name": names.get(did, f"DC {did}"),
                "location": "",
                "rack_count": int(cnt),
            })
    rows.sort(key=lambda x: x["datacenter_id"])
    return {"datacenters": rows}


@app.get("/api/topology")
def topology_graph(
    datacenter_id: Optional[int] = None,
    mode: str = "racks",
    rack_id: Optional[int] = None,
    sess: dict = Depends(require_user),
):
    mode = (mode or "elevation").lower()
    if mode in ("elevation", "racks", ""):
        graph = connections.build_elevation(datacenter_id=datacenter_id)
    elif mode == "rack" and rack_id is not None:
        graph = connections.build_rack_detail(int(rack_id))
    elif mode == "devices":
        graph = connections.build_topology_graph(datacenter_id=datacenter_id)
        graph["mode"] = "devices"
    else:
        graph = connections.build_elevation(datacenter_id=datacenter_id)
    base_meta = dict(graph.get("meta") or {})
    base_meta.update({
        "connections_file": str(connections.CONNECTIONS_FILE),
        "connections_exists": connections.CONNECTIONS_FILE.exists(),
        "node_count": len(graph.get("nodes") or graph.get("devices") or []),
        "edge_count": len(graph.get("edges") or graph.get("links") or []),
        "rack_count": len(graph.get("racks") or []),
        "datacenter_id": datacenter_id,
        "mode": graph.get("mode") or mode,
        "engine": "elevation-v3",
    })
    graph["meta"] = base_meta
    return graph


@app.get("/api/connections")
def list_connections(sess: dict = Depends(require_user)):
    df = connections.load_connections()
    return df.fillna("").to_dict("records")


@app.post("/api/connections")
def add_connection(body: ConnectionIn, sess: dict = Depends(require_edit)):
    ok, msg, row = connections.create_connection(**body.model_dump())
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg, "connection": row}


@app.delete("/api/connections/{connection_id}")
def remove_connection(connection_id: int, sess: dict = Depends(require_edit)):
    if not connections.delete_connection(connection_id):
        raise HTTPException(404, "Not found")
    return {"ok": True}


@app.get("/api/devices/{device_id}/ports")
def device_ports(device_id: int, sess: dict = Depends(require_user)):
    return connections.device_port_map(device_id)


class PlacementMoveIn(BaseModel):
    rack_id: int
    device_id: int
    start_unit: int


@app.post("/api/placements/move")
def move_placement(body: PlacementMoveIn, sess: dict = Depends(require_edit)):
    """Drag-drop: remove existing placement of device then place at new unit."""
    racks, devices, placements = inventory.load_inventory()
    # remove any existing placement for this device
    if not placements.empty:
        placements = placements[placements["device_id"].astype(int) != int(body.device_id)].copy()
    placements, ok, msg = inventory.place_device(
        racks, devices, placements, body.rack_id, body.device_id, body.start_unit
    )
    if not ok:
        raise HTTPException(400, msg)
    inventory.save_inventory(racks, devices, placements)
    return {"ok": True, "message": msg}


# ---------- BI / Superset ----------
@app.get("/api/bi/settings")
def bi_settings(sess: dict = Depends(require_user)):
    import os
    return {
        "superset_url": os.environ.get("SUPERSET_URL", "http://localhost:8088"),
        "hint": "Set SUPERSET_URL env or use docker-compose.superset.yml",
    }


# ---------- Bale Bot ----------
class BaleSettingsIn(BaseModel):
    bot_token: Optional[str] = None
    default_chat_id: Optional[str] = None
    enabled: Optional[bool] = None
    auto_alerts: Optional[bool] = None
    notify_service_stop: Optional[bool] = None
    polling: Optional[bool] = None
    mini_app_url: Optional[str] = None


class BaleTestIn(BaseModel):
    text: str = "🧪 پیام تست از DC Monitor Pro"


@app.get("/api/bale/settings")
def bale_get_settings(sess: dict = Depends(require_admin)):
    return bale.public_settings()


@app.post("/api/bale/settings")
def bale_save_settings(body: BaleSettingsIn, sess: dict = Depends(require_admin)):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    # empty string token means "don't change"
    if "bot_token" in patch and not str(patch["bot_token"]).strip():
        patch.pop("bot_token")
    saved = bale.save_settings(patch)
    # if mini_app_url set and token exists, push menu button
    token = saved.get("bot_token") or ""
    mini = (saved.get("mini_app_url") or "").strip()
    menu_result = None
    if token and mini:
        menu_result = {"ok": True, "disabled": True}
    return {
        "ok": True,
        "settings": bale.public_settings(),
        "menu_button": menu_result,
    }


@app.post("/api/bale/check-token")
def bale_check_token(sess: dict = Depends(require_admin)):
    s = bale.load_settings()
    token = s.get("bot_token") or ""
    return bale.check_token(token)


@app.post("/api/bale/start")
def bale_start(sess: dict = Depends(require_admin)):
    return bale.start_runner()


@app.post("/api/bale/stop")
def bale_stop(sess: dict = Depends(require_admin)):
    return bale.stop_runner()


@app.post("/api/bale/test-message")
def bale_test_message(body: BaleTestIn, sess: dict = Depends(require_admin)):
    s = bale.load_settings()
    token = s.get("bot_token") or ""
    chat_id = s.get("default_chat_id") or ""
    if not token:
        raise HTTPException(400, "توکن تنظیم نشده")
    if not chat_id:
        raise HTTPException(400, "Chat ID تنظیم نشده")
    res = bale.send_message(token, chat_id, body.text)
    if not res.get("ok"):
        raise HTTPException(400, res.get("description", "ارسال ناموفق"))
    return {"ok": True, "result": res.get("result")}


@app.post("/api/bale/send-alerts-now")
def bale_send_alerts_now(sess: dict = Depends(require_admin)):
    s = bale.load_settings()
    token = s.get("bot_token") or ""
    chat_id = s.get("default_chat_id") or ""
    if not token or not chat_id:
        raise HTTPException(400, "توکن یا Chat ID تنظیم نشده")
    alerts = mc.load_table("alerts")
    if alerts.empty:
        text = "✅ هیچ آلرت فعالی نیست."
    else:
        open_a = alerts[alerts["status"].isin(["OPEN", "ACKNOWLEDGED"])]
        lines = [f"🚨 <b>آلرت‌های فعال</b> ({len(open_a)})\n"]
        for _, a in open_a.head(15).iterrows():
            lines.append(
                f"• <b>{a.get('severity')}</b> [{a.get('status')}]\n"
                f"  {a.get('device_id')} · {a.get('metric')}={a.get('value')}"
            )
        if open_a.empty:
            lines = ["✅ آلرت فعالی نیست."]
        text = "\n".join(lines)
    res = bale.send_message(token, chat_id, text)
    if not res.get("ok"):
        raise HTTPException(400, res.get("description", "ارسال ناموفق"))
    return {"ok": True, "sent": True}


@app.get("/api/bale/status")
def bale_status(sess: dict = Depends(require_user)):
    return bale.public_settings()



# ---------- AI Assistant (Ollama + Tools) ----------
class AIChatIn(BaseModel):
    message: str
    history: Optional[list] = None


@app.post("/api/ai/chat")
def ai_chat(body: AIChatIn, sess: dict = Depends(require_user)):
    """چت هوشمند با دسترسی به داده‌های رک، دیوایس و اتصالات"""
    if not body.message or not body.message.strip():
        raise HTTPException(400, "پیام خالی است")
    result = llm_svc.get_llm().chat(body.message.strip(), history=body.history or [])
    return result


@app.get("/api/ai/health")
def ai_health(sess: dict = Depends(require_user)):
    """بررسی واقعی در دسترس بودن Ollama / مدل LLM"""
    model = llm_svc.DEFAULT_MODEL
    try:
        import ollama
        models = ollama.list()
        names = []
        if hasattr(models, "models"):
            names = [getattr(m, "model", None) or getattr(m, "name", "") for m in models.models]
        elif isinstance(models, dict):
            names = [m.get("name") or m.get("model", "") for m in models.get("models", [])]
        available = any(model in (n or "") for n in names)
        try:
            ollama.chat(model=model, messages=[{"role": "user", "content": "ping"}], options={"num_predict": 1})
            reachable = True
        except Exception:
            reachable = available
        return {
            "status": "ok" if (available or reachable) else "unavailable",
            "available": bool(available or reachable),
            "model": model,
            "models_found": [n for n in names if n][:12],
        }
    except Exception as e:
        return {
            "status": "unavailable",
            "available": False,
            "model": model,
            "error": str(e),
        }


@app.get("/api/guided/device-connections/{device_id}")
def guided_device_connections(device_id: int, sess: dict = Depends(require_user)):
    """خلاصه اتصالات یک دیوایس برای حالت راهنمای دستی (بدون LLM)"""
    from core.llm_tools import get_device_connections
    return get_device_connections(str(device_id))


@app.get("/api/guided/rack-devices/{rack_id}")
def guided_rack_devices(rack_id: int, sess: dict = Depends(require_user)):
    """دیوایس‌های یک رک برای حالت راهنما"""
    from core.llm_tools import get_rack_devices
    return get_rack_devices(str(rack_id))


# ---------- static / mini-app ----------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/ai")
def ai_page():
    """صفحه چت دستیار هوشمند"""
    return FileResponse(STATIC / "ai.html")


@app.get("/mini")
@app.get("/miniapp")
def mini_app():
    """Bale Mini App entry — same SPA, mobile-friendly."""
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
ASSETS = Path(__file__).parent / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)
(ASSETS / "racks").mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=str(ASSETS)), name="assets")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8600, reload=True)
